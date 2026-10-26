#!/usr/bin/env python3
"""Site monitor with SQLite persistence, daily logs, and log rotation."""

import argparse
import logging
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests
import yaml


@dataclass
class SiteConfig:
    name: str
    url: str
    enabled: bool = True


@dataclass
class CheckResult:
    name: str
    url: Optional[str]
    status: Optional[int]
    time_ms: float
    ok: bool
    error: Optional[str]


class LogManager:
    """Handles daily log file creation and retention cleanup."""

    def __init__(self, logs_dir: Path, retention_days: int):
        self.logs_dir = logs_dir
        self.retention_days = retention_days
        self.logger = logging.getLogger()
        self._log_file: Optional[Path] = None

    def setup(self, quiet: bool = False) -> Path:
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        today = datetime.now().strftime("%Y-%m-%d")
        log_file = self.logs_dir / f"monitor-{today}.log"
        self._log_file = log_file

        self.logger.setLevel(logging.INFO)
        # Avoid duplicate handlers when the module is imported multiple times.
        self.logger.handlers.clear()

        formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(formatter)
        self.logger.addHandler(fh)

        if not quiet:
            sh = logging.StreamHandler(sys.stdout)
            sh.setFormatter(formatter)
            self.logger.addHandler(sh)

        return log_file

    def cleanup_old_logs(self) -> int:
        cutoff = datetime.now() - timedelta(days=self.retention_days)
        count = 0
        for f in self.logs_dir.glob("monitor-*.log"):
            try:
                date_str = f.stem.split("monitor-")[1]
                file_date = datetime.strptime(date_str, "%Y-%m-%d")
                if file_date < cutoff:
                    f.unlink()
                    count += 1
            except (ValueError, IndexError):
                continue
        return count


class StateStore:
    """SQLite persistence for site checks and failure counters."""

    def __init__(self, db_path: Path):
        self.db_path = db_path

    def init_schema(self) -> None:
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS site_checks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    url TEXT,
                    checked_at TEXT NOT NULL,
                    status_code INTEGER,
                    response_time_ms REAL,
                    success INTEGER NOT NULL,
                    error TEXT,
                    alert_triggered INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS failure_counts (
                    name TEXT PRIMARY KEY,
                    consecutive_failures INTEGER NOT NULL DEFAULT 0,
                    last_failure_at TEXT
                )
                """
            )
            conn.commit()

    def save_check(self, result: CheckResult, alert_triggered: bool) -> None:
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                """
                INSERT INTO site_checks
                    (name, url, checked_at, status_code, response_time_ms, success, error, alert_triggered)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.name,
                    result.url,
                    datetime.now().isoformat(),
                    result.status,
                    result.time_ms,
                    1 if result.ok else 0,
                    result.error,
                    1 if alert_triggered else 0,
                ),
            )
            conn.commit()

    def update_failure_count(self, name: str, ok: bool, alert_threshold: int) -> bool:
        alert_triggered = False
        with sqlite3.connect(str(self.db_path)) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT consecutive_failures FROM failure_counts WHERE name = ?",
                (name,),
            )
            row = cursor.fetchone()

            if ok:
                if row:
                    cursor.execute(
                        "UPDATE failure_counts SET consecutive_failures = 0, last_failure_at = NULL WHERE name = ?",
                        (name,),
                    )
                    conn.commit()
            else:
                failures = 1
                if row:
                    failures = row[0] + 1
                    cursor.execute(
                        "UPDATE failure_counts SET consecutive_failures = ?, last_failure_at = ? WHERE name = ?",
                        (failures, datetime.now().isoformat(), name),
                    )
                else:
                    cursor.execute(
                        "INSERT INTO failure_counts (name, consecutive_failures, last_failure_at) VALUES (?, ?, ?)",
                        (name, failures, datetime.now().isoformat()),
                    )
                conn.commit()
                if failures >= alert_threshold:
                    alert_triggered = True

        return alert_triggered

    def was_in_alert(self, name: str, alert_threshold: int) -> bool:
        """Return True if the site had reached the alert threshold before this check."""
        with sqlite3.connect(str(self.db_path)) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT consecutive_failures FROM failure_counts WHERE name = ?",
                (name,),
            )
            row = cursor.fetchone()
            return row is not None and row[0] >= alert_threshold


class SlackNotifier:
    """Sends alerts to Slack via Incoming Webhook."""

    def __init__(self, webhook_url: Optional[str]):
        self.webhook_url = webhook_url

    def _post(self, payload: dict) -> bool:
        if not self.webhook_url:
            return False
        try:
            resp = requests.post(self.webhook_url, json=payload, timeout=10)
            return resp.status_code == 200
        except requests.RequestException as exc:
            logging.getLogger().error("Failed to send Slack notification: %s", exc)
            return False

    def send_alert(self, result: CheckResult, threshold: int) -> bool:
        text = (
            f"🚨 *ALERTA: {result.name} esta caido*\n"
            f"• URL: {result.url}\n"
            f"• Error: {result.error or 'Unknown error'}\n"
            f"• Fallos consecutivos: {threshold}\n"
            f"• Hora: {datetime.now().isoformat()}"
        )
        return self._post({"text": text})

    def send_recovery(self, result: CheckResult) -> bool:
        text = (
            f"✅ *RECUPERADO: {result.name}*\n"
            f"• URL: {result.url}\n"
            f"• Status: {result.status}\n"
            f"• Tiempo de respuesta: {result.time_ms:.2f} ms\n"
            f"• Hora: {datetime.now().isoformat()}"
        )
        return self._post({"text": text})


class SiteChecker:
    """Performs HTTP checks with robust exception classification."""

    def __init__(self, timeout: int):
        self.timeout = timeout

    def check(self, site: SiteConfig) -> CheckResult:
        if not site.url:
            return CheckResult(
                name=site.name,
                url=None,
                status=None,
                time_ms=0.0,
                ok=False,
                error="Missing URL",
            )

        start = time.perf_counter()
        try:
            resp = requests.get(site.url, timeout=self.timeout)
            elapsed_ms = (time.perf_counter() - start) * 1000
            ok = resp.status_code < 400
            return CheckResult(
                name=site.name,
                url=site.url,
                status=resp.status_code,
                time_ms=round(elapsed_ms, 2),
                ok=ok,
                error=None,
            )
        except requests.Timeout as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000
            return CheckResult(
                name=site.name,
                url=site.url,
                status=None,
                time_ms=round(elapsed_ms, 2),
                ok=False,
                error=f"Timeout after {self.timeout}s: {exc}",
            )
        except requests.ConnectionError as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000
            return CheckResult(
                name=site.name,
                url=site.url,
                status=None,
                time_ms=round(elapsed_ms, 2),
                ok=False,
                error=f"Connection error (DNS/network): {exc}",
            )
        except requests.TooManyRedirects as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000
            return CheckResult(
                name=site.name,
                url=site.url,
                status=None,
                time_ms=round(elapsed_ms, 2),
                ok=False,
                error=f"Too many redirects: {exc}",
            )
        except requests.SSLError as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000
            return CheckResult(
                name=site.name,
                url=site.url,
                status=None,
                time_ms=round(elapsed_ms, 2),
                ok=False,
                error=f"SSL error: {exc}",
            )
        except requests.RequestException as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000
            return CheckResult(
                name=site.name,
                url=site.url,
                status=None,
                time_ms=round(elapsed_ms, 2),
                ok=False,
                error=f"Request error: {exc}",
            )


class Monitor:
    """Orchestrates configuration, checking, persistence, and logging."""

    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.config_path = base_dir / "config.yaml"
        self.db_path = base_dir / "state.db"
        self.logs_dir = base_dir / "logs"

    def load_config(self) -> dict:
        if not self.config_path.exists():
            raise FileNotFoundError(f"Config file not found: {self.config_path}")
        with self.config_path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    @staticmethod
    def print_summary(results: list[CheckResult]) -> bool:
        print(f"{'Site':<25} {'Status':>8} {'Time (ms)':>10} {'Result':>10}")
        print("-" * 60)
        any_failed = False
        for r in results:
            status = r.status if r.status is not None else "—"
            ok_str = "✅ OK" if r.ok else "❌ FAIL"
            if not r.ok:
                any_failed = True
            print(f"{r.name:<25} {status:>8} {r.time_ms:>10.2f} {ok_str:>10}")
            if r.error:
                print(f"  → {r.error}")
        print("-" * 60)
        total = len(results)
        passed = sum(1 for r in results if r.ok)
        print(f"Total: {total} | Passed: {passed} | Failed: {total - passed}")
        return any_failed

    def run(self, quiet: bool = False) -> int:
        raw_config = self.load_config()
        timeout = raw_config.get("timeout", 10)
        log_retention_days = raw_config.get("log_retention_days", 30)
        alert_threshold = raw_config.get("alert_threshold", 2)
        slack_webhook_url = raw_config.get("slack_webhook_url")
        raw_sites = raw_config.get("sites", [])

        store = StateStore(self.db_path)
        store.init_schema()

        log_mgr = LogManager(self.logs_dir, log_retention_days)
        log_file = log_mgr.setup(quiet=quiet)
        removed = log_mgr.cleanup_old_logs()

        slack = SlackNotifier(slack_webhook_url)

        logger = logging.getLogger()
        if removed:
            logger.info("Cleaned up %d old log file(s)", removed)
        logger.info("Monitor started — %d site(s) configured", len(raw_sites))

        checker = SiteChecker(timeout)
        results: list[CheckResult] = []
        any_failed = False

        for raw_site in raw_sites:
            site = SiteConfig(
                name=raw_site.get("name", "Unnamed"),
                url=raw_site.get("url", ""),
                enabled=raw_site.get("enabled", True),
            )
            if not site.enabled:
                continue

            result = checker.check(site)

            # Check if site was already in alert state *before* updating counter
            was_alerted = store.was_in_alert(result.name, alert_threshold)

            alert_triggered = store.update_failure_count(
                result.name, result.ok, alert_threshold
            )
            store.save_check(result, alert_triggered)

            status_str = result.status if result.status is not None else "ERR"
            logger.info(
                "Checked %s — status=%s time_ms=%.2f ok=%s alert=%s",
                result.name,
                status_str,
                result.time_ms,
                result.ok,
                alert_triggered,
            )
            if alert_triggered:
                logger.warning(
                    "[ALERTA] %s has failed %d consecutive times",
                    result.name,
                    alert_threshold,
                )
                slack.send_alert(result, alert_threshold)

            if result.ok and was_alerted:
                logger.info("[RECUPERADO] %s is back online", result.name)
                slack.send_recovery(result)

            results.append(result)
            if not result.ok:
                any_failed = True

        if not quiet:
            any_failed = self.print_summary(results) or any_failed

        logger.info(
            "Monitor finished — passed=%d failed=%d",
            sum(1 for r in results if r.ok),
            sum(1 for r in results if not r.ok),
        )
        return 1 if any_failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Site monitor")
    parser.add_argument("--quiet", action="store_true", help="Suppress console output")
    parser.add_argument("--config", type=str, default="/home/ubuntu/www/site-monitor/config.yaml", help="Path to config YAML")
    args = parser.parse_args()

    base_dir = Path(args.config).parent
    monitor = Monitor(base_dir)
    monitor.config_path = Path(args.config)
    return monitor.run(quiet=args.quiet)


if __name__ == "__main__":
    sys.exit(main())
