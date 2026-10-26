# PLAN — Site Monitor (Uptime Checker)

**Versión:** 1.0  
**Fecha:** 2026-10-26  
**Autor:** Hermes Agent  
**Ruta:** `/home/ubuntu/www/site-monitor/`

---

## 1. Resumen Ejecutivo

Sistema ligero de monitoreo de uptime para sitios web. Lee un archivo de configuración YAML, verifica cada URL mediante HTTP(S), registra resultados en SQLite y logs rotativos, y genera un resumen en terminal. Se ejecuta vía cron de Hermes con frecuencia configurable.

**Alcance incluido:**
- Verificación HTTP/HTTPS con timeout configurable.
- Validación de status code y contenido esperado (opcional).
- Logs diarios y base de datos de estado para historial.
- Detección de sitios caídos y alerta por fallos consecutivos.
- Resumen visual en terminal tras cada ejecución.

**Alcance excluido:**
- Notificaciones por email, Slack, Telegram, etc. (extensión futura).
- Dashboard web (se puede construir sobre la BD SQLite).
- Métricas avanzadas (percentiles, gráficas).

---

## 2. Stack Tecnológico

| Componente | Tecnología | Justificación |
|------------|-----------|---------------|
| Lenguaje | Python 3 | Disponible en el sistema, librería estándar potente. |
| HTTP client | `requests` | Robusto, maneja timeouts, redirects y excepciones. |
| Configuración | YAML (`PyYAML`) | Legible para humanos, fácil de editar sin tocar código. |
| Estado / Historial | SQLite 3 | Sin servidor, portable, consultable vía SQL. |
| Scheduler | System crontab (`crontab`) | Persistente, no depende de Hermes activo, gestión estándar Unix. |
| Logs | Rotación por fecha (`logging`) | Un archivo por día, limpieza automática de antiguos. |

---

## 3. Arquitectura General

```
┌─────────────────────────────────────────────────────┐
│         Hermes Cron (scheduler)           │
└─────────────────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────┐
│              monitor.py                    │
│  ────────────────────────────────────────────────────  │
│  1. Lee config.yaml                        │
│  2. Por cada sitio habilitado:             │
│       —— Hace GET                         │
│       —— Valida status/content            │
│       —— Actualiza contador de fallos     │
│       —— Guarda resultado en SQLite       │
│  3. Imprime resumen en terminal            │
│  4. Rota logs antiguos                     │
└─────────────────────────────────────────────────────┘
                     │
        ┌───────────┼───────────┐
        ▼            ▼            ▼
┌───────────┐ ┌───────────┐ ┌───────────┐
│ config.yaml │ │ state.db    │ │ logs/       │
│ (URLs/n)    │ │ (SQLite)    │ │ (rotativos) │
└───────────┘ └───────────┘ └───────────┘
```

---

## 4. Modelo de Datos (SQLite)

**Tabla `site_checks`** — registro de cada ejecución:

| Campo | Tipo | Descripción |
|-------|------|-------------|
| id | INTEGER PK | Autoincremental |
| name | TEXT | Nombre descriptivo del sitio |
| url | TEXT | URL verificada |
| checked_at | TEXT (ISO) | Timestamp de la verificación |
| status_code | INTEGER | HTTP status recibido |
| response_time_ms | REAL | Tiempo de respuesta en milisegundos |
| success | INTEGER (0/1) | ¿Pasó todas las validaciones? |
| error | TEXT | Mensaje de error, si aplica |
| alert_triggered | INTEGER (0/1) | ¿Se activó alerta por fallos consecutivos? |

**Tabla `failure_counts`** — contador para evitar alertas por glitch temporal:

| Campo | Tipo | Descripción |
|-------|------|-------------|
| name | TEXT PK | Nombre del sitio |
| consecutive_failures | INTEGER | Fallos seguidos acumulados |
| last_failure_at | TEXT (ISO) | Último fallo registrado |

---

## 5. Plan de Ejecución (Phases)

| Phase | Objetivo | Entregable |
|-------|----------|------------|
| **Tracer Bullet** | Script básico que corre una vez y genera output visible | `monitor.py` ejecutable, resumen en consola |
| **GREEN** | Persistencia (SQLite + logs), configuración externa YAML | `config.yaml`, `state.db`, logs diarios |
| **REFACTOR** | Manejo de errores robusto, alertas por fallos consecutivos, limpieza de logs | Alertas configurables, retención de logs |
| **INTEGRATE** | Scheduling automático vía crontab del sistema | `*/5 * * * *` para monitor, `0 3 1 * *` para cleanup |
| **NOTIFY** | Alertas a Slack vía Incoming Webhook | `SlackNotifier` integrado en `monitor.py`, probado con URL falsa |
| **DEPLOY** | Validación end-to-end y documentación | PLAN + README + pruebas manuales OK |

---

## 6. Seguridad

- [x] No hay credenciales hardcodeadas; toda la config está en `config.yaml`.
- [x] No se ejecuta código externo ni se parsea HTML con eval.
- [x] `requests` se usa solo en modo GET; no hay modificación de estado en los sitios monitoreados.
- [x] Base de datos SQLite local, sin exposición de red.
- [x] Los logs no capturan el body completo de la respuesta (solo status, tiempo y error descriptivo).

---

## 7. Testing Strategy

- **Manual:** Ejecutar `python3 monitor.py` y verificar resumen visual.
- **Manual negativo:** Agregar una URL inexistente (ej. `https://fake-site-not-exists-12345.local`) y confirmar que:
  - Se registra el fallo.
  - El contador de fallos consecutivos aumenta.
  - Tras N fallos aparece `[ALERTA]` en el log.
- **CI-ready:** El script retorna exit code `1` si algún sitio falla, útil para pipelines.
- **Validación DEPLOY realizada (2026-10-26):**
  - 12 sitios configurados; todos retornan HTTP 200 en ejecución manual.
  - Alerta por fallos consecutivos verificada con URL falsa (threshold=2).
  - Alertas a Slack verificadas: mensaje de alerta y recuperación recibidos en canal.
  - Cleanup mensual verificado: `cleanup.py` eliminó 6 registros huérfanos de `failure_counts`.
  - Rotación de logs confirmada: archivo de +30 días eliminado automáticamente.
  - Crontab del sistema configurado: `*/5 * * * *` para monitor, `0 3 1 * *` para cleanup.

---

## 8. Riesgos y Mitigaciones

| Riesgo | Mitigación |
|--------|-----------|
| Falso positivo por glitch de red | `consecutive_failures` configurable (default 2). |
| Crecimiento descontrolado de logs | Rotación por fecha + limpieza automática de antiguos vía `LogManager`. |
| Crecimiento descontrolado de SQLite | `cleanup.py` mensual elimina registros `site_checks` mayores a `log_retention_days`. |
| Hermes Cron no disponible | Se usa `crontab` del sistema directamente (persistente, no se elimina solo). |
| Dependencia no instalada (`requests`, `pyyaml`) | Se asumen disponibles; si no, `pip3 install requests pyyaml`. |

---

## 9. Auto-evaluación / Métricas

- ¿Cuántos sitios se monitorean? → Contar filas en `site_checks` por día.
- ¿Cuál es el uptime %? → `SUM(success) / COUNT(*)` por sitio y rango de fechas.
- ¿Cuántas alertas se dispararon? → `SUM(alert_triggered)` en `site_checks`.
- Tiempo medio de respuesta → `AVG(response_time_ms)` por sitio.

---

## 10. Uso Rápido

```bash
# 1. Ir al directorio
cd /home/ubuntu/www/site-monitor

# 2. Editar URLs / frecuencia
nano config.yaml

# 3. Ejecutar manualmente
python3 monitor.py

# 4. Usar configuración alternativa
python3 monitor.py --config /ruta/a/otro-config.yaml

# 5. Ver logs
cat logs/monitor-$(date +%Y-%m-%d).log

# 6. Consultar historial en SQLite
sqlite3 state.db "SELECT name, checked_at, success, error FROM site_checks ORDER BY id DESC LIMIT 20;"
```

---

## 11. Configuración del Cron (crontab del sistema)

El monitor usa crontab del usuario `ubuntu`, no Hermes Cron:

```bash
# Ver jobs actuales
crontab -l

# Editar
crontab -e
```

Jobs configurados:

| Job | Schedule | Comando |
|-----|----------|---------|
| Monitor | `*/5 * * * *` | `cd /home/ubuntu/www/site-monitor && /usr/bin/python3 monitor.py --quiet >> logs/cron.log 2>&1` |
| Cleanup | `0 3 1 * *` | `cd /home/ubuntu/www/site-monitor && /usr/bin/python3 cleanup.py >> logs/cleanup-cron.log 2>&1` |

Los crons del sistema son persistentes (no se eliminan solos). Se gestionan con `crontab -l`, `crontab -e`, o `crontab -r`.

## 12. Notificaciones (Slack)

Configuración en `config.yaml`:
```yaml
slack_webhook_url: "https://hooks.slack.com/services/T.../B.../..."
```

El monitor envía dos tipos de mensajes:
- **Alerta** (`🚨`) cuando un sitio alcanza `alert_threshold` fallos consecutivos.
- **Recuperación** (`✅`) cuando un sitio vuelve a responder OK después de estar en alerta.

Para probar:
1. Agregar un sitio con URL falsa al `config.yaml`.
2. Correr el monitor `alert_threshold` veces.
3. Verificar que el mensaje llega al canal de Slack.
4. Quitar el sitio de prueba.

---

## 12. Archivos Entregados

| Ruta | Propósito |
|------|-----------|
| `/home/ubuntu/www/site-monitor/monitor.py` | Script principal de monitoreo |
| `/home/ubuntu/www/site-monitor/cleanup.py` | Limpieza mensual de SQLite y logs antiguos |
| `/home/ubuntu/www/site-monitor/config.yaml` | URLs, frecuencia, umbrales y webhook de Slack |
| `/home/ubuntu/www/site-monitor/state.db` | Base de datos SQLite (creada en ejecución) |
| `/home/ubuntu/www/site-monitor/logs/` | Logs diarios y de cleanup (creado en ejecución) |
| `/home/ubuntu/www/site-monitor/PLAN_site-monitor_v1.md` | Este documento |
| `/home/ubuntu/www/site-monitor/README.md` | Documentación de instalación, uso y troubleshooting |

---

## 13. Desviaciones del Plan Original

| Ítem | Plan original | Implementación final | Justificación |
|-------|---------------|----------------------|---------------|
| CLI args | `--quiet` | `--quiet`, `--config` | Facilitar pruebas con configs alternativas sin sobreescribir el archivo principal. |
| Scheduler | Hermes Cron | `crontab` del sistema | Más simple, persistente, no depende de Hermes Cron activo. |
| Frecuencia cron | Cada hora | Cada 5 minutos | Mayor granularidad para detectar caídas rápido. |
| Notificaciones | Excluido del alcance | Slack Incoming Webhook | Agregado tras aprobación del usuario; probado con URL falsa. |
| Cleanup mensual | No previsto | `cleanup.py` + cron mensual | Evita crecimiento infinito de SQLite (103k+ filas/mes a cada 5 min). |
| Wrapper cron | No previsto explícito | `~/.hermes/scripts/site-monitor.sh` (obsoleto) | Ahora se usa `crontab` directamente; el wrapper ya no es necesario. |
| Sites de ejemplo | Genéricos | 12 sitios reales validados | Se eligieron URLs que no bloquean requests automatizados (evitan 403). |
