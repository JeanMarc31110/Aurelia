import errno
import logging
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.local_config import ensure_local_directories, load_local_config


logger = logging.getLogger("aurelia.migration")
CRITICAL_TABLES = (
    "invoices", "bank_transactions", "payment_matches", "accounting_exports",
    "companies", "users", "ingested_files",
)


def database_snapshot(path):
    path = Path(path)
    connection = sqlite3.connect(path, timeout=10)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        counts = {
            table: connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in CRITICAL_TABLES if table in tables
        }
        active_company = connection.execute(
            "SELECT COUNT(*) FROM companies WHERE active=1"
        ).fetchone()[0] if "companies" in tables else 0
        active_admin = connection.execute(
            "SELECT COUNT(*) FROM users WHERE role='admin' AND active=1"
        ).fetchone()[0] if "users" in tables else 0
        return {"integrity": integrity, "counts": counts,
                "active_company": active_company, "active_admin": active_admin}
    finally:
        connection.close()


def _sqlite_backup(source, destination):
    source_connection = sqlite3.connect(source, timeout=10)
    target_connection = sqlite3.connect(destination)
    try:
        source_connection.backup(target_connection)
        target_connection.commit()
    finally:
        target_connection.close();source_connection.close()


def _publish_without_overwrite(temporary, destination, expected_snapshot):
    try:
        os.replace(temporary, destination)
        return "atomic_replace"
    except OSError as exc:
        if exc.errno != errno.EXDEV and getattr(exc, "winerror", None) != 17:
            raise
    if destination.exists():
        return "target_appeared"
    # Some Windows/junction layouts reject os.replace with WinError 17 even
    # inside the same apparent directory.  Exclusive creation still prevents
    # overwriting a target that appeared concurrently; the integrity and
    # critical counts are rechecked immediately after the durable copy.
    with temporary.open("rb") as source_stream, destination.open("xb") as target_stream:
        shutil.copyfileobj(source_stream, target_stream, length=1024 * 1024)
        target_stream.flush();os.fsync(target_stream.fileno())
    if database_snapshot(destination) != expected_snapshot:
        raise RuntimeError("La publication LocalAppData ne correspond pas à la base historique")
    return "exclusive_copy"


def migrate_legacy_database(config=None, force=False):
    config = ensure_local_directories(config or load_local_config())
    source = Path(config.legacy_sqlite_path)
    destination = Path(config.sqlite_path)
    if not force and (os.getenv("AURELIA_DB_PATH") or os.getenv("AURELIA_DATA_DIR")):
        return {"status": "explicit_configuration", "source": str(source),
                "destination": str(destination)}
    if source.resolve() == destination.resolve():
        return {"status": "same_path", "source": str(source), "destination": str(destination)}
    if destination.exists():
        target = database_snapshot(destination)
        if target["integrity"] != "ok":
            raise RuntimeError("La base LocalAppData existante est invalide; aucun écrasement effectué")
        return {"status": "target_exists", "source": str(source), "destination": str(destination),
                "target": target}
    if not source.is_file():
        return {"status": "no_legacy_database", "source": str(source),
                "destination": str(destination)}

    source_snapshot = database_snapshot(source)
    if source_snapshot["integrity"] != "ok":
        raise RuntimeError("La base historique est invalide; migration annulée")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    safety_backup = config.backups_dir / f"aurelia_{stamp}_pre-localappdata-migration.db"
    _sqlite_backup(source, safety_backup)
    if database_snapshot(safety_backup) != source_snapshot:
        safety_backup.unlink(missing_ok=True)
        raise RuntimeError("Le backup préalable ne correspond pas à la base historique")

    temporary = destination.with_name(f".{destination.name}.{stamp}.migration.tmp")
    try:
        _sqlite_backup(source, temporary)
        target_snapshot = database_snapshot(temporary)
        if target_snapshot != source_snapshot:
            raise RuntimeError("La copie LocalAppData ne correspond pas à la base historique")
        if destination.exists():
            return {"status": "target_appeared", "source": str(source),
                    "destination": str(destination), "safety_backup": str(safety_backup)}
        publication = _publish_without_overwrite(temporary, destination, source_snapshot)
        if publication == "target_appeared":
            return {"status": "target_appeared", "source": str(source),
                    "destination": str(destination), "safety_backup": str(safety_backup)}
    finally:
        temporary.unlink(missing_ok=True)

    logger.info("legacy_database_migrated backup=%s", safety_backup.name)
    return {"status": "migrated", "source": str(source), "destination": str(destination),
            "safety_backup": str(safety_backup), "publication": publication,
            "source_snapshot": source_snapshot,
            "target_snapshot": database_snapshot(destination)}
