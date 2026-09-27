import argparse
import ast
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
LOCKFILE = ROOT / "requirements.lock.txt"
BUILD_REQUIREMENTS = ROOT / "requirements-build.txt"
SPEC = ROOT / "installer" / "aurelia.spec"
ISS = ROOT / "installer" / "Aurelia.iss"
RELEASE_ROOT = ROOT / "release"
REQUIRED_LANGUAGES = ("eng", "fra", "spa", "osd")
FORBIDDEN_PARTS = {"test", "tests", "samples", "examples", ".git", "__pycache__", "backups", "captures", "screenshots"}
FORBIDDEN_FILENAMES = {".env", "session.secret"}
FORBIDDEN_DEVELOPMENT_FILENAMES = {"test.py", "testutils.py", "testdrawings.py", "testshapes.py"}
FORBIDDEN_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".log", ".pem", ".key"}
ALLOWED_SECURITY_RESOURCES = {"_internal/certifi/cacert.pem"}
TEXT_SUFFIXES = {".txt", ".json", ".html", ".css", ".js", ".xml", ".ini", ".cfg", ".md"}


class BuildError(RuntimeError):
    pass


def run(command, cwd=ROOT, env=None, capture=False, timeout=None):
    result = subprocess.run(
        [str(part) for part in command], cwd=cwd, env=env, text=True,
        capture_output=capture, timeout=timeout,
    )
    if result.returncode:
        output = ((result.stdout or "") + (result.stderr or "")).strip()
        raise BuildError(f"Commande échouée ({result.returncode}): {' '.join(map(str, command))}\n{output}")
    return result


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_product_version(root=ROOT):
    value = (Path(root) / "VERSION.txt").read_text(encoding="utf-8-sig").strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", value):
        raise BuildError("VERSION.txt doit contenir une version X.Y.Z")
    return value


def expected_python_version(lockfile=LOCKFILE):
    first_lines = Path(lockfile).read_text(encoding="utf-8").splitlines()[:5]
    match = re.search(r"Python\s+(\d+\.\d+\.\d+)", "\n".join(first_lines))
    if not match:
        raise BuildError("Version Python absente de requirements.lock.txt")
    return match.group(1)


def parse_pinned_requirements(path=BUILD_REQUIREMENTS, seen=None):
    path = Path(path).resolve()
    seen = set() if seen is None else seen
    if path in seen:
        return {}
    seen.add(path)
    requirements = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("-r "):
            requirements.update(parse_pinned_requirements(path.parent / line[3:].strip(), seen))
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([^\s]+)", line)
        if not match:
            raise BuildError(f"Dépendance de build non verrouillée: {line}")
        requirements[re.sub(r"[-_.]+", "-", match.group(1)).lower()] = match.group(2)
    return requirements


def verify_dependencies(requirements=None):
    requirements = requirements or parse_pinned_requirements()
    installed = {
        re.sub(r"[-_.]+", "-", distribution.metadata["Name"]).lower(): distribution.version
        for distribution in importlib.metadata.distributions()
        if distribution.metadata.get("Name")
    }
    mismatches = {
        name: {"expected": version, "actual": installed.get(name)}
        for name, version in requirements.items()
        if installed.get(name) != version
    }
    if mismatches:
        raise BuildError(f"Environnement différent du lockfile: {json.dumps(mismatches, sort_keys=True)}")
    return {name: requirements[name] for name in sorted(requirements)}


def schema_version_from_source(path=None):
    path = Path(path or (ROOT / "app" / "db.py"))
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "CURRENT_SCHEMA_VERSION":
                    return int(ast.literal_eval(node.value))
    raise BuildError("CURRENT_SCHEMA_VERSION introuvable")


def canonical_publisher(path=ISS):
    content = Path(path).read_text(encoding="utf-8-sig")
    match = re.search(r'^#define\s+MyAppPublisher\s+"([^"]+)"', content, re.MULTILINE)
    if not match:
        raise BuildError("Publisher canonique absent de installer/Aurelia.iss")
    return match.group(1)


def git_output(*arguments):
    return run(["git", "-c", f"safe.directory={ROOT}", "-C", ROOT, *arguments], capture=True).stdout.strip()


def verify_source_tree(allow_dirty=False):
    status = git_output("status", "--porcelain", "--untracked-files=all")
    if status and not allow_dirty:
        raise BuildError("Le build de release exige un dépôt Git propre")
    tracked = set(git_output("ls-files").splitlines())
    required = {
        "aurelia_launcher.py", "VERSION.txt", "requirements.lock.txt",
        "installer/aurelia.spec", "resources/tesseract/tesseract.exe",
        *{f"resources/tesseract/tessdata/{language}.traineddata" for language in REQUIRED_LANGUAGES},
    }
    missing = sorted(required - tracked)
    if missing:
        raise BuildError(f"Ressources non suivies par Git: {missing}")
    return {"commit": git_output("rev-parse", "HEAD"), "dirty": bool(status)}


def generate_version_file(version, publisher, destination):
    numbers = [int(part) for part in version.split(".")] + [0]
    file_version = ", ".join(map(str, numbers))
    content = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers=({file_version}), prodvers=({file_version}),
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', '{publisher}'),
      StringStruct('FileDescription', 'Aurelia - facturation et pre-comptabilite locale'),
      StringStruct('FileVersion', '{version}.0'),
      StringStruct('InternalName', 'Aurelia'),
      StringStruct('OriginalFilename', 'Aurelia.exe'),
      StringStruct('ProductName', 'Aurelia'),
      StringStruct('ProductVersion', '{version}.0')
    ])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)\n"""
    Path(destination).write_text(content, encoding="utf-8", newline="\n")
    return Path(destination)


def safe_remove_tree(path):
    path = Path(path).resolve()
    release = RELEASE_ROOT.resolve()
    if path == release or release not in path.parents:
        raise BuildError(f"Suppression refusée hors staging release: {path}")
    if path.exists():
        shutil.rmtree(path)


def file_inventory(root):
    root = Path(root)
    files = sorted(path for path in root.rglob("*") if path.is_file())
    return {
        path.relative_to(root).as_posix(): {"size": path.stat().st_size, "sha256": sha256_file(path)}
        for path in files
    }


def required_bundle_paths():
    required = {
        "Aurelia.exe",
        "_internal/VERSION.txt",
        "_internal/app/templates/login.html",
        "_internal/app/templates/dashboard.html",
        "_internal/app/static/style.css",
        "_internal/config/account_mapping.json",
        "_internal/config/policy.json",
        "_internal/resources/tesseract/tesseract.exe",
    }
    required.update(
        f"_internal/resources/tesseract/tessdata/{language}.traineddata"
        for language in REQUIRED_LANGUAGES
    )
    return required


def inspect_bundle(bundle, developer_markers=None):
    bundle = Path(bundle)
    inventory = file_inventory(bundle)
    missing = sorted(required_bundle_paths() - set(inventory))
    if missing:
        raise BuildError(f"Ressources absentes du bundle: {missing}")
    forbidden = []
    for relative in inventory:
        path = PurePosixPath(relative)
        lowered_parts = {part.lower() for part in path.parts}
        name = path.name.lower()
        suffix = path.suffix.lower()
        if (
            lowered_parts & FORBIDDEN_PARTS or name in FORBIDDEN_FILENAMES
            or name in FORBIDDEN_DEVELOPMENT_FILENAMES
            or (suffix in FORBIDDEN_SUFFIXES and relative not in ALLOWED_SECURITY_RESOURCES)
        ):
            forbidden.append(relative)
    if forbidden:
        raise BuildError(f"Fichiers interdits dans le bundle: {forbidden[:20]}")
    markers = [marker.lower() for marker in (developer_markers or []) if marker]
    text_leaks = []
    binary_metadata_hits = []
    for relative in inventory:
        path = bundle / Path(*PurePosixPath(relative).parts)
        content = path.read_bytes()
        lower = content.lower()
        hits = [marker for marker in markers if marker.encode("utf-8", errors="ignore") in lower]
        if not hits:
            continue
        if path.suffix.lower() in TEXT_SUFFIXES:
            text_leaks.append(relative)
        else:
            binary_metadata_hits.append(relative)
    if text_leaks:
        raise BuildError(f"Chemin développeur présent dans des ressources texte: {text_leaks}")
    largest = sorted(
        ({"path": relative, "size": details["size"]} for relative, details in inventory.items()),
        key=lambda row: row["size"], reverse=True,
    )[:10]
    tesseract_size = sum(
        details["size"] for relative, details in inventory.items()
        if relative.startswith("_internal/resources/tesseract/")
    )
    python_size = sum(
        details["size"] for relative, details in inventory.items()
        if "python" in PurePosixPath(relative).name.lower() or PurePosixPath(relative).name == "base_library.zip"
    )
    return {
        "file_count": len(inventory),
        "total_size": sum(details["size"] for details in inventory.values()),
        "largest_files": largest,
        "tesseract_size": tesseract_size,
        "python_runtime_size": python_size,
        "forbidden_files": forbidden,
        "text_path_leaks": text_leaks,
        "binary_build_metadata_hits": binary_metadata_hits,
        "inventory": inventory,
    }


def windows_version_metadata(executable):
    escaped = str(Path(executable).resolve()).replace("'", "''")
    command = (
        f"$v=(Get-Item -LiteralPath '{escaped}').VersionInfo; "
        "$v | Select-Object FileVersion,ProductVersion,ProductName,CompanyName,FileDescription,OriginalFilename | ConvertTo-Json -Compress"
    )
    result = run(["powershell.exe", "-NoProfile", "-Command", command], capture=True)
    return json.loads(result.stdout)


def verify_windows_metadata(executable, version, publisher):
    metadata = windows_version_metadata(executable)
    expected_version = f"{version}.0"
    checks = {
        "FileVersion": expected_version,
        "ProductVersion": expected_version,
        "ProductName": "Aurelia",
        "CompanyName": publisher,
        "OriginalFilename": "Aurelia.exe",
    }
    mismatches = {key: {"expected": expected, "actual": metadata.get(key)} for key, expected in checks.items() if metadata.get(key) != expected}
    if mismatches:
        raise BuildError(f"Métadonnées Windows invalides: {mismatches}")
    return metadata


def tesseract_details(bundle):
    executable = Path(bundle) / "_internal" / "resources" / "tesseract" / "tesseract.exe"
    environment = os.environ.copy()
    environment["TESSDATA_PREFIX"] = str(executable.parent / "tessdata")
    version = run([executable, "--version"], env=environment, capture=True).stdout.splitlines()[0].strip()
    languages_output = run([executable, "--list-langs"], env=environment, capture=True).stdout.splitlines()
    languages = sorted(line.strip() for line in languages_output if line.strip() in REQUIRED_LANGUAGES)
    if languages != sorted(REQUIRED_LANGUAGES):
        raise BuildError(f"Langues OCR incomplètes: {languages}")
    return {"version": version, "languages": languages}


def wait_health(base_url, timeout=25):
    import requests
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            response = requests.get(f"{base_url}/health", timeout=.5)
            if response.status_code == 200:
                return response
        except requests.RequestException:
            time.sleep(.15)
    raise BuildError("Le binaire n'a pas répondu à /health")


def smoke_binary(bundle, runtime_root, functional=True, mono_instance=True):
    import requests
    bundle = Path(bundle).resolve()
    runtime_root = Path(runtime_root).resolve()
    if runtime_root.exists():
        shutil.rmtree(runtime_root)
    environment = os.environ.copy()
    environment.update({
        "AURELIA_RUNTIME_ROOT": str(runtime_root),
        "AURELIA_WATCHER_ENABLED": "0",
        "AURELIA_BACKUPS_ENABLED": "1",
        "AURELIA_NO_BROWSER": "1",
    })
    executable = bundle / "Aurelia.exe"
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen([executable], cwd=bundle, env=environment, creationflags=flags)
    second = None
    base = "http://127.0.0.1:8000"
    password = f"Synthetic-{uuid.uuid4().hex}!"
    try:
        health = wait_health(base)
        if mono_instance:
            second = subprocess.Popen([executable], cwd=bundle, env=environment, creationflags=flags)
            second.wait(timeout=10)
            if second.returncode != 0:
                raise BuildError(f"La seconde instance a retourné {second.returncode}")
        session = requests.Session()
        setup_page = session.get(f"{base}/setup", timeout=5)
        setup = session.post(f"{base}/setup", data={
            "username": "portable-admin", "password": password, "password_confirmation": password,
            "legal_name": "Portable Synthetic Company", "tax_id": "PORTABLE-SYNTH",
            "country": "FR", "currency": "EUR",
        }, allow_redirects=False, timeout=5)
        login = session.post(f"{base}/login", data={
            "username": "portable-admin", "password": password,
        }, allow_redirects=False, timeout=5)
        page_responses = {path: session.get(f"{base}{path}", timeout=5) for path in ("/", "/work", "/settings", "/settings/ocr")}
        pages = {path: response.status_code for path, response in page_responses.items()}
        functional_result = None
        if functional:
            ubl = b'''<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2" xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2" xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"><cbc:ID>BUILD-SMOKE-001</cbc:ID><cbc:IssueDate>2026-09-26</cbc:IssueDate><cbc:DocumentCurrencyCode>EUR</cbc:DocumentCurrencyCode><cac:AccountingSupplierParty><cac:Party><cac:PartyName><cbc:Name>BUILD SUPPLIER</cbc:Name></cac:PartyName></cac:Party></cac:AccountingSupplierParty><cac:AccountingCustomerParty><cac:Party><cac:PartyName><cbc:Name>Portable Synthetic Company</cbc:Name></cac:PartyName></cac:Party></cac:AccountingCustomerParty><cac:InvoiceLine><cbc:ID>1</cbc:ID><cbc:InvoicedQuantity>1</cbc:InvoicedQuantity><cbc:LineExtensionAmount>100.00</cbc:LineExtensionAmount><cac:Item><cbc:Description>Build smoke</cbc:Description></cac:Item><cac:Price><cbc:PriceAmount>100.00</cbc:PriceAmount></cac:Price></cac:InvoiceLine><cac:TaxTotal><cbc:TaxAmount>20.00</cbc:TaxAmount></cac:TaxTotal><cac:LegalMonetaryTotal><cbc:TaxExclusiveAmount>100.00</cbc:TaxExclusiveAmount><cbc:PayableAmount>120.00</cbc:PayableAmount></cac:LegalMonetaryTotal></Invoice>'''
            upload = session.post(f"{base}/api/invoices/upload", files={"file": ("build-smoke.ubl", ubl, "application/xml")}, timeout=10)
            payload = upload.json()
            invoice_id = payload.get("decision", {}).get("invoice_id")
            review = session.get(f"{base}/invoices/{invoice_id}", timeout=5) if invoice_id else None
            approval = session.post(f"{base}/invoices/{invoice_id}/approve", data={"comment": "synthetic build smoke"}, allow_redirects=False, timeout=5) if invoice_id else None
            runtime_database = runtime_root / "state" / "aurelia_v5.db"
            with sqlite3.connect(runtime_database) as connection:
                customer_id = connection.execute(
                    "INSERT INTO customers(name,email) VALUES(?,?)",
                    ("Portable Synthetic Customer", "synthetic@example.invalid"),
                ).lastrowid
            outbound = session.post(f"{base}/api/outbound", data={
                "customer_id": customer_id, "description": "Synthetic portable build",
                "net_amount": "100.00", "vat_rate": "20", "due_days": "30",
            }, timeout=10)
            outbound_payload = outbound.json()
            generated_paths = [Path(outbound_payload[key]).resolve() for key in ("pdf_path", "xml_path")]
            functional_result = {
                "upload": upload.status_code,
                "decision": payload.get("decision", {}).get("status"),
                "review": review.status_code if review else None,
                "approval": approval.status_code if approval else None,
                "outbound": outbound.status_code,
                "generated_documents": [str(path.relative_to(runtime_root)) for path in generated_paths],
                "generated_documents_exist": all(path.is_file() for path in generated_paths),
                "generated_documents_outside_app": all(bundle not in path.parents for path in generated_paths),
            }
        shutdown = session.post(f"{base}/shutdown", timeout=5)
        process.wait(timeout=15)
        if process.returncode != 0:
            raise BuildError(f"Aurelia.exe a retourné {process.returncode}")
        with socket.socket() as probe:
            probe.settimeout(.5)
            port_released = probe.connect_ex(("127.0.0.1", 8000)) != 0
        result = {
            "health": health.status_code,
            "setup_page": setup_page.status_code,
            "setup": setup.status_code,
            "login": login.status_code,
            "pages": pages,
            "ocr_page_reports_available": "Disponible" in page_responses["/settings/ocr"].text,
            "functional": functional_result,
            "shutdown": shutdown.status_code,
            "port_released": port_released,
            "runtime_database": "state/aurelia_v5.db",
            "backup_files": [str(path.relative_to(runtime_root)) for path in sorted((runtime_root / "documents" / "Backups").glob("*.db"))],
            "app_directory_runtime_residue": sorted(
                path.relative_to(bundle).as_posix()
                for path in bundle.rglob("*")
                if path.is_file() and path.suffix.lower() in {".db", ".log", ".lock"}
            ),
        }
        if health.status_code != 200 or setup_page.status_code != 200 or setup.status_code != 303 or login.status_code != 303:
            raise BuildError(f"Smoke HTTP incomplet: {result}")
        if any(status != 200 for status in pages.values()) or not result["ocr_page_reports_available"] or not port_released or result["app_directory_runtime_residue"]:
            raise BuildError(f"Smoke pages/arrêt incomplet: {result}")
        if functional and (
            functional_result["upload"] != 200 or functional_result["review"] != 200
            or functional_result["approval"] != 303 or functional_result["outbound"] != 200
            or not functional_result["generated_documents_exist"]
            or not functional_result["generated_documents_outside_app"]
        ):
            raise BuildError(f"Smoke fonctionnel incomplet: {result}")
        if not result["backup_files"]:
            raise BuildError(f"Aucune sauvegarde SQLite créée sous le runtime isolé: {result}")
        return result
    finally:
        if second is not None and second.poll() is None:
            second.kill(); second.wait(timeout=5)
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=5)


def build_once(index, version_file, source_epoch):
    work = RELEASE_ROOT / ".work" / f"build-{index}"
    safe_remove_tree(work)
    work.mkdir(parents=True)
    environment = os.environ.copy()
    environment.update({
        "AURELIA_VERSION_FILE": str(version_file),
        "PYTHONHASHSEED": "0",
        "SOURCE_DATE_EPOCH": str(source_epoch),
    })
    run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--workpath", work / "pyinstaller", "--distpath", work / "dist", SPEC,
    ], env=environment)
    bundle = work / "dist" / "Aurelia"
    if not bundle.is_dir():
        raise BuildError("PyInstaller n'a pas produit le dossier onedir Aurelia")
    return bundle


def dependency_inventory(packages, tesseract):
    components = []
    for name, version in packages.items():
        metadata = importlib.metadata.metadata(name)
        components.append({
            "name": metadata.get("Name", name),
            "version": version,
            "license": metadata.get("License-Expression") or metadata.get("License") or "unknown",
            "type": "python-package",
        })
    components.append({
        "name": "Tesseract OCR",
        "version": tesseract["version"],
        "license": "Apache-2.0",
        "type": "bundled-native-component",
        "languages": tesseract["languages"],
    })
    return {"format": "Aurelia third-party components v1", "components": components}


def make_build_manifest(**values):
    required = {
        "aurelia_version", "source_commit_sha", "build_utc", "python_version",
        "pyinstaller_version", "requirements_lock_sha256", "build_spec_sha256",
        "executable_sha256", "file_count", "total_artifact_size",
        "tesseract_version", "database_schema_version", "build_mode",
    }
    missing = required - set(values)
    if missing:
        raise BuildError(f"Champs manifest absents: {sorted(missing)}")
    return {"build_manifest_version": 1, **values}


def create_portable_archive(release_directory, archive_path):
    release_directory = Path(release_directory)
    archive_path = Path(archive_path)
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(item for item in release_directory.rglob("*") if item.is_file()):
            archive.write(path, f"{release_directory.name}/{path.relative_to(release_directory).as_posix()}")
    return archive_path


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build portable Windows Aurelia")
    parser.add_argument("--allow-dirty", action="store_true", help="Development validation only")
    parser.add_argument("--install-dependencies", action="store_true")
    parser.add_argument("--build-count", type=int, default=2, choices=(1, 2))
    args = parser.parse_args(argv)

    source = verify_source_tree(args.allow_dirty)
    version = read_product_version()
    expected_python = expected_python_version()
    actual_python = platform.python_version()
    if actual_python != expected_python:
        raise BuildError(f"Python {expected_python} requis, trouvé {actual_python}")
    if args.install_dependencies:
        run([sys.executable, "-m", "pip", "install", "-r", BUILD_REQUIREMENTS])
    packages = verify_dependencies()
    pyinstaller_version = importlib.metadata.version("pyinstaller")
    publisher = canonical_publisher()
    run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"])

    work_root = RELEASE_ROOT / ".work"
    release_directory = RELEASE_ROOT / f"Aurelia-{version}"
    portable_archive = RELEASE_ROOT / f"Aurelia-{version}-portable.zip"
    safe_remove_tree(work_root)
    safe_remove_tree(release_directory)
    portable_archive.unlink(missing_ok=True)
    work_root.mkdir(parents=True, exist_ok=True)
    version_file = generate_version_file(version, publisher, work_root / "windows-version.txt")
    source_epoch = int(git_output("show", "-s", "--format=%ct", "HEAD"))
    developer_markers = [str(Path.home()), str(ROOT), "Desktop\\Aurélia", "Desktop/Aurélia"]

    builds = []
    for index in range(1, args.build_count + 1):
        bundle = build_once(index, version_file, source_epoch)
        inspection = inspect_bundle(bundle, developer_markers)
        metadata = verify_windows_metadata(bundle / "Aurelia.exe", version, publisher)
        ocr = tesseract_details(bundle)
        smoke = smoke_binary(bundle, work_root / f"runtime-{index}", functional=index == 1)
        builds.append({
            "bundle": bundle,
            "inspection": inspection,
            "metadata": metadata,
            "ocr": ocr,
            "smoke": smoke,
        })

    reproducibility = "SINGLE_BUILD_ONLY"
    if len(builds) == 2:
        first_inventory = builds[0]["inspection"]["inventory"]
        second_inventory = builds[1]["inspection"]["inventory"]
        if set(first_inventory) != set(second_inventory):
            raise BuildError("Les deux builds n'ont pas la même disposition de fichiers")
        reproducibility = (
            "BIT-REPRODUCIBLE" if first_inventory == second_inventory
            else "FUNCTIONALLY REPRODUCIBLE"
        )

    relocation = work_root / "relocation" / "Aurelia"
    relocation.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(builds[0]["bundle"], relocation)
    relocation_smoke = smoke_binary(relocation, work_root / "runtime-relocated", functional=False)

    release_directory.mkdir(parents=True)
    final_app = release_directory / "app"
    shutil.copytree(builds[0]["bundle"], final_app)
    final_inspection = inspect_bundle(final_app, developer_markers)
    final_ocr = tesseract_details(final_app)
    components = dependency_inventory(packages, final_ocr)
    components_path = release_directory / "THIRD_PARTY_COMPONENTS.json"
    components_path.write_text(json.dumps(components, indent=2, sort_keys=True), encoding="utf-8", newline="\n")
    manifest = make_build_manifest(
        aurelia_version=version,
        source_commit_sha=source["commit"],
        source_tree_dirty=source["dirty"],
        build_utc=datetime.now(timezone.utc).isoformat(),
        python_version=actual_python,
        pyinstaller_version=pyinstaller_version,
        requirements_lock_sha256=sha256_file(LOCKFILE),
        build_spec_sha256=sha256_file(SPEC),
        executable_sha256=sha256_file(final_app / "Aurelia.exe"),
        file_count=final_inspection["file_count"],
        total_artifact_size=final_inspection["total_size"],
        largest_files=final_inspection["largest_files"],
        tesseract_size=final_inspection["tesseract_size"],
        python_runtime_size=final_inspection["python_runtime_size"],
        tesseract_version=final_ocr["version"],
        ocr_languages=final_ocr["languages"],
        database_schema_version=schema_version_from_source(),
        build_mode="onedir",
        dependency_count=len(packages),
        reproducibility=reproducibility,
        build_layouts_match=(len(builds) == 1 or set(builds[0]["inspection"]["inventory"]) == set(builds[1]["inspection"]["inventory"])),
        windows_version_metadata=builds[0]["metadata"],
        forbidden_file_scan="pass",
        developer_text_path_scan="pass",
        binary_build_metadata_hits=final_inspection["binary_build_metadata_hits"],
        smoke_test=builds[0]["smoke"],
        relocation_smoke=relocation_smoke,
    )
    manifest_path = release_directory / "build-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8", newline="\n")
    create_portable_archive(release_directory, portable_archive)
    sums = {
        "app/Aurelia.exe": sha256_file(final_app / "Aurelia.exe"),
        "build-manifest.json": sha256_file(manifest_path),
        "THIRD_PARTY_COMPONENTS.json": sha256_file(components_path),
        f"../{portable_archive.name}": sha256_file(portable_archive),
    }
    sums_path = release_directory / "SHA256SUMS.txt"
    sums_path.write_text(
        "".join(f"{digest}  {name}\n" for name, digest in sums.items()),
        encoding="ascii", newline="\n",
    )
    print(json.dumps({
        "status": "success",
        "release_directory": str(release_directory),
        "portable_archive": str(portable_archive),
        "manifest": str(manifest_path),
        "reproducibility": reproducibility,
        "file_count": final_inspection["file_count"],
        "total_size": final_inspection["total_size"],
        "executable_sha256": sums["app/Aurelia.exe"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as exc:
        print(f"BUILD FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
