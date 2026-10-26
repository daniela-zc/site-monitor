#!/usr/bin/env python3
"""Monthly cleanup: purges old SQLite records and orphaned failure_counts."""

import logging
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import yaml


def load_config(config_path: Path) -> dict:
    with config_path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def setup_logging(logs_dir: Path) -> logging.Logger:
    logs_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    log_file = logs_dir / f"cleanup-{today}.log"

    logger = logging.getLogger("cleanup")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(formatter)
    logger.addHandler(sh)

    return logger


def cleanup_state(db_path: Path, retention_days: int, enabled_sites: set[str], logger: logging.Logger) -> dict:
    cutoff = datetime.now() - timedelta(days=retention_days)
    cutoff_iso = cutoff.isoformat()

    stats = {"deleted_checks": 0, "deleted_failures": 0}

    with sqlite3.connect(str(db_path)) as conn:
        cursor = conn.cursor()

        # Delete old site_checks records
        cursor.execute(
            "DELETE FROM site_checks WHERE checked_at < ?",
            (cutoff_iso,),
        )
        stats["deleted_checks"] = cursor.rowcount

        # Delete orphaned failure_counts for sites no longer in config
        placeholders = ",".join("?" * len(enabled_sites)) if enabled_sites else ""
        if placeholders:
            cursor.execute(
                f"DELETE FROM failure_counts WHERE name NOT IN ({placeholders})",
                tuple(enabled_sites),
            )
            stats["deleted_failures"] = cursor.rowcount

        conn.commit()

    logger.info(
        "Cleanup complete: deleted %d old site_checks (before %s) and %d orphaned failure_counts",
        stats["deleted_checks"],
        cutoff_iso,
        stats["deleted_failures"],
    )

    return stats


def cleanup_logs(logs_dir: Path, retention_days: int, logger: logging.Logger) -> int:
    cutoff = datetime.now() - timedelta(days=retention_days)
    count = 0

    for pattern in ("monitor-*.log", "cleanup-*.log"):
        for f in logs_dir.glob(pattern):
            try:
                date_str = f.stem.split("-")[1]
                file_date = datetime.strptime(date_str, "%Y-%m-%d")
                if file_date < cutoff:
                    f.unlink()
                    count += 1
                    logger.info("Deleted old log: %s", f.name)
            except (ValueError, IndexError):
                continue

    logger.info("Deleted %d old log files", count)
    return count


def main() -> int:
    base_dir = Path(__file__).parent.resolve()
    config_path = base_dir / "config.yaml"
    db_path = base_dir / "state.db"
    logs_dir = base_dir / "logs"

    config = load_config(config_path)
    retention_days = config.get("log_retention_days", 30)
    raw_sites = config.get("sites", [])
    enabled_sites = {s.get("name", "Unnamed") for s in raw_sites if s.get("enabled", True)}

    logger = setup_logging(logs_dir)
    logger.info("Monthly cleanup started — retention=%d days", retention_days)

    if not db_path.exists():
        logger.info("state.db not found, nothing to clean")
        return 0

    state_stats = cleanup_state(db_path, retention_days, enabled_sites, logger)
    log_count = cleanup_logs(logs_dir, retention_days, logger)

    logger.info(
        "Cleanup finished — deleted_checks=%d deleted_failures=%d deleted_logs=%d",
        state_stats["deleted_checks"],
        state_stats["deleted_failures"],
        log_count,
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
