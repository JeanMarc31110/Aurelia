import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


logger = logging.getLogger("aurelia.update")
MANIFEST_FORMAT_VERSION = 1
UPDATE_STATE_FORMAT_VERSION = 1
MAX_MANIFEST_BYTES = 1024 * 1024
VERSION_PATTERN = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
PRODUCTION_MODE = "production"
TEST_MODE = "test"


class UpdateError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class UpdateManifest:
    manifest_format_version: int
    aurelia_version: str
    release_timestamp: str
    package_url: str
    file_size: int
    sha256: str
    source_commit: str
    minimum_app_version: str
    minimum_schema_version: int
    maximum_schema_version: int
    target_schema_version: int
    rollback_schema_compatible: bool
    release_summary: str
    signature_required: bool
    publisher: str

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict):
            raise UpdateError("MANIFEST_INVALID", "Le manifeste de mise à jour doit être un objet JSON")
        required = {field.name for field in cls.__dataclass_fields__.values()}
        missing = sorted(required - set(value))
        if missing:
            raise UpdateError("MANIFEST_INVALID", f"Champs manquants dans le manifeste: {missing}")
        try:
            manifest = cls(**{name: value[name] for name in required})
        except (TypeError, ValueError) as exc:
            raise UpdateError("MANIFEST_INVALID", "Types invalides dans le manifeste") from exc
        manifest.validate()
        return manifest

    def validate(self):
        if self.manifest_format_version != MANIFEST_FORMAT_VERSION:
            raise UpdateError("MANIFEST_FORMAT_UNSUPPORTED", "Format de manifeste non pris en charge")
        parse_version(self.aurelia_version)
        parse_version(self.minimum_app_version)
        if not isinstance(self.file_size, int) or isinstance(self.file_size, bool) or self.file_size <= 0:
            raise UpdateError("MANIFEST_INVALID", "Taille de package invalide")
        if not SHA256_PATTERN.fullmatch(str(self.sha256).lower()):
            raise UpdateError("MANIFEST_INVALID", "Empreinte SHA-256 invalide")
        if not COMMIT_PATTERN.fullmatch(str(self.source_commit).lower()):
            raise UpdateError("MANIFEST_INVALID", "Commit source invalide")
        if not all(isinstance(item, int) and not isinstance(item, bool) and item >= 0 for item in (
            self.minimum_schema_version, self.maximum_schema_version, self.target_schema_version,
        )):
            raise UpdateError("MANIFEST_INVALID", "Compatibilité de schéma invalide")
        if self.minimum_schema_version > self.maximum_schema_version:
            raise UpdateError("MANIFEST_INVALID", "Plage de schéma invalide")
        if not isinstance(self.rollback_schema_compatible, bool) or not isinstance(self.signature_required, bool):
            raise UpdateError("MANIFEST_INVALID", "Politique de sécurité invalide")
        if not isinstance(self.release_summary, str) or len(self.release_summary) > 4000:
            raise UpdateError("MANIFEST_INVALID", "Résumé de version invalide")
        if not isinstance(self.publisher, str) or not self.publisher.strip():
            raise UpdateError("MANIFEST_INVALID", "Éditeur absent du manifeste")
        parsed = urllib.parse.urlparse(self.package_url)
        if parsed.scheme not in {"https", "http", "file"} or not parsed.path:
            raise UpdateError("MANIFEST_INVALID", "URL de package invalide")
        if any(part in {".", ".."} for part in Path(urllib.parse.unquote(parsed.path)).parts):
            raise UpdateError("MANIFEST_PATH_UNSAFE", "Chemin de package non sûr")
        try:
            datetime.fromisoformat(self.release_timestamp.replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise UpdateError("MANIFEST_INVALID", "Horodatage de version invalide") from exc

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class SignatureResult:
    status: str
    subject: str | None = None
    thumbprint: str | None = None


def parse_version(value):
    match = VERSION_PATTERN.fullmatch(str(value))
    if not match:
        raise UpdateError("VERSION_INVALID", f"Version Aurelia invalide: {value!r}")
    return tuple(int(part) for part in match.groups())


def compare_versions(left, right):
    return (parse_version(left) > parse_version(right)) - (parse_version(left) < parse_version(right))


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UpdateError("STATE_INVALID", f"État de mise à jour illisible: {Path(path).name}") from exc


def validate_manifest_for_install(manifest, current_version, current_schema, mode=PRODUCTION_MODE):
    manifest.validate()
    if compare_versions(manifest.aurelia_version, current_version) <= 0:
        code = "UPDATE_SAME_VERSION" if manifest.aurelia_version == current_version else "UPDATE_DOWNGRADE_REJECTED"
        raise UpdateError(code, "La version proposée doit être strictement plus récente")
    if compare_versions(current_version, manifest.minimum_app_version) < 0:
        raise UpdateError("APP_VERSION_UNSUPPORTED", "La version installée est trop ancienne pour cette mise à jour")
    if not manifest.minimum_schema_version <= current_schema <= manifest.maximum_schema_version:
        raise UpdateError("SCHEMA_INCOMPATIBLE", "Le schéma de données courant n'est pas compatible")
    if mode == PRODUCTION_MODE:
        if urllib.parse.urlparse(manifest.package_url).scheme != "https":
            raise UpdateError("TRANSPORT_INSECURE", "Le mode production exige une URL HTTPS")
        if not manifest.signature_required:
            raise UpdateError("SIGNATURE_POLICY_INVALID", "Le manifeste production doit exiger une signature")
    elif mode != TEST_MODE:
        raise UpdateError("UPDATE_MODE_INVALID", "Mode de mise à jour invalide")
    return manifest


def fetch_manifest(url, mode=PRODUCTION_MODE, opener=urllib.request.urlopen, timeout=15):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"https", "http", "file"}:
        raise UpdateError("MANIFEST_URL_INVALID", "Source de manifeste non prise en charge")
    if mode == PRODUCTION_MODE and parsed.scheme != "https":
        raise UpdateError("TRANSPORT_INSECURE", "Le mode production exige une source HTTPS")
    try:
        with opener(url, timeout=timeout) as response:
            final_url = getattr(response, "geturl", lambda: url)()
            if mode == PRODUCTION_MODE and urllib.parse.urlparse(final_url).scheme != "https":
                raise UpdateError("TRANSPORT_DOWNGRADE", "La source HTTPS a redirigé vers un transport non sûr")
            payload = response.read(MAX_MANIFEST_BYTES + 1)
    except Exception as exc:
        raise UpdateError("MANIFEST_DOWNLOAD_FAILED", "Téléchargement du manifeste impossible") from exc
    if len(payload) > MAX_MANIFEST_BYTES:
        raise UpdateError("MANIFEST_TOO_LARGE", "Manifeste trop volumineux")
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateError("MANIFEST_INVALID", "Manifeste JSON invalide") from exc
    return UpdateManifest.from_dict(value)


def authenticode_signature(path):
    if os.name != "nt":
        return SignatureResult("UNAVAILABLE")
    literal = str(Path(path).resolve()).replace("'", "''")
    script = (
        f"$s=Get-AuthenticodeSignature -LiteralPath '{literal}';"
        "$o=[ordered]@{status=$s.Status.ToString();subject=$null;thumbprint=$null};"
        "if($s.SignerCertificate){$o.subject=$s.SignerCertificate.Subject;$o.thumbprint=$s.SignerCertificate.Thumbprint};"
        "$o|ConvertTo-Json -Compress"
    )
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=30,
    )
    if completed.returncode:
        return SignatureResult("ERROR")
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return SignatureResult("ERROR")
    return SignatureResult(value.get("status", "ERROR"), value.get("subject"), value.get("thumbprint"))


def enforce_signature_policy(path, mode=PRODUCTION_MODE, allow_unsigned_test=False,
                             expected_publisher="FEWURA", allowed_thumbprints=(), verifier=authenticode_signature):
    result = verifier(path)
    if mode == TEST_MODE and allow_unsigned_test and result.status in {"NotSigned", "UNSIGNED", "UNAVAILABLE"}:
        logger.warning("update_unsigned_test_override package=%s", Path(path).name)
        return result
    if result.status != "Valid":
        raise UpdateError("SIGNATURE_REJECTED", f"Signature Authenticode refusée: {result.status}")
    if expected_publisher.casefold() not in (result.subject or "").casefold():
        raise UpdateError("SIGNER_IDENTITY_MISMATCH", "L'identité du signataire ne correspond pas à l'éditeur attendu")
    normalized = {str(item).replace(" ", "").upper() for item in allowed_thumbprints if str(item).strip()}
    if normalized and (result.thumbprint or "").replace(" ", "").upper() not in normalized:
        raise UpdateError("SIGNER_THUMBPRINT_MISMATCH", "Le certificat signataire n'est pas autorisé")
    return result


def _download(url, destination, expected_size, timeout=60):
    request = urllib.request.Request(url, headers={"User-Agent": "Aurelia-Updater/1"})
    written = 0
    with urllib.request.urlopen(request, timeout=timeout) as response, Path(destination).open("wb") as target:
        if urllib.parse.urlparse(url).scheme == "https" and urllib.parse.urlparse(response.geturl()).scheme != "https":
            raise UpdateError("TRANSPORT_DOWNGRADE", "Le téléchargement HTTPS a été redirigé vers un transport non sûr")
        while True:
            block = response.read(1024 * 1024)
            if not block:
                break
            written += len(block)
            if written > expected_size:
                raise UpdateError("PACKAGE_SIZE_MISMATCH", "Le package dépasse la taille annoncée")
            target.write(block)
        target.flush()
        os.fsync(target.fileno())


def stage_package(manifest, staging_root, mode=PRODUCTION_MODE, allow_unsigned_test=False,
                  expected_publisher="FEWURA", allowed_thumbprints=(), downloader=_download,
                  signature_verifier=authenticode_signature, disk_usage=shutil.disk_usage):
    root = Path(staging_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    available = disk_usage(root).free
    required = manifest.file_size * 2 + 10 * 1024 * 1024
    if available < required:
        raise UpdateError("DISK_SPACE_INSUFFICIENT", "Espace disque insuffisant pour préparer la mise à jour")
    update_id = uuid.uuid4().hex
    directory = root / update_id
    directory.mkdir()
    partial = directory / "package.exe.part"
    package = directory / "package.exe"
    try:
        downloader(manifest.package_url, partial, manifest.file_size)
        if not partial.is_file() or partial.stat().st_size != manifest.file_size:
            raise UpdateError("PACKAGE_SIZE_MISMATCH", "Taille du package téléchargé incorrecte")
        if sha256_file(partial) != manifest.sha256.lower():
            raise UpdateError("PACKAGE_HASH_MISMATCH", "Empreinte SHA-256 du package incorrecte")
        signature = enforce_signature_policy(
            partial, mode, allow_unsigned_test, expected_publisher, allowed_thumbprints, signature_verifier,
        )
        os.replace(partial, package)
        package.chmod(0o444)
        logger.info(
            "update_package_staged update_id=%s target_version=%s signature=%s",
            update_id, manifest.aurelia_version, signature.status,
        )
        return {"update_id": update_id, "directory": directory, "package": package, "signature": asdict(signature)}
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise


def verify_staged_package(package, manifest, mode, allow_unsigned_test, expected_publisher,
                          allowed_thumbprints=(), signature_verifier=authenticode_signature):
    package = Path(package)
    if not package.is_file() or package.stat().st_size != manifest.file_size:
        raise UpdateError("PACKAGE_SIZE_MISMATCH", "Le package préparé a été remplacé ou tronqué")
    if sha256_file(package) != manifest.sha256.lower():
        raise UpdateError("PACKAGE_HASH_MISMATCH", "Le package préparé a été modifié")
    return enforce_signature_policy(
        package, mode, allow_unsigned_test, expected_publisher, allowed_thumbprints, signature_verifier,
    )


def _safe_child(root, child):
    root = Path(root).resolve()
    child = Path(child).resolve()
    if child == root or root not in child.parents:
        raise UpdateError("PATH_UNSAFE", "Chemin de mise à jour hors de la zone autorisée")
    return child


def create_application_recovery_point(app_dir, recovery_root, version):
    parse_version(version)
    app_dir = Path(app_dir).resolve()
    recovery_root = Path(recovery_root).resolve()
    recovery_root.mkdir(parents=True, exist_ok=True)
    if not (app_dir / "Aurelia.exe").is_file():
        raise UpdateError("RECOVERY_SOURCE_INVALID", "Aurelia.exe est absent de l'installation courante")
    target = _safe_child(recovery_root, recovery_root / version)
    temporary = _safe_child(recovery_root, recovery_root / f".{version}.{uuid.uuid4().hex}.tmp")
    if temporary.exists():
        shutil.rmtree(temporary)
    shutil.copytree(app_dir, temporary)
    if not (temporary / "Aurelia.exe").is_file():
        shutil.rmtree(temporary, ignore_errors=True)
        raise UpdateError("RECOVERY_COPY_INVALID", "Le point de récupération est incomplet")
    if target.exists():
        shutil.rmtree(target)
    os.replace(temporary, target)
    for candidate in recovery_root.iterdir():
        if candidate.is_dir() and candidate != target and VERSION_PATTERN.fullmatch(candidate.name):
            shutil.rmtree(_safe_child(recovery_root, candidate))
    logger.info("update_recovery_created from_version=%s", version)
    return target


def restore_application(recovery_dir, live_app_dir):
    recovery_dir = Path(recovery_dir).resolve()
    live_app_dir = Path(live_app_dir).resolve()
    if not (recovery_dir / "Aurelia.exe").is_file():
        raise UpdateError("ROLLBACK_SOURCE_INVALID", "Le point de récupération applicatif est invalide")
    parent = live_app_dir.parent
    failed = _safe_child(parent, parent / f".{live_app_dir.name}.failed-{uuid.uuid4().hex}")
    if live_app_dir.exists():
        os.replace(live_app_dir, failed)
    try:
        shutil.copytree(recovery_dir, live_app_dir)
        if not (live_app_dir / "Aurelia.exe").is_file():
            raise UpdateError("ROLLBACK_COPY_INVALID", "La restauration applicative est incomplète")
    except Exception:
        shutil.rmtree(live_app_dir, ignore_errors=True)
        if failed.exists():
            os.replace(failed, live_app_dir)
        raise
    return failed


def prune_failed_application(path):
    path = Path(path)
    if path.exists():
        shutil.rmtree(path)


def read_update_state(path):
    path = Path(path)
    if not path.is_file():
        return None
    value = load_json(path)
    if value.get("state_format_version") != UPDATE_STATE_FORMAT_VERSION:
        raise UpdateError("STATE_FORMAT_UNSUPPORTED", "Format d'état de mise à jour non pris en charge")
    return value


def validate_update_state_paths(state_path, state):
    update_root = Path(state_path).resolve().parent
    package = Path(state["package_path"]).resolve()
    recovery = Path(state["recovery_dir"]).resolve()
    live_app = Path(state["live_app_dir"]).resolve()
    if update_root not in package.parents or update_root not in recovery.parents:
        raise UpdateError("STATE_PATH_UNSAFE", "Le package ou le point de récupération sort de la zone de mise à jour")
    if live_app == update_root or update_root in live_app.parents or live_app in update_root.parents:
        raise UpdateError("STATE_PATH_UNSAFE", "Les cycles application et mise à jour doivent rester séparés")
    return package, recovery, live_app


def update_state(path, state, status, **fields):
    value = dict(state)
    value.update(fields)
    value["status"] = status
    value["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
    atomic_write_json(path, value)
    return value


def prepare_update(manifest, current_version, current_schema, app_dir, update_root, data_backup_creator,
                   mode=PRODUCTION_MODE, allow_unsigned_test=False, expected_publisher="FEWURA",
                   allowed_thumbprints=(), downloader=_download, signature_verifier=authenticode_signature,
                   disk_usage=shutil.disk_usage):
    validate_manifest_for_install(manifest, current_version, current_schema, mode)
    update_root = Path(update_root).resolve()
    staged = stage_package(
        manifest, update_root / "staging", mode, allow_unsigned_test, expected_publisher,
        allowed_thumbprints, downloader, signature_verifier, disk_usage,
    )
    try:
        backup = data_backup_creator()
        recovery = create_application_recovery_point(app_dir, update_root / "recovery", current_version)
    except Exception:
        shutil.rmtree(staged["directory"], ignore_errors=True)
        raise
    state_path = update_root / "update-state.json"
    state = {
        "state_format_version": UPDATE_STATE_FORMAT_VERSION,
        "update_id": staged["update_id"],
        "status": "READY_TO_INSTALL",
        "from_version": current_version,
        "target_version": manifest.aurelia_version,
        "manifest": manifest.to_dict(),
        "package_path": str(staged["package"]),
        "package_sha256": manifest.sha256.lower(),
        "live_app_dir": str(Path(app_dir).resolve()),
        "recovery_dir": str(recovery),
        "pre_update_schema_version": current_schema,
        "data_backup_reference": str(backup),
        "mode": mode,
        "allow_unsigned_test": bool(allow_unsigned_test),
        "expected_publisher": expected_publisher,
        "allowed_thumbprints": list(allowed_thumbprints),
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    atomic_write_json(state_path, state)
    logger.info("update_ready update_id=%s from_version=%s target_version=%s", staged["update_id"], current_version, manifest.aurelia_version)
    return state_path, state


def default_installer_runner(package, live_app_dir):
    completed = subprocess.run([
        str(package), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", f"/DIR={live_app_dir}",
    ], timeout=900)
    return completed.returncode


def critical_assets_present(app_dir):
    app_dir = Path(app_dir)
    required = (
        app_dir / "Aurelia.exe",
        app_dir / "_internal" / "VERSION.txt",
        app_dir / "_internal" / "app" / "templates" / "login.html",
        app_dir / "_internal" / "app" / "static" / "style.css",
    )
    return all(path.is_file() for path in required)


def default_launcher(app_dir):
    environment = os.environ.copy()
    environment["AURELIA_NO_BROWSER"] = "1"
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.Popen([str(Path(app_dir) / "Aurelia.exe")], cwd=app_dir, env=environment, creationflags=flags)


def default_health_checker(process, expected_version, timeout=35):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        try:
            with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=.8) as response:
                payload = json.loads(response.read().decode("utf-8"))
                if response.status == 200 and payload.get("app") == "aurelia" and payload.get("version") == expected_version:
                    with urllib.request.urlopen("http://127.0.0.1:8000/login", timeout=.8) as state_response:
                        return state_response.status in {200, 303}
        except Exception:
            time.sleep(.25)
    return False


def execute_prepared_update(state_path, schema_reader, installer_runner=default_installer_runner,
                            launcher=default_launcher, health_checker=default_health_checker,
                            signature_verifier=authenticode_signature):
    state_path = Path(state_path)
    state = read_update_state(state_path)
    if state.get("status") not in {"READY_TO_INSTALL", "INSTALLING"}:
        raise UpdateError("STATE_NOT_INSTALLABLE", "La mise à jour n'est pas prête à être installée")
    manifest = UpdateManifest.from_dict(state["manifest"])
    package,recovery,live_app=validate_update_state_paths(state_path,state)
    verify_staged_package(
        package, manifest, state["mode"], state["allow_unsigned_test"], state["expected_publisher"],
        state.get("allowed_thumbprints", ()), signature_verifier,
    )
    state = update_state(state_path, state, "INSTALLING")
    logger.info("update_installing update_id=%s target_version=%s", state["update_id"], state["target_version"])
    process = None
    failure_code = None
    try:
        if installer_runner(package, live_app) != 0:
            failure_code = "INSTALLER_FAILED"
            raise UpdateError(failure_code, "L'installateur de mise à jour a échoué")
        if not critical_assets_present(live_app):
            failure_code = "CRITICAL_ASSET_MISSING"
            raise UpdateError(failure_code, "Des ressources critiques manquent après installation")
        process = launcher(live_app)
        if not health_checker(process, manifest.aurelia_version):
            failure_code = "HEALTH_CHECK_FAILED"
            raise UpdateError(failure_code, "La nouvelle version ne répond pas correctement")
        state = update_state(state_path, state, "COMPLETED", health_result="pass", installed_version=manifest.aurelia_version)
        logger.info("update_completed update_id=%s target_version=%s", state["update_id"], manifest.aurelia_version)
        return state
    except Exception as exc:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        current_schema = schema_reader()
        schema_changed = current_schema != state["pre_update_schema_version"]
        if schema_changed and not manifest.rollback_schema_compatible:
            state = update_state(
                state_path, state, "RECOVERY_REQUIRED", failure_code=failure_code or getattr(exc, "code", "UPDATE_FAILED"),
                current_schema_version=current_schema, automatic_rollback="prevented_incompatible_schema",
            )
            logger.error("update_recovery_required update_id=%s schema=%s", state["update_id"], current_schema)
            return state
        failed_app = None
        try:
            failed_app = restore_application(recovery, live_app)
            rollback_process = launcher(live_app)
            if not health_checker(rollback_process, state["from_version"]):
                raise UpdateError("ROLLBACK_HEALTH_FAILED", "La version restaurée ne répond pas correctement")
            prune_failed_application(failed_app)
            state = update_state(
                state_path, state, "ROLLED_BACK", failure_code=failure_code or getattr(exc, "code", "UPDATE_FAILED"),
                rollback_result="pass", restored_version=state["from_version"], current_schema_version=current_schema,
            )
            logger.warning("update_rolled_back update_id=%s restored_version=%s", state["update_id"], state["from_version"])
            return state
        except Exception as rollback_exc:
            state = update_state(
                state_path, state, "ROLLBACK_FAILED", failure_code=failure_code or getattr(exc, "code", "UPDATE_FAILED"),
                rollback_error=type(rollback_exc).__name__, current_schema_version=current_schema,
            )
            logger.exception("update_rollback_failed update_id=%s", state["update_id"])
            return state


def update_mode_from_environment():
    return os.getenv("AURELIA_UPDATE_MODE", PRODUCTION_MODE).strip().lower()


def unsigned_test_override_from_environment():
    return os.getenv("AURELIA_ALLOW_UNSIGNED_UPDATES", "").strip().lower() in {"1", "true", "yes", "on"}


def configured_thumbprints():
    return tuple(item.strip() for item in os.getenv("AURELIA_UPDATE_SIGNER_THUMBPRINTS", "").split(",") if item.strip())


def update_root_for_config(config):
    return Path(config.data_dir) / "Updates"


def current_update_status(config):
    state_path = update_root_for_config(config) / "update-state.json"
    state = read_update_state(state_path) if state_path.is_file() else None
    return state or {"status": "UP_TO_DATE"}


def check_configured_update(config, current_version, current_schema):
    url = os.getenv("AURELIA_UPDATE_MANIFEST_URL", "").strip()
    if not url:
        raise UpdateError("UPDATE_SOURCE_NOT_CONFIGURED", "Aucune source de mise à jour n'est configurée")
    mode = update_mode_from_environment()
    manifest = fetch_manifest(url, mode)
    validate_manifest_for_install(manifest, current_version, current_schema, mode)
    root = update_root_for_config(config)
    root.mkdir(parents=True, exist_ok=True)
    atomic_write_json(root / "available-manifest.json", manifest.to_dict())
    state_path = root / "update-state.json"
    state = {
        "state_format_version": UPDATE_STATE_FORMAT_VERSION,
        "update_id": uuid.uuid4().hex,
        "status": "AVAILABLE",
        "from_version": current_version,
        "target_version": manifest.aurelia_version,
        "manifest": manifest.to_dict(),
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    atomic_write_json(state_path, state)
    logger.info("update_available current_version=%s target_version=%s", current_version, manifest.aurelia_version)
    return state


def prepare_configured_update(config, current_version, current_schema, data_backup_creator):
    root = update_root_for_config(config)
    manifest_path = root / "available-manifest.json"
    if not manifest_path.is_file():
        raise UpdateError("UPDATE_NOT_CHECKED", "Vérifiez d'abord les mises à jour disponibles")
    manifest = UpdateManifest.from_dict(load_json(manifest_path))
    mode = update_mode_from_environment()
    allow_unsigned = unsigned_test_override_from_environment()
    if allow_unsigned and mode != TEST_MODE:
        raise UpdateError("UNSIGNED_OVERRIDE_FORBIDDEN", "L'override non signé est réservé au mode test")
    return prepare_update(
        manifest, current_version, current_schema, config.program_dir, root, data_backup_creator,
        mode=mode, allow_unsigned_test=allow_unsigned,
        expected_publisher=os.getenv("AURELIA_UPDATE_EXPECTED_PUBLISHER", "FEWURA").strip() or "FEWURA",
        allowed_thumbprints=configured_thumbprints(),
    )


def launch_update_helper(state_path, parent_pid):
    state = read_update_state(state_path)
    helper = Path(state["recovery_dir"]) / "Aurelia.exe"
    if not helper.is_file():
        raise UpdateError("UPDATE_HELPER_MISSING", "Le helper de mise à jour est absent du point de récupération")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    subprocess.Popen(
        [str(helper), "--apply-update", str(Path(state_path).resolve()), "--parent-pid", str(parent_pid)],
        cwd=helper.parent, env=os.environ.copy(), creationflags=flags, close_fds=True,
    )
    logger.info("update_helper_launched update_id=%s target_version=%s", state["update_id"], state["target_version"])
