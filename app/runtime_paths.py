import os
from dataclasses import dataclass
from pathlib import Path


def _resolved(value):
    return Path(value).expanduser().resolve()


def _from_env(name, default):
    value = os.getenv(name, "").strip()
    return _resolved(value or default)


def _home_directory():
    explicit = os.getenv("USERPROFILE") or os.getenv("HOME")
    if explicit:
        return _resolved(explicit)
    try:
        return Path.home().resolve()
    except RuntimeError:
        return Path.cwd().resolve()


@dataclass(frozen=True)
class RuntimePaths:
    app_install_root: Path
    state_root: Path
    data_root: Path
    config_root: Path
    secret_root: Path
    log_root: Path
    documents_root: Path
    inbox_root: Path
    processed_root: Path
    error_root: Path
    archive_root: Path
    export_root: Path
    backup_root: Path
    upload_root: Path
    email_attachment_root: Path
    generated_document_root: Path
    sqlite_path: Path
    legacy_generated_root: Path
    legacy_upload_root: Path
    legacy_email_attachment_root: Path
    legacy_session_secret_path: Path
    legacy_google_credentials_path: Path
    legacy_gmail_token_path: Path


def resolve_runtime_paths(app_install_root):
    app_install_root = _resolved(app_install_root)
    runtime_root = os.getenv("AURELIA_RUNTIME_ROOT", "").strip()
    if runtime_root:
        runtime_root = _resolved(runtime_root)
        default_state_root = runtime_root / "state"
        default_documents_root = runtime_root / "documents"
    else:
        home = _home_directory()
        local_app_data = _resolved(os.getenv("LOCALAPPDATA") or (home / ".local" / "share"))
        user_profile = _resolved(os.getenv("USERPROFILE") or home)
        default_state_root = local_app_data / "Aurelia"
        default_documents_root = user_profile / "Documents" / "Aurelia"

    state_root = _from_env("AURELIA_DATA_DIR", default_state_root)
    data_root = _from_env("AURELIA_INTERNAL_DATA_DIR", state_root / "Data")
    config_root = _from_env("AURELIA_CONFIG_DIR", state_root / "Config")
    secret_root = _from_env("AURELIA_SECRETS_DIR", state_root / "Secrets")
    log_root = _from_env("AURELIA_LOGS_DIR", state_root / "Logs")
    documents_root = _from_env("AURELIA_DOCUMENTS_DIR", default_documents_root)

    return RuntimePaths(
        app_install_root=app_install_root,
        state_root=state_root,
        data_root=data_root,
        config_root=config_root,
        secret_root=secret_root,
        log_root=log_root,
        documents_root=documents_root,
        inbox_root=_from_env("AURELIA_INBOX_DIR", documents_root / "Inbox"),
        processed_root=_from_env("AURELIA_PROCESSED_DIR", documents_root / "Processed"),
        error_root=_from_env("AURELIA_ERRORS_DIR", documents_root / "Errors"),
        archive_root=_from_env("AURELIA_ARCHIVE_DIR", documents_root / "Archive"),
        export_root=_from_env("AURELIA_EXPORTS_PATH", documents_root / "Exports"),
        backup_root=_from_env("AURELIA_BACKUPS_DIR", documents_root / "Backups"),
        upload_root=_from_env("AURELIA_UPLOADS_PATH", data_root / "uploads"),
        email_attachment_root=_from_env(
            "AURELIA_EMAIL_ATTACHMENTS_DIR", data_root / "email_attachments"
        ),
        generated_document_root=_from_env(
            "AURELIA_GENERATED_DOCUMENTS_DIR", documents_root / "Generated"
        ),
        sqlite_path=_from_env("AURELIA_DB_PATH", state_root / "aurelia_v5.db"),
        legacy_generated_root=app_install_root / "data" / "generated",
        legacy_upload_root=state_root / "uploads",
        legacy_email_attachment_root=state_root / "email_attachments",
        legacy_session_secret_path=state_root / "session.secret",
        legacy_google_credentials_path=state_root / "config" / "google_client_secret.json",
        legacy_gmail_token_path=state_root / "gmail" / "token.json",
    )
