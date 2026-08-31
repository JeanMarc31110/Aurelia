import logging
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from app import db
from app.local_config import ensure_local_directories, load_local_config


logger = logging.getLogger("aurelia.backups")
BACKUP_PATTERN = "aurelia_*.db"


def prune_backups(directory, keep_last=14):
    directory = Path(directory)
    backups = sorted(directory.glob(BACKUP_PATTERN), key=lambda path: (path.stat().st_mtime_ns, path.name), reverse=True)
    removed = []
    for path in backups[keep_last:]:
        path.unlink()
        removed.append(path)
    return removed


def create_sqlite_backup(directory=None, source_path=None, reason="manual", keep_last=None):
    config = load_local_config()
    source = Path(source_path or db.DB_PATH)
    if not source.is_file():raise FileNotFoundError(f"Base SQLite introuvable: {source}")
    directory = Path(directory or config.backups_dir);directory.mkdir(parents=True, exist_ok=True)
    safe_reason = re.sub(r"[^a-z0-9_-]", "-", str(reason).lower()).strip("-") or "manual"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = directory / f"aurelia_{stamp}_{safe_reason}.db"
    temporary = destination.with_suffix(".db.tmp")
    source_connection = sqlite3.connect(source, timeout=10)
    target_connection = sqlite3.connect(temporary)
    try:
        source_connection.backup(target_connection)
        target_connection.commit()
        integrity = target_connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":raise IOError(f"Backup SQLite invalide: {integrity}")
    finally:
        target_connection.close();source_connection.close()
    os.replace(temporary, destination)
    removed = prune_backups(directory, keep_last or config.backup_retention)
    logger.info("backup_created filename=%s removed=%s", destination.name, len(removed))
    return destination


def backup_if_due(config=None, source_path=None, reason="automatic"):
    config = ensure_local_directories(config or load_local_config())
    source = Path(source_path or db.DB_PATH)
    if not source.is_file():return None
    backups = sorted(config.backups_dir.glob(BACKUP_PATTERN), key=lambda path: path.stat().st_mtime, reverse=True)
    if backups and time.time() - backups[0].stat().st_mtime < config.backup_interval_seconds:
        return None
    return create_sqlite_backup(config.backups_dir, source, reason, config.backup_retention)


class BackupScheduler:
    def __init__(self, config=None, source_path=None):
        self.config = ensure_local_directories(config or load_local_config())
        self.source_path = Path(source_path or db.DB_PATH)
        self._stop_event = threading.Event()
        self._thread = None

    @property
    def running(self):return bool(self._thread and self._thread.is_alive())

    def _run(self):
        logger.info("backup_scheduler_started")
        while not self._stop_event.is_set():
            try:backup_if_due(self.config, self.source_path)
            except Exception as exc:logger.exception("automatic_backup_failed error=%s", type(exc).__name__)
            self._stop_event.wait(min(self.config.backup_interval_seconds, 3600))
        logger.info("backup_scheduler_stopped")

    def start(self):
        if self.running:return False
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="aurelia-backup-scheduler", daemon=True)
        self._thread.start();return True

    def stop(self, timeout=10):
        if not self._thread:return False
        self._stop_event.set();self._thread.join(timeout)
        stopped = not self._thread.is_alive()
        if stopped:self._thread = None
        return stopped
