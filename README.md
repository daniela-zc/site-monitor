# Site Monitor

Monitor de sitios web con persistencia SQLite, logs diarios, rotación automática y alertas por fallos consecutivos.

## Características

- **Monitoreo HTTP/HTTPS** de múltiples sitios con timeout configurable.
- **Persistencia SQLite** para historial de checks y contadores de fallos.
- **Logs diarios** con rotación y limpieza automática de archivos antiguos.
- **Alertas por fallos consecutivos** para evitar ruido por glitch temporal.
- **Resumen visual** en consola tras cada ejecución.
- **Scheduling** vía Hermes Cron (o cron del sistema).
- **Exit code** útil para CI: retorna `1` si algún sitio falla, `0` si todos pasan.

---

## Requisitos

- Python 3.8+
- `requests`
- `pyyaml`

Instalación de dependencias:

```bash
pip3 install requests pyyaml
```

---

## Instalación

1. Clonar o copiar el directorio:

```bash
cd /home/ubuntu/www/site-monitor
```

2. Verificar dependencias:

```bash
python3 -c "import requests, yaml; print('OK')"
```

3. Revisar `config.yaml` y ajustar sitios según necesidad.

4. Ejecutar manualmente para validar:

```bash
python3 monitor.py
```

---

## Uso

### Ejecución manual

```bash
# Modo interactivo con resumen en consola
python3 monitor.py

# Modo silencioso (solo logs y SQLite)
python3 monitor.py --quiet

# Usar un archivo de configuración alternativo
python3 monitor.py --config /ruta/a/otro-config.yaml
```

### Scheduling automático (crontab del sistema)

El monitor corre vía crontab del usuario `ubuntu`:

- **Monitor:** cada 5 minutos (`*/5 * * * *`)
- **Cleanup mensual:** día 1 de cada mes a las 03:00 (`0 3 1 * *`)

Verificar:
```bash
crontab -l
```

Modificar:
```bash
crontab -e
```

Los crons son persistentes (quedan hasta que alguien los borre con `crontab -r`).

### Cleanup mensual automático

Cada mes, `cleanup.py` elimina:
- Registros de `site_checks` en SQLite mayores a `log_retention_days` (default 30).
- Registros huérfanos de `failure_counts` para sitios que ya no están en `config.yaml`.
- Archivos de log `monitor-*.log` y `cleanup-*.log` más antiguos que el umbral.

Ejecutar manualmente:
```bash
python3 cleanup.py
```

Log del cleanup: `logs/cleanup-YYYY-MM-DD.log`.

---

## Configuración (`config.yaml`)

```yaml
timeout: 10                 # Segundos de timeout por solicitud
log_retention_days: 30      # Días de retención de logs y registros SQLite
alert_threshold: 2          # Fallos consecutivos antes de alertar
slack_webhook_url: "https://hooks.slack.com/services/..."  # Opcional

sites:
  - name: Google
    url: https://www.google.com
    enabled: true
  - name: GitHub
    url: https://github.com
    enabled: true
  - name: Disabled Site
    url: https://example.com
    enabled: false
```

| Campo | Descripción |
|-------|-------------|
| `timeout` | Tiempo máximo de espera por check (segundos). |
| `log_retention_days` | Registros SQLite y logs más antiguos que este valor se eliminan automáticamente. |
| `alert_threshold` | Número de fallos consecutivos necesarios para disparar alerta. |
| `slack_webhook_url` | URL del webhook de Slack para alertas (opcional). |
| `sites` | Lista de sitios a monitorear. Cada uno tiene `name`, `url` y `enabled`. |

> **Tip:** Para probar alertas, añade temporalmente un sitio con una URL inexistente (ej. `https://fake.local`) y corre el monitor `alert_threshold` veces.

---

## Estructura de archivos

```
/home/ubuntu/www/site-monitor/
├── monitor.py          # Script principal de monitoreo
├── cleanup.py          # Limpieza mensual de SQLite y logs
├── config.yaml         # Configuración de sitios, umbrales y webhook
├── state.db            # SQLite (creado/actualizado en ejecución)
├── logs/               # Logs diarios y de cleanup (creado en ejecución)
│   ├── monitor-YYYY-MM-DD.log
│   └── cleanup-YYYY-MM-DD.log
├── PLAN_site-monitor_v1.md  # Documento de diseño original
└── README.md           # Este archivo
```

---

## Base de datos SQLite

### Tablas

- **`site_checks`** — registro de cada verificación.
- **`failure_counts`** — contador de fallos consecutivos por sitio.

### Consultas útiles

```bash
# Últimos 20 checks
cd /home/ubuntu/www/site-monitor
python3 - <<'PY'
import sqlite3, tabulate  # opcional, para formato bonito
conn = sqlite3.connect('state.db')
rows = conn.execute('SELECT name, checked_at, success, error FROM site_checks ORDER BY id DESC LIMIT 20').fetchall()
for r in rows:
    print(r)
conn.close()
PY

# Uptime % por sitio (últimas 24h)
python3 - <<'PY'
import sqlite3
conn = sqlite3.connect('state.db')
rows = conn.execute('''
  SELECT name,
         ROUND(100.0 * SUM(success) / COUNT(*), 2) AS uptime_pct,
         COUNT(*) AS checks,
         SUM(alert_triggered) AS alerts
  FROM site_checks
  WHERE checked_at >= datetime('now', '-1 day')
  GROUP BY name
''').fetchall()
for r in rows:
    print(r)
conn.close()
PY
```

---

## Troubleshooting

### El monitor retorna exit code 1

Esto es **esperado** cuando algún sitio falla. El script retorna:
- `0` si todos los sitios habilitados responden correctamente.
- `1` si al menos uno falla.

Esto permite usarlo en pipelines de CI/CD.

### `requests` o `pyyaml` no encontrados

```bash
pip3 install requests pyyaml
```

### El cron job aparece como fallido

El wrapper `site-monitor.sh` siempre retorna `0` para evitar que el scheduler de Hermes Cron marque fallos de sitios como errores del job. Si ves fallos, revisa:

```bash
hermes cron list
cat /home/ubuntu/www/site-monitor/logs/monitor-$(date +%Y-%m-%d).log
```

### Logs no se rotan

La rotación ocurre en cada ejecución. Si un archivo es más antiguo que `log_retention_days`, se elimina. Verifica:

```bash
ls -la /home/ubuntu/www/site-monitor/logs/
```

### Alertas no se disparan

Revisa que `alert_threshold` sea el valor deseado y que el sitio falle ese número de veces **consecutivas**. Un solo fallo seguido de un éxito reinicia el contador.

### 403 Forbidden en algunos sitios

Algunos sitios (Wikipedia, Reddit, Stack Overflow) bloquean solicitudes sin `User-Agent`. Si necesitas monitorearlos, edita `monitor.py` en la clase `SiteChecker` para añadir headers:

```python
resp = requests.get(site.url, timeout=self.timeout, headers={"User-Agent": "site-monitor/1.0"})
```

---

## Validación end-to-end realizada

| Criterio | Estado | Detalle |
|----------|--------|---------|
| Monitor cada 5 minutos vía crontab | ✅ OK | `*/5 * * * *` configurado en crontab del usuario. |
| 12 sitios monitoreados sin errores | ✅ OK | Todos retornan HTTP 200 en ejecución manual. |
| Alertas por fallos consecutivos | ✅ OK | URL falsa dispara alerta tras 2 fallos consecutivos. |
| Alertas a Slack | ✅ OK | Webhook configurado; mensajes de alerta y recuperación recibidos en Slack. |
| Cleanup mensual automático | ✅ OK | `cleanup.py` elimina registros >30 días y huérfanos de `failure_counts`. |
| Rotación de logs | ✅ OK | Archivos más antiguos que `log_retention_days` eliminados automáticamente. |

---

## Licencia

Uso interno. Extensible para notificaciones (email, Slack, etc.) sobre la base SQLite existente.