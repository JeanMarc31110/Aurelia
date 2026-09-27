import json
import logging
import os
import platform
import shutil
import sys
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from app.local_config import ensure_local_directories, load_local_config
from app.services.backup_restore import MaintenanceError, _database_details, read_maintenance_status
from app.services.integrations import status as integration_status
from app.services.ocr import ocr_status
from app.services.redaction import redact, redact_text
from app.version import APP_VERSION


logger = logging.getLogger("aurelia.maintenance")
MAX_LOG_BYTES = 200_000


def _safe_database_version(config):
    if not config.sqlite_path.is_file():
        return None
    try:
        return _database_details(config.sqlite_path)["schema_version"]
    except Exception:
        return "unavailable"


def _safe_ocr_status():
    try:
        value = ocr_status()
    except Exception:
        return {"available": False, "version": None, "source": "unavailable"}
    return {
        "available": bool(value.get("available")),
        "version": value.get("version"),
        "source": value.get("source"),
        "configured_languages": value.get("configured_languages"),
        "missing_languages": value.get("missing_languages") or [],
    }


def _safe_integrations():
    try:
        values = integration_status()
    except Exception:
        return {"status": "unavailable"}
    return {
        name: {"configured": bool(details.get("configured"))}
        for name, details in values.items()
        if isinstance(details, dict)
    }


def _recent_redacted_log(path):
    path = Path(path)
    if not path.is_file():
        return ""
    with path.open("rb") as stream:
        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        stream.seek(max(0, size - MAX_LOG_BYTES))
        content = stream.read(MAX_LOG_BYTES).decode("utf-8", errors="replace")
    return redact_text(content)


def create_diagnostic_bundle(config=None, destination_dir=None):
    config = ensure_local_directories(config or load_local_config())
    operation_id = uuid.uuid4().hex
    destination_dir = Path(destination_dir or (config.exports_dir / "Diagnostics"))
    destination_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc)
    filename = f"Aurelia-Diagnostics-{timestamp.strftime('%Y%m%d-%H%M%S')}-{operation_id[:8]}.zip"
    destination = destination_dir / filename
    temporary = destination_dir / f".{filename}.{operation_id}.tmp"
    try:
        maintenance = read_maintenance_status(config)
    except MaintenanceError:
        maintenance = {"last_status_result": "unavailable"}
    last_state = {
        key: value for key, value in maintenance.items()
        if key.startswith("last_") and not key.endswith("_path")
    }
    metadata = redact({
        "diagnostic_format_version": 1,
        "operation_id": operation_id,
        "created_at_utc": timestamp.isoformat(),
        "aurelia_version": APP_VERSION,
        "build_commit": os.getenv("AURELIA_BUILD_COMMIT") or None,
        "database_schema_version": _safe_database_version(config),
        "operating_system": {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "architecture": platform.machine(),
        },
        "runtime": {"python_version": platform.python_version(), "frozen": bool(getattr(sys, "frozen", False))},
        "ocr": _safe_ocr_status(),
        "integrations": _safe_integrations(),
        "runtime_paths": {
            "state": "%LOCALAPPDATA%/Aurelia",
            "data": "%LOCALAPPDATA%/Aurelia/Data",
            "config": "%LOCALAPPDATA%/Aurelia/Config",
            "logs": "%LOCALAPPDATA%/Aurelia/Logs",
            "documents": "%USERPROFILE%/Documents/Aurelia",
        },
        "disk": {
            "state_free_bytes": shutil.disk_usage(config.data_dir).free,
            "documents_free_bytes": shutil.disk_usage(config.documents_dir).free,
        },
        "maintenance": last_state,
        "last_startup_status": "running" if config.log_path.is_file() else "not_recorded",
    })
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "diagnostics.json",
                json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"),
            )
            archive.writestr("recent.log", _recent_redacted_log(config.log_path).encode("utf-8"))
        with temporary.open("r+b") as stream:
            os.fsync(stream.fileno())
        if destination.exists():
            raise FileExistsError(destination)
        os.replace(temporary, destination)
        logger.info(
            "maintenance operation_id=%s operation=diagnostics result=success bundle=%s",
            operation_id, destination.name,
        )
        return {"path": destination, "metadata": metadata, "operation_id": operation_id}
    finally:
        temporary.unlink(missing_ok=True)
