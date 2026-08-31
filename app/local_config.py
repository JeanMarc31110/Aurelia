import os
from dataclasses import dataclass
from pathlib import Path

from app.resource_paths import program_directory

PROGRAM_DIR = program_directory()


def _legacy_database_path(program_dir):
    program_dir = Path(program_dir).resolve()
    explicit = os.getenv("AURELIA_LEGACY_DB_PATH", "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    candidates = [program_dir / "data" / "aurelia_v5.db"]
    # A repository build lives in <project>/dist/Aurelia.  Supporting this
    # layout lets the first packaged launch migrate the Phase 4 database.
    if program_dir.parent.name.lower() == "dist":
        candidates.append(program_dir.parent.parent / "data" / "aurelia_v5.db")
    return next((path for path in candidates if path.is_file()), candidates[0])


def _path_from_env(name, default):
    value = os.getenv(name, "").strip()
    return Path(value).expanduser().resolve() if value else Path(default).expanduser().resolve()


def _bool_from_env(name, default=True):
    value = os.getenv(name)
    if value is None:return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _float_from_env(name, default, minimum):
    try:return max(minimum, float(os.getenv(name, default)))
    except (TypeError, ValueError):return default


def _int_from_env(name, default, minimum):
    try:return max(minimum, int(os.getenv(name, default)))
    except (TypeError, ValueError):return default


@dataclass(frozen=True)
class LocalConfig:
    program_dir: Path
    data_dir: Path
    documents_dir: Path
    inbox_dir: Path
    processed_dir: Path
    errors_dir: Path
    archive_dir: Path
    exports_dir: Path
    backups_dir: Path
    logs_dir: Path
    uploads_dir: Path
    sqlite_path: Path
    log_path: Path
    legacy_data_layout: bool
    watcher_enabled: bool
    watcher_poll_seconds: float
    watcher_stable_checks: int
    watcher_stable_seconds: float
    backups_enabled: bool
    backup_interval_seconds: float
    backup_retention: int

    @property
    def session_secret_path(self):
        return self.data_dir / "session.secret"

    @property
    def legacy_sqlite_path(self):
        return _legacy_database_path(self.program_dir)

    def required_directories(self):
        return (
            self.data_dir, self.documents_dir, self.inbox_dir, self.processed_dir,
            self.errors_dir, self.archive_dir, self.exports_dir, self.backups_dir,
            self.logs_dir, self.uploads_dir, self.sqlite_path.parent,
        )


def load_local_config(program_dir=None):
    program_dir = Path(program_dir or PROGRAM_DIR).resolve()
    explicit_data = os.getenv("AURELIA_DATA_DIR", "").strip()
    if explicit_data:
        data_dir = _path_from_env("AURELIA_DATA_DIR", explicit_data)
    else:
        local_app_data = Path(os.getenv("LOCALAPPDATA") or (Path.home() / ".local" / "share"))
        data_dir = (local_app_data / "Aurelia").resolve()

    user_documents = Path(os.getenv("USERPROFILE") or Path.home()) / "Documents" / "Aurelia"
    documents_dir = _path_from_env("AURELIA_DOCUMENTS_DIR", user_documents)
    inbox = _path_from_env("AURELIA_INBOX_DIR", documents_dir / "Inbox")
    processed = _path_from_env("AURELIA_PROCESSED_DIR", documents_dir / "Processed")
    errors = _path_from_env("AURELIA_ERRORS_DIR", documents_dir / "Errors")
    archive = _path_from_env("AURELIA_ARCHIVE_DIR", documents_dir / "Archive")
    exports = _path_from_env("AURELIA_EXPORTS_PATH", documents_dir / "Exports")
    backups = _path_from_env("AURELIA_BACKUPS_DIR", documents_dir / "Backups")
    uploads = _path_from_env("AURELIA_UPLOADS_PATH", data_dir / "uploads")
    sqlite_path = _path_from_env("AURELIA_DB_PATH", data_dir / "aurelia_v5.db")
    logs = _path_from_env("AURELIA_LOGS_DIR", data_dir / "logs")
    return LocalConfig(
        program_dir=program_dir, data_dir=data_dir, documents_dir=documents_dir,
        inbox_dir=inbox, processed_dir=processed, errors_dir=errors, archive_dir=archive,
        exports_dir=exports, backups_dir=backups, logs_dir=logs, uploads_dir=uploads,
        sqlite_path=sqlite_path, log_path=logs / "aurelia.log",
        legacy_data_layout=_legacy_database_path(program_dir).is_file(),
        watcher_enabled=_bool_from_env("AURELIA_WATCHER_ENABLED", True),
        watcher_poll_seconds=_float_from_env("AURELIA_WATCHER_POLL_SECONDS", 1.0, 0.05),
        watcher_stable_checks=_int_from_env("AURELIA_WATCHER_STABLE_CHECKS", 3, 2),
        watcher_stable_seconds=_float_from_env("AURELIA_WATCHER_STABLE_SECONDS", 1.0, 0.05),
        backups_enabled=_bool_from_env("AURELIA_BACKUPS_ENABLED", True),
        backup_interval_seconds=_float_from_env("AURELIA_BACKUP_INTERVAL_HOURS", 24.0, 0.01) * 3600,
        backup_retention=_int_from_env("AURELIA_BACKUP_RETENTION", 14, 1),
    )


def ensure_local_directories(config=None):
    config = config or load_local_config()
    for directory in dict.fromkeys(config.required_directories()):
        directory.mkdir(parents=True, exist_ok=True)
    return config
