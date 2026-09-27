import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from app import db
from app.instance_lock import InstanceLock
from app.local_config import ensure_local_directories, load_local_config
from app.version import APP_VERSION


logger = logging.getLogger("aurelia.maintenance")
BACKUP_FORMAT_VERSION = 1
RUNTIME_LAYOUT_VERSION = 1
BACKUP_SUFFIX = ".aurelia-backup"
MINIMUM_FREE_MARGIN = 64 * 1024 * 1024
EPHEMERAL_SUFFIXES = {".tmp", ".temp", ".lock", ".part"}
SECRET_NAME_MARKERS = ("secret", "token", "password", "credential", "api_key", "apikey")
SENSITIVE_CONFIG_PATTERN = re.compile(
    r'''(?ix)["']?(access_token|refresh_token|password|client_secret|api_key|apikey|session_secret)["']?\s*[:=]'''
)


class MaintenanceError(RuntimeError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _utc_now():
    return datetime.now(timezone.utc)


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _disk_free(path):
    probe = Path(path)
    probe.mkdir(parents=True, exist_ok=True)
    return shutil.disk_usage(probe).free


def _check_space(path, required, code):
    available = _disk_free(path)
    if available < required:
        raise MaintenanceError(code, f"Espace disque insuffisant: {required} octets requis")
    return available


def _status_path(config):
    return config.internal_data_dir / "maintenance_status.json"


def read_maintenance_status(config=None):
    config = config or load_local_config()
    path = _status_path(config)
    if not path.is_file():
        return {"operations": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MaintenanceError("STATUS_INVALID", "État de maintenance local invalide") from exc
    return data if isinstance(data, dict) else {"operations": []}


def _write_status(config, operation, result, **details):
    path = _status_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    status = read_maintenance_status(config)
    timestamp = _utc_now().isoformat()
    record = {
        "operation_id": details.pop("operation_id"),
        "operation": operation,
        "timestamp_utc": timestamp,
        "result": result,
        **details,
    }
    operations = list(status.get("operations") or [])[-19:]
    operations.append(record)
    status["operations"] = operations
    status[f"last_{operation}_time"] = timestamp
    status[f"last_{operation}_result"] = result
    if "backup_reference" in record:
        status[f"last_{operation}_reference"] = record["backup_reference"]
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(status, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return record


def _database_details(path):
    try:
        connection = sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True, timeout=10)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            schema_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        finally:
            connection.close()
    except sqlite3.DatabaseError as exc:
        raise MaintenanceError("DATABASE_CORRUPT", "La base SQLite est illisible") from exc
    if integrity != "ok":
        raise MaintenanceError("DATABASE_CORRUPT", f"Échec integrity_check: {integrity}")
    return {"integrity": integrity, "schema_version": schema_version}


def _capture_database(source, destination):
    _database_details(source)
    try:
        source_connection = sqlite3.connect(source, timeout=10)
        target_connection = sqlite3.connect(destination)
        try:
            source_connection.backup(target_connection)
            target_connection.commit()
        finally:
            target_connection.close()
            source_connection.close()
    except sqlite3.DatabaseError as exc:
        raise MaintenanceError("DATABASE_BACKUP_FAILED", "La sauvegarde SQLite a échoué") from exc
    return _database_details(destination)


def _inside(path, root):
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False


def _excluded_config_secret(path):
    path = Path(path)
    name = path.name.lower()
    if any(marker in name for marker in SECRET_NAME_MARKERS):
        return True
    if path.suffix.lower() not in {".json", ".ini", ".toml", ".yaml", ".yml", ".env", ".txt"}:
        return False
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    return bool(SENSITIVE_CONFIG_PATTERN.search(content))


def _enumerate_root(root, logical_root, excluded_roots=(), exclude_config_secrets=False):
    root = Path(root)
    if not root.exists():
        return []
    if root.is_symlink() or not root.is_dir():
        raise MaintenanceError("DATASET_PATH_INVALID", f"Racine de données invalide: {logical_root}")
    excluded = tuple(Path(path).resolve() for path in excluded_roots)
    files = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise MaintenanceError("SYMLINK_REJECTED", f"Lien symbolique refusé: {logical_root}")
        if not path.is_file():
            continue
        resolved = path.resolve()
        if not _inside(resolved, root):
            raise MaintenanceError("PATH_ESCAPE", f"Chemin hors racine refusé: {logical_root}")
        if any(_inside(resolved, excluded_root) for excluded_root in excluded):
            continue
        if path.suffix.lower() in EPHEMERAL_SUFFIXES or path.name.endswith("~"):
            continue
        if exclude_config_secrets and _excluded_config_secret(path):
            continue
        relative = path.relative_to(root).as_posix()
        files.append((path, f"{logical_root}/{relative}"))
    return files


def _dataset_sources(config):
    sources = []
    sources.extend(_enumerate_root(config.internal_data_dir, "data"))
    sources.extend(_enumerate_root(
        config.documents_dir, "documents", excluded_roots=(config.backups_dir,)
    ))
    sources.extend(_enumerate_root(config.config_dir, "config", exclude_config_secrets=True))
    logical_paths = [logical for _, logical in sources]
    if len(logical_paths) != len(set(logical_paths)):
        raise MaintenanceError("DATASET_DUPLICATE", "Deux fichiers partagent le même chemin logique")
    return sources


def _dataset_size(config):
    total = config.sqlite_path.stat().st_size if config.sqlite_path.is_file() else 0
    return total + sum(source.stat().st_size for source, _ in _dataset_sources(config))


def _manifest_inventory(entries):
    inventory = {}
    for entry in entries:
        category = entry["path"].split("/", 1)[0]
        row = inventory.setdefault(category, {"file_count": 0, "logical_size": 0})
        row["file_count"] += 1
        row["logical_size"] += entry["size"]
    return inventory


def _schema_upgrade_supported(version):
    if version > db.CURRENT_SCHEMA_VERSION:
        return False
    while version < db.CURRENT_SCHEMA_VERSION:
        if version not in db.MIGRATIONS:
            return False
        version += 1
    return True


def _prune_complete_backups(directory, keep_last):
    backups = sorted(
        Path(directory).glob(f"Aurelia-Backup-*{BACKUP_SUFFIX}"),
        key=lambda path: (path.stat().st_mtime_ns, path.name),
        reverse=True,
    )
    removed = []
    for path in backups[keep_last:]:
        path.unlink()
        removed.append(path)
    return removed


def create_complete_backup(config=None, destination_dir=None, retain=True, operation_label="backup"):
    config = ensure_local_directories(config or load_local_config())
    operation_id = uuid.uuid4().hex
    destination_dir = Path(destination_dir or config.backups_dir)
    destination_dir.mkdir(parents=True, exist_ok=True)
    estimate = _dataset_size(config)
    _check_space(destination_dir, max(MINIMUM_FREE_MARGIN, estimate * 2), "BACKUP_SPACE_INSUFFICIENT")
    timestamp = _utc_now()
    filename = (
        f"Aurelia-Backup-{timestamp.strftime('%Y%m%d-%H%M%S')}-{operation_id[:8]}"
        f"{BACKUP_SUFFIX}"
    )
    destination = destination_dir / filename
    temporary_archive = destination_dir / f".{filename}.{operation_id}.tmp"
    try:
        with tempfile.TemporaryDirectory(prefix=".aurelia-backup-stage-", dir=destination_dir) as stage_name:
            stage = Path(stage_name)
            database_copy = stage / "database" / "aurelia_v5.db"
            database_copy.parent.mkdir(parents=True)
            database = _capture_database(config.sqlite_path, database_copy)
            entries = []
            snapshots = [(database_copy, "database/aurelia_v5.db")]
            for source, logical in _dataset_sources(config):
                target = stage / "snapshot" / PurePosixPath(logical)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                snapshots.append((target, logical))
            for source, logical in snapshots:
                category = logical.split("/", 1)[0]
                entries.append({
                    "path": logical,
                    "size": source.stat().st_size,
                    "sha256": _sha256(source),
                    "classification": (
                        "sensitive_configuration" if category == "config" else "normal_customer_data"
                    ),
                })
            entries.sort(key=lambda row: row["path"])
            manifest = {
                "backup_format_version": BACKUP_FORMAT_VERSION,
                "backup_id": operation_id,
                "aurelia_version": APP_VERSION,
                "database_schema_version": database["schema_version"],
                "created_at_utc": timestamp.isoformat(),
                "runtime_layout_version": RUNTIME_LAYOUT_VERSION,
                "database_integrity": database["integrity"],
                "secret_handling": {
                    "mode": "excluded_reauthentication_required",
                    "excluded_classes": ["session_secret", "oauth_tokens", "api_credentials"],
                },
                "inventory": _manifest_inventory(entries),
                "total_file_count": len(entries),
                "total_logical_size": sum(row["size"] for row in entries),
                "files": entries,
            }
            with zipfile.ZipFile(temporary_archive, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(
                    "manifest.json",
                    json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"),
                )
                for source, logical in sorted(snapshots, key=lambda item: item[1]):
                    archive.write(source, logical)
        with temporary_archive.open("r+b") as stream:
            os.fsync(stream.fileno())
        validate_backup(temporary_archive)
        if destination.exists():
            raise MaintenanceError("BACKUP_NAME_COLLISION", "Une sauvegarde du même nom existe déjà")
        os.replace(temporary_archive, destination)
        if retain:
            _prune_complete_backups(destination_dir, config.complete_backup_retention)
        _write_status(
            config, operation_label, "success", operation_id=operation_id,
            backup_reference=destination.name, backup_id=operation_id,
            app_version=APP_VERSION, schema_version=database["schema_version"], failure_code=None,
        )
        logger.info(
            "maintenance operation_id=%s operation=%s result=success backup=%s",
            operation_id, operation_label, destination.name,
        )
        return {"path": destination, "manifest": manifest, "operation_id": operation_id}
    except Exception as exc:
        code = exc.code if isinstance(exc, MaintenanceError) else "BACKUP_FAILED"
        _write_status(
            config, operation_label, "failed", operation_id=operation_id,
            backup_reference=None, app_version=APP_VERSION,
            schema_version=None, failure_code=code,
        )
        logger.error(
            "maintenance operation_id=%s operation=%s result=failed code=%s",
            operation_id, operation_label, code,
        )
        if isinstance(exc, MaintenanceError):
            raise
        raise MaintenanceError(code, "La sauvegarde complète a échoué") from exc
    finally:
        temporary_archive.unlink(missing_ok=True)


def _safe_archive_names(archive):
    names = archive.namelist()
    if len(names) != len(set(names)):
        raise MaintenanceError("ARCHIVE_DUPLICATE", "Entrées dupliquées dans la sauvegarde")
    for info in archive.infolist():
        path = PurePosixPath(info.filename)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise MaintenanceError("ARCHIVE_PATH_ESCAPE", "Chemin d'archive non sûr")
        mode = (info.external_attr >> 16) & 0o170000
        if mode == 0o120000:
            raise MaintenanceError("ARCHIVE_SYMLINK", "Lien symbolique interdit dans la sauvegarde")
    return names


def validate_backup(path):
    path = Path(path)
    if not path.is_file():
        raise MaintenanceError("BACKUP_NOT_FOUND", "Sauvegarde introuvable")
    try:
        with zipfile.ZipFile(path, "r") as archive:
            names = _safe_archive_names(archive)
            if "manifest.json" not in names:
                raise MaintenanceError("MANIFEST_MISSING", "manifest.json absent")
            try:
                manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise MaintenanceError("MANIFEST_INVALID", "Manifest illisible") from exc
            required = {
                "backup_format_version", "backup_id", "aurelia_version", "database_schema_version",
                "created_at_utc", "runtime_layout_version", "database_integrity", "secret_handling",
                "total_file_count", "total_logical_size", "files",
            }
            if not isinstance(manifest, dict) or not required.issubset(manifest):
                raise MaintenanceError("MANIFEST_INVALID", "Manifest incomplet")
            if manifest["backup_format_version"] != BACKUP_FORMAT_VERSION:
                raise MaintenanceError("FORMAT_UNSUPPORTED", "Version de sauvegarde non supportée")
            files = manifest["files"]
            if not isinstance(files, list):
                raise MaintenanceError("MANIFEST_INVALID", "Inventaire invalide")
            expected = {row.get("path") for row in files if isinstance(row, dict)}
            if None in expected or len(expected) != len(files):
                raise MaintenanceError("MANIFEST_INVALID", "Inventaire dupliqué ou invalide")
            if set(names) != expected | {"manifest.json"}:
                raise MaintenanceError("ARCHIVE_INVENTORY_MISMATCH", "Contenu différent du manifest")
            total_size = 0
            for row in files:
                logical = row["path"]
                digest = hashlib.sha256()
                size = 0
                with archive.open(logical, "r") as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(block)
                        size += len(block)
                if size != row.get("size") or digest.hexdigest() != row.get("sha256"):
                    raise MaintenanceError("HASH_MISMATCH", f"Intégrité invalide: {logical}")
                total_size += size
            if len(files) != manifest["total_file_count"] or total_size != manifest["total_logical_size"]:
                raise MaintenanceError("MANIFEST_TOTAL_MISMATCH", "Totaux du manifest invalides")
            database_row = next((row for row in files if row["path"] == "database/aurelia_v5.db"), None)
            if database_row is None:
                raise MaintenanceError("DATABASE_MISSING", "Base absente de la sauvegarde")
            with tempfile.TemporaryDirectory(prefix="aurelia-backup-validate-") as directory:
                database_path = Path(directory) / "aurelia_v5.db"
                with archive.open(database_row["path"]) as source, database_path.open("wb") as target:
                    shutil.copyfileobj(source, target)
                database = _database_details(database_path)
            if database["schema_version"] != manifest["database_schema_version"]:
                raise MaintenanceError("SCHEMA_MANIFEST_MISMATCH", "Version de schéma incohérente")
            if not _schema_upgrade_supported(database["schema_version"]):
                raise MaintenanceError("SCHEMA_UNSUPPORTED", "Schéma de sauvegarde non compatible")
            return {"manifest": manifest, "database": database, "path": path}
    except zipfile.BadZipFile as exc:
        raise MaintenanceError("ARCHIVE_INVALID", "Archive de sauvegarde invalide") from exc


def _extract_verified_backup(path, destination):
    validation = validate_backup(path)
    with zipfile.ZipFile(path, "r") as archive:
        for row in validation["manifest"]["files"]:
            target = Path(destination) / PurePosixPath(row["path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(row["path"]) as source, target.open("xb") as output:
                shutil.copyfileobj(source, output)
    return validation


def _validate_restored_runtime(config, expected_backup_id, validator=None):
    database = _database_details(config.sqlite_path)
    if database["schema_version"] != db.CURRENT_SCHEMA_VERSION:
        raise MaintenanceError("POST_RESTORE_SCHEMA", "Schéma restauré inattendu")
    connection = sqlite3.connect(config.sqlite_path, timeout=10)
    try:
        for table in ("users", "companies", "invoices"):
            connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()
    except sqlite3.DatabaseError as exc:
        raise MaintenanceError("POST_RESTORE_QUERY", "Tables restaurées illisibles") from exc
    finally:
        connection.close()
    for directory in (
        config.internal_data_dir, config.config_dir, config.documents_dir,
        config.uploads_dir, config.email_attachments_dir, config.generated_documents_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    if validator is not None:
        validator(config)
    return {"backup_id": expected_backup_id, "database": database}


def _swap_path(live, candidate, rollback, swaps):
    live = Path(live)
    candidate = Path(candidate)
    rollback = Path(rollback)
    existed = live.exists()
    if existed:
        os.replace(live, rollback)
    try:
        os.replace(candidate, live)
    except Exception:
        if existed and rollback.exists() and not live.exists():
            os.replace(rollback, live)
        raise
    swaps.append((live, rollback, existed, None))


def _rollback_swaps(swaps):
    for live, rollback, existed, preserved_child in reversed(swaps):
        failed = live.with_name(f".{live.name}.failed-{uuid.uuid4().hex}")
        if live.exists():
            os.replace(live, failed)
        if preserved_child and failed.is_dir() and rollback.is_dir():
            retained = failed / preserved_child
            if retained.exists() and not (rollback / preserved_child).exists():
                os.replace(retained, rollback / preserved_child)
        if existed and rollback.exists():
            os.replace(rollback, live)
        if failed.is_dir():
            shutil.rmtree(failed)
        else:
            failed.unlink(missing_ok=True)


def restore_backup(path, config=None, post_restore_validator=None):
    config = ensure_local_directories(config or load_local_config())
    validation = validate_backup(path)
    manifest = validation["manifest"]
    operation_id = uuid.uuid4().hex
    current_size = _dataset_size(config)
    required = max(
        MINIMUM_FREE_MARGIN,
        int(manifest["total_logical_size"]) * 3 + current_size * 2,
    )
    _check_space(config.data_dir, required, "RESTORE_SPACE_INSUFFICIENT")
    instance_lock = InstanceLock(config.data_dir)
    if not instance_lock.acquire():
        raise MaintenanceError("RESTORE_APP_RUNNING", "Arrêtez Aurelia avant la restauration")
    restore_lock = config.data_dir / ".restore.lock"
    lock_handle = None
    swaps = []
    safety = None
    workspace = None
    document_candidate = None
    try:
        try:
            lock_handle = restore_lock.open("x", encoding="ascii")
            lock_handle.write(operation_id)
            lock_handle.flush()
        except FileExistsError as exc:
            raise MaintenanceError("RESTORE_LOCKED", "Une restauration est déjà en cours") from exc
        safety_dir = config.data_dir / "RestoreSafety"
        safety = create_complete_backup(
            config, destination_dir=safety_dir, retain=False, operation_label="pre_restore_backup"
        )
        workspace = Path(tempfile.mkdtemp(prefix=f".restore-{operation_id}-", dir=config.data_dir))
        extracted = workspace / "extracted"
        _extract_verified_backup(path, extracted)
        candidate_database = extracted / "database" / "aurelia_v5.db"
        if validation["database"]["schema_version"] < db.CURRENT_SCHEMA_VERSION:
            db.init_db(candidate_database)
        _database_details(candidate_database)

        candidate_data = workspace / "candidate-Data"
        candidate_config = workspace / "candidate-Config"
        shutil.copytree(extracted / "data", candidate_data) if (extracted / "data").exists() else candidate_data.mkdir()
        shutil.copytree(extracted / "config", candidate_config) if (extracted / "config").exists() else candidate_config.mkdir()
        config.documents_dir.parent.mkdir(parents=True, exist_ok=True)
        document_candidate = Path(tempfile.mkdtemp(
            prefix=f".{config.documents_dir.name}.candidate-{operation_id}-",
            dir=config.documents_dir.parent,
        ))
        if (extracted / "documents").exists():
            shutil.copytree(extracted / "documents", document_candidate, dirs_exist_ok=True)

        rollback_database = config.sqlite_path.with_name(f".{config.sqlite_path.name}.rollback-{operation_id}")
        rollback_data = config.internal_data_dir.with_name(f".{config.internal_data_dir.name}.rollback-{operation_id}")
        rollback_config = config.config_dir.with_name(f".{config.config_dir.name}.rollback-{operation_id}")
        rollback_documents = config.documents_dir.with_name(f".{config.documents_dir.name}.rollback-{operation_id}")

        _swap_path(config.sqlite_path, candidate_database, rollback_database, swaps)
        _swap_path(config.internal_data_dir, candidate_data, rollback_data, swaps)
        _swap_path(config.config_dir, candidate_config, rollback_config, swaps)
        if config.documents_dir.exists():
            os.replace(config.documents_dir, rollback_documents)
            backup_child_name = (
                config.backups_dir.name
                if config.backups_dir.parent.resolve() == config.documents_dir.resolve()
                else None
            )
            old_backups = rollback_documents / backup_child_name if backup_child_name else None
            if old_backups is not None and old_backups.exists():
                os.replace(old_backups, document_candidate / config.backups_dir.name)
            os.replace(document_candidate, config.documents_dir)
            swaps.append((config.documents_dir, rollback_documents, True, backup_child_name))
        else:
            os.replace(document_candidate, config.documents_dir)
            swaps.append((config.documents_dir, rollback_documents, False, None))
        document_candidate = None

        post_validation = _validate_restored_runtime(
            config, manifest["backup_id"], post_restore_validator
        )
        for _, rollback, _, _ in swaps:
            if rollback.is_dir():
                shutil.rmtree(rollback)
            else:
                rollback.unlink(missing_ok=True)
        _write_status(
            config, "restore", "success", operation_id=operation_id,
            backup_reference=Path(path).name, backup_id=manifest["backup_id"],
            safety_backup_reference=safety["path"].name,
            app_version=APP_VERSION, schema_version=db.CURRENT_SCHEMA_VERSION, failure_code=None,
        )
        logger.info(
            "maintenance operation_id=%s operation=restore result=success backup_id=%s",
            operation_id, manifest["backup_id"],
        )
        return {
            "operation_id": operation_id,
            "backup_id": manifest["backup_id"],
            "safety_backup": safety["path"],
            "validation": post_validation,
        }
    except Exception as exc:
        if swaps:
            try:
                _rollback_swaps(swaps)
            except Exception as rollback_exc:
                raise MaintenanceError("RESTORE_ROLLBACK_FAILED", "Rollback de restauration impossible") from rollback_exc
        code = exc.code if isinstance(exc, MaintenanceError) else "RESTORE_FAILED"
        _write_status(
            config, "restore", "failed", operation_id=operation_id,
            backup_reference=Path(path).name, backup_id=manifest.get("backup_id"),
            safety_backup_reference=safety["path"].name if safety else None,
            app_version=APP_VERSION, schema_version=None, failure_code=code,
        )
        logger.error(
            "maintenance operation_id=%s operation=restore result=failed code=%s",
            operation_id, code,
        )
        if isinstance(exc, MaintenanceError):
            raise
        raise MaintenanceError(code, "La restauration a échoué") from exc
    finally:
        if lock_handle is not None:
            lock_handle.close()
        restore_lock.unlink(missing_ok=True)
        if workspace is not None:
            shutil.rmtree(workspace, ignore_errors=True)
        if document_candidate is not None:
            shutil.rmtree(document_candidate, ignore_errors=True)
        instance_lock.release()
