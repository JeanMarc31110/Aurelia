import os
from dataclasses import dataclass
from pathlib import Path

from app.resource_paths import program_directory
from app.runtime_paths import resolve_runtime_paths

PROGRAM_DIR = program_directory()


def _legacy_database_path(program_dir):
    program_dir = Path(program_dir).resolve()
    explicit = os.getenv("AURELIA_LEGACY_DB_PATH", "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    isolated_runtime = os.getenv("AURELIA_RUNTIME_ROOT", "").strip()
    if isolated_runtime:
        return (Path(isolated_runtime).expanduser().resolve() / "legacy" / "aurelia_v5.db")
    candidates = [program_dir / "data" / "aurelia_v5.db"]
    # A repository build lives in <project>/dist/Aurelia.  Supporting this
    # layout lets the first packaged launch migrate the Phase 4 database.
    if program_dir.parent.name.lower() == "dist":
        candidates.append(program_dir.parent.parent / "data" / "aurelia_v5.db")
    return next((path for path in candidates if path.is_file()), candidates[0])


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
    internal_data_dir: Path
    config_dir: Path
    secrets_dir: Path
    documents_dir: Path
    inbox_dir: Path
    processed_dir: Path
    errors_dir: Path
    archive_dir: Path
    exports_dir: Path
    backups_dir: Path
    logs_dir: Path
    uploads_dir: Path
    email_attachments_dir: Path
    generated_documents_dir: Path
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
    complete_backup_retention: int

    @property
    def session_secret_path(self):
        return self.secrets_dir / "session.secret"

    @property
    def legacy_sqlite_path(self):
        return _legacy_database_path(self.program_dir)

    def required_directories(self):
        return (
            self.data_dir, self.internal_data_dir, self.config_dir, self.secrets_dir,
            self.documents_dir, self.inbox_dir, self.processed_dir,
            self.errors_dir, self.archive_dir, self.exports_dir, self.backups_dir,
            self.logs_dir, self.uploads_dir, self.email_attachments_dir,
            self.generated_documents_dir, self.sqlite_path.parent,
        )


def load_local_config(program_dir=None):
    program_dir = Path(program_dir or PROGRAM_DIR).resolve()
    paths = resolve_runtime_paths(program_dir)
    return LocalConfig(
        program_dir=program_dir, data_dir=paths.state_root,
        internal_data_dir=paths.data_root, config_dir=paths.config_root,
        secrets_dir=paths.secret_root, documents_dir=paths.documents_root,
        inbox_dir=paths.inbox_root, processed_dir=paths.processed_root,
        errors_dir=paths.error_root, archive_dir=paths.archive_root,
        exports_dir=paths.export_root, backups_dir=paths.backup_root,
        logs_dir=paths.log_root, uploads_dir=paths.upload_root,
        email_attachments_dir=paths.email_attachment_root,
        generated_documents_dir=paths.generated_document_root,
        sqlite_path=paths.sqlite_path, log_path=paths.log_root / "aurelia.log",
        legacy_data_layout=_legacy_database_path(program_dir).is_file(),
        watcher_enabled=_bool_from_env("AURELIA_WATCHER_ENABLED", True),
        watcher_poll_seconds=_float_from_env("AURELIA_WATCHER_POLL_SECONDS", 1.0, 0.05),
        watcher_stable_checks=_int_from_env("AURELIA_WATCHER_STABLE_CHECKS", 3, 2),
        watcher_stable_seconds=_float_from_env("AURELIA_WATCHER_STABLE_SECONDS", 1.0, 0.05),
        backups_enabled=_bool_from_env("AURELIA_BACKUPS_ENABLED", True),
        backup_interval_seconds=_float_from_env("AURELIA_BACKUP_INTERVAL_HOURS", 24.0, 0.01) * 3600,
        backup_retention=_int_from_env("AURELIA_BACKUP_RETENTION", 14, 1),
        complete_backup_retention=_int_from_env("AURELIA_COMPLETE_BACKUP_RETENTION", 5, 1),
    )


def ensure_local_directories(config=None):
    config = config or load_local_config()
    for directory in dict.fromkeys(config.required_directories()):
        directory.mkdir(parents=True, exist_ok=True)
    return config
