import hashlib
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from app.runtime_paths import resolve_runtime_paths


logger = logging.getLogger("aurelia.runtime_migration")


@dataclass(frozen=True)
class MigrationOutcome:
    item: str
    status: str
    files_copied: int = 0
    conflicts: int = 0


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _same_path(left, right):
    return os.path.normcase(str(Path(left).resolve())) == os.path.normcase(str(Path(right).resolve()))


def _copy_without_overwrite(source, destination):
    source = Path(source)
    destination = Path(destination)
    if destination.exists():
        return "already_present" if _sha256(source) == _sha256(destination) else "conflict"
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination.parent, prefix=f".{destination.name}.", suffix=".tmp", delete=False
        ) as output:
            temporary = Path(output.name)
            with source.open("rb") as input_stream:
                shutil.copyfileobj(input_stream, output)
            output.flush()
            os.fsync(output.fileno())
        if _sha256(source) != _sha256(temporary):
            raise OSError("legacy runtime copy verification failed")
        try:
            os.link(temporary, destination)
        except FileExistsError:
            return "already_present" if _sha256(source) == _sha256(destination) else "conflict"
        return "copied"
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _migrate_directory(item, source, destination):
    source = Path(source)
    destination = Path(destination)
    if _same_path(source, destination) or not source.is_dir():
        return MigrationOutcome(item, "not_applicable")
    copied = conflicts = existing = 0
    for source_file in sorted(path for path in source.rglob("*") if path.is_file()):
        status = _copy_without_overwrite(source_file, destination / source_file.relative_to(source))
        copied += status == "copied"
        conflicts += status == "conflict"
        existing += status == "already_present"
    if conflicts:
        status = "conflict"
    elif copied:
        status = "copied"
    elif existing:
        status = "already_present"
    else:
        status = "empty"
    return MigrationOutcome(item, status, copied, conflicts)


def _migrate_file(item, source, destination):
    source = Path(source)
    destination = Path(destination)
    if _same_path(source, destination) or not source.is_file():
        return MigrationOutcome(item, "not_applicable")
    status = _copy_without_overwrite(source, destination)
    return MigrationOutcome(item, status, int(status == "copied"), int(status == "conflict"))


def migrate_legacy_runtime_data(config):
    paths = resolve_runtime_paths(config.program_dir)
    outcomes = (
        _migrate_directory(
            "generated_documents", paths.legacy_generated_root, config.generated_documents_dir
        ),
        _migrate_directory("uploads", paths.legacy_upload_root, config.uploads_dir),
        _migrate_directory(
            "email_attachments", paths.legacy_email_attachment_root, config.email_attachments_dir
        ),
        _migrate_file("session_secret", paths.legacy_session_secret_path, config.session_secret_path),
        _migrate_file(
            "gmail_credentials",
            paths.legacy_google_credentials_path,
            config.config_dir / "google_client_secret.json",
        ),
        _migrate_file(
            "gmail_token", paths.legacy_gmail_token_path, config.secrets_dir / "gmail" / "token.json"
        ),
    )
    for outcome in outcomes:
        logger.info(
            "runtime_migration item=%s status=%s copied=%d conflicts=%d",
            outcome.item,
            outcome.status,
            outcome.files_copied,
            outcome.conflicts,
        )
    return outcomes
