import argparse
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import uuid
import winreg
from datetime import datetime, timezone
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.build_windows_installer import (
    BuildError,
    phase6d_paths,
    sha256_file,
    write_installer_sums,
)
from tools.build_windows_release import read_product_version


VALIDATION_ROOT = ROOT / "release" / ".installer-validation"
UNINSTALL_REGISTRY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"


def run_process(command, timeout=180, env=None):
    result = subprocess.run(
        [str(part) for part in command], cwd=ROOT, env=env, text=True,
        capture_output=True, timeout=timeout,
    )
    return result


def wait_health(base="http://127.0.0.1:8000", timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            response = requests.get(f"{base}/health", timeout=.5)
            if response.status_code == 200:
                return response
        except requests.RequestException:
            pass
        time.sleep(.15)
    raise BuildError("L'application installée n'a pas répondu à /health")


def port_released():
    with socket.socket() as probe:
        probe.settimeout(.5)
        return probe.connect_ex(("127.0.0.1", 8000)) != 0


def isolated_environment(runtime_root, browser=False):
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    environment = os.environ.copy()
    environment.update({
        "PATH": os.pathsep.join((str(system_root / "System32"), str(system_root))),
        "AURELIA_RUNTIME_ROOT": str(runtime_root),
        "AURELIA_WATCHER_ENABLED": "0",
        "AURELIA_BACKUPS_ENABLED": "1",
        "AURELIA_NO_BROWSER": "0" if browser else "1",
    })
    return environment


def start_installed(install_dir, runtime_root, browser=False):
    executable = Path(install_dir) / "Aurelia.exe"
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(
        [executable], cwd=install_dir,
        env=isolated_environment(runtime_root, browser), creationflags=flags,
    )
    wait_health()
    return process


def stop_installed(process, session):
    response = session.post("http://127.0.0.1:8000/shutdown", timeout=5)
    process.wait(timeout=15)
    if response.status_code != 200 or process.returncode != 0 or not port_released():
        raise BuildError("Arrêt incomplet de l'application installée")
    return response.status_code


def setup_and_product_smoke(install_dir, runtime_root, password):
    process = start_installed(install_dir, runtime_root)
    second = None
    session = requests.Session()
    base = "http://127.0.0.1:8000"
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        second = subprocess.Popen(
            [Path(install_dir) / "Aurelia.exe"], cwd=install_dir,
            env=isolated_environment(runtime_root), creationflags=flags,
        )
        second.wait(timeout=10)
        if second.returncode != 0:
            raise BuildError(f"La seconde instance installée a retourné {second.returncode}")
        setup_page = session.get(f"{base}/setup", allow_redirects=False, timeout=5)
        setup = session.post(f"{base}/setup", data={
            "username": "installer-admin", "password": password,
            "password_confirmation": password, "legal_name": "Installer Synthetic Company",
            "tax_id": "INSTALLER-SYNTH", "country": "FR", "currency": "EUR",
        }, allow_redirects=False, timeout=5)
        login = session.post(f"{base}/login", data={
            "username": "installer-admin", "password": password,
        }, allow_redirects=False, timeout=5)
        pages = {
            path: session.get(f"{base}{path}", timeout=5)
            for path in ("/", "/work", "/settings", "/settings/ocr")
        }
        ubl = b'''<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2" xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2" xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"><cbc:ID>INSTALLER-SMOKE-001</cbc:ID><cbc:IssueDate>2026-09-27</cbc:IssueDate><cbc:DocumentCurrencyCode>EUR</cbc:DocumentCurrencyCode><cac:AccountingSupplierParty><cac:Party><cac:PartyName><cbc:Name>INSTALLER SUPPLIER</cbc:Name></cac:PartyName></cac:Party></cac:AccountingSupplierParty><cac:AccountingCustomerParty><cac:Party><cac:PartyName><cbc:Name>Installer Synthetic Company</cbc:Name></cac:PartyName></cac:Party></cac:AccountingCustomerParty><cac:InvoiceLine><cbc:ID>1</cbc:ID><cbc:InvoicedQuantity>1</cbc:InvoicedQuantity><cbc:LineExtensionAmount>100.00</cbc:LineExtensionAmount><cac:Item><cbc:Description>Installer smoke</cbc:Description></cac:Item><cac:Price><cbc:PriceAmount>100.00</cbc:PriceAmount></cac:Price></cac:InvoiceLine><cac:TaxTotal><cbc:TaxAmount>20.00</cbc:TaxAmount></cac:TaxTotal><cac:LegalMonetaryTotal><cbc:TaxExclusiveAmount>100.00</cbc:TaxExclusiveAmount><cbc:PayableAmount>120.00</cbc:PayableAmount></cac:LegalMonetaryTotal></Invoice>'''
        upload = session.post(
            f"{base}/api/invoices/upload",
            files={"file": ("installer-smoke.ubl", ubl, "application/xml")}, timeout=10,
        )
        payload = upload.json()
        invoice_id = payload.get("decision", {}).get("invoice_id")
        review = session.get(f"{base}/invoices/{invoice_id}", timeout=5) if invoice_id else None
        approval = session.post(
            f"{base}/invoices/{invoice_id}/approve", data={"comment": "synthetic installer smoke"},
            allow_redirects=False, timeout=5,
        ) if invoice_id else None
        database = runtime_root / "state" / "aurelia_v5.db"
        with sqlite3.connect(database) as connection:
            customer_id = connection.execute(
                "INSERT INTO customers(name,email) VALUES(?,?)",
                ("Installer Synthetic Customer", "installer@example.invalid"),
            ).lastrowid
        outbound = session.post(f"{base}/api/outbound", data={
            "customer_id": customer_id, "description": "Installed application smoke",
            "net_amount": "100.00", "vat_rate": "20", "due_days": "30",
        }, timeout=10)
        outbound_payload = outbound.json()
        generated = [Path(outbound_payload[key]).resolve() for key in ("pdf_path", "xml_path")]
        ocr = installed_ocr_smoke(install_dir, runtime_root)
        result = {
            "health": 200,
            "setup_page": setup_page.status_code,
            "setup": setup.status_code,
            "login": login.status_code,
            "pages": {path: response.status_code for path, response in pages.items()},
            "ocr_page": "Disponible" in pages["/settings/ocr"].text,
            "multi_instance_exit": second.returncode,
            "upload": upload.status_code,
            "review": review.status_code if review else None,
            "approval": approval.status_code if approval else None,
            "outbound": outbound.status_code,
            "generated_documents_exist": all(path.is_file() for path in generated),
            "generated_documents_outside_install": all(Path(install_dir) not in path.parents for path in generated),
            "ocr": ocr,
        }
        expected_pages = all(response.status_code == 200 for response in pages.values())
        expected = (
            setup_page.status_code == 200 and setup.status_code == 303 and login.status_code == 303
            and expected_pages and result["ocr_page"] and upload.status_code == 200
            and result["review"] == 200 and result["approval"] == 303
            and outbound.status_code == 200 and result["generated_documents_exist"]
            and result["generated_documents_outside_install"] and ocr["passed"]
        )
        if not expected:
            raise BuildError(f"Smoke produit installé incomplet: {result}")
        result["shutdown"] = stop_installed(process, session)
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
        session.close()


def existing_state_smoke(install_dir, runtime_root, password):
    process = start_installed(install_dir, runtime_root)
    session = requests.Session()
    base = "http://127.0.0.1:8000"
    try:
        setup = session.get(f"{base}/setup", allow_redirects=False, timeout=5)
        login = session.post(f"{base}/login", data={
            "username": "installer-admin", "password": password,
        }, allow_redirects=False, timeout=5)
        dashboard = session.get(f"{base}/", timeout=5)
        if setup.status_code != 303 or login.status_code != 303 or dashboard.status_code != 200:
            raise BuildError("Les données existantes ne sont pas réutilisées après réinstallation")
        stop_installed(process, session)
        return {"setup_redirect": setup.status_code, "login": login.status_code, "dashboard": dashboard.status_code}
    finally:
        if process.poll() is None:
            process.terminate(); process.wait(timeout=8)
        session.close()


def installed_ocr_smoke(install_dir, runtime_root):
    executable = Path(install_dir) / "_internal" / "resources" / "tesseract" / "tesseract.exe"
    tessdata = executable.parent / "tessdata"
    image = runtime_root / "ocr-installer-smoke.png"
    canvas = Image.new("RGB", (1000, 240), "white")
    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.truetype("arial.ttf", 72)
    except OSError:
        font = ImageFont.load_default()
    draw.text((35, 70), "AURELIA INSTALL 530", font=font, fill="black")
    canvas.save(image)
    environment = isolated_environment(runtime_root)
    environment["TESSDATA_PREFIX"] = str(tessdata)
    result = run_process([executable, image, "stdout", "-l", "eng"], timeout=30, env=environment)
    output = result.stdout.strip()
    return {
        "passed": result.returncode == 0 and "AURELIA" in output.upper(),
        "text": output,
        "bundled_executable": executable.is_file(),
        "languages": [language for language in ("eng", "fra", "spa", "osd") if (tessdata / f"{language}.traineddata").is_file()],
        "system_path_excluded": "Tesseract-OCR" not in environment["PATH"] and "Python" not in environment["PATH"],
    }


def installer_command(installer, install_dir, log_path):
    return [
        installer, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CURRENTUSER",
        "/MERGETASKS=!desktopicon", f"/DIR={install_dir}", f"/LOG={log_path}",
    ]


def install(installer, install_dir, log_path):
    result = run_process(installer_command(installer, install_dir, log_path), timeout=300)
    if result.returncode != 0:
        raise BuildError(f"Installation Inno Setup échouée: {result.returncode}")
    return result.returncode


def uninstall_entry():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_REGISTRY) as root:
            for index in range(winreg.QueryInfoKey(root)[0]):
                name = winreg.EnumKey(root, index)
                with winreg.OpenKey(root, name) as key:
                    try:
                        display = winreg.QueryValueEx(key, "DisplayName")[0]
                    except FileNotFoundError:
                        continue
                    if display == "Aurelia":
                        return {
                            "present": True,
                            "display_name": display,
                            "display_version": winreg.QueryValueEx(key, "DisplayVersion")[0],
                            "publisher": winreg.QueryValueEx(key, "Publisher")[0],
                        }
    except FileNotFoundError:
        pass
    return {"present": False}


def business_hashes(runtime_root):
    roots = [runtime_root / "state", runtime_root / "documents"]
    values = {}
    for root in roots:
        if root.is_dir():
            for path in sorted(item for item in root.rglob("*") if item.is_file() and path_is_business(item)):
                values[path.relative_to(runtime_root).as_posix()] = sha256_file(path)
    return values


def path_is_business(path):
    return path.suffix.lower() not in {".log", ".lock"} and "Backups" not in path.parts


def verify_installed_content(source_app, install_dir):
    source = {path.relative_to(source_app).as_posix(): sha256_file(path) for path in source_app.rglob("*") if path.is_file()}
    for relative, digest in source.items():
        installed = install_dir / Path(*relative.split("/"))
        if not installed.is_file() or sha256_file(installed) != digest:
            raise BuildError(f"Fichier installé différent de Phase 6D: {relative}")
    extras = sorted(
        path.relative_to(install_dir).as_posix() for path in install_dir.rglob("*") if path.is_file()
        and path.relative_to(install_dir).as_posix() not in source
    )
    allowed_extras = {"unins000.dat", "unins000.exe"}
    unexpected = [name for name in extras if name not in allowed_extras]
    if unexpected:
        raise BuildError(f"Fichiers installés inattendus: {unexpected}")
    forbidden = [
        relative for relative in source
        if Path(relative).suffix.lower() in {".db", ".sqlite", ".log", ".key"}
        or Path(relative).name.lower() in {".env", "session.secret"}
    ]
    if forbidden:
        raise BuildError(f"Données interdites installées: {forbidden}")
    return {"source_files": len(source), "installer_files": extras, "unexpected_files": unexpected}


def validation_environment_snapshot():
    import shutil as _shutil
    return {
        "type": "isolated_local_windows_environment_option_d_not_clean_machine",
        "windows_version": f"{os.sys.getwindowsversion().major}.{os.sys.getwindowsversion().minor}.{os.sys.getwindowsversion().build}",
        "architecture": os.environ.get("PROCESSOR_ARCHITECTURE", "unknown"),
        "python_present_before_install": bool(_shutil.which("python") or _shutil.which("py")),
        "system_tesseract_present_before_install": bool(_shutil.which("tesseract")),
        "aurelia_installed_before_test": uninstall_entry()["present"],
        "real_runtime_paths_used": False,
    }


def validate(installer):
    version = read_product_version()
    paths = phase6d_paths(version)
    installer = Path(installer).resolve()
    if installer != paths["installer"].resolve() or not installer.is_file():
        raise BuildError("L'installateur à valider n'est pas l'artefact de release attendu")
    if VALIDATION_ROOT.exists():
        shutil.rmtree(VALIDATION_ROOT)
    VALIDATION_ROOT.mkdir(parents=True)
    install_dir = VALIDATION_ROOT / "installed" / "Aurelia"
    runtime_root = VALIDATION_ROOT / "runtime"
    logs = VALIDATION_ROOT / "logs"
    logs.mkdir()
    environment = validation_environment_snapshot()
    password = f"Installer-{uuid.uuid4().hex}!"
    shortcut = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Aurelia.lnk"
    report = {
        "installer_validation_report_version": 1,
        "aurelia_version": version,
        "source_commit": json.loads(paths["manifest"].read_text(encoding="utf-8"))["source_commit_sha"],
        "validation_utc": datetime.now(timezone.utc).isoformat(),
        "installer_sha256": sha256_file(installer),
        "installer_size": installer.stat().st_size,
        "environment": environment,
        "signing_status": "blocked_certificate_unavailable",
        "signature_verification": "not_signed",
        "smartscreen_observation": "not_evaluated_without_authenticode_certificate",
    }
    try:
        install(installer, install_dir, logs / "install.log")
        report["install_result"] = "pass"
        report["installed_apps_entry"] = uninstall_entry()
        report["start_menu_shortcut"] = shortcut.is_file()
        if (
            not report["installed_apps_entry"].get("present")
            or report["installed_apps_entry"].get("display_version") != version
            or report["installed_apps_entry"].get("publisher") != "FEWURA"
            or not report["start_menu_shortcut"]
        ):
            raise BuildError("L'inscription Applications installées ou le raccourci du menu Démarrer est invalide")
        report["installed_content"] = verify_installed_content(paths["app"], install_dir)
        report["first_launch"] = setup_and_product_smoke(install_dir, runtime_root, password)
        before_reinstall = business_hashes(runtime_root)
        install(installer, install_dir, logs / "same-version-reinstall.log")
        after_reinstall = business_hashes(runtime_root)
        report["same_version_reinstall"] = {
            "result": "pass" if before_reinstall == after_reinstall else "fail",
            "customer_data_preserved": before_reinstall == after_reinstall,
            "installed_content": verify_installed_content(paths["app"], install_dir),
        }
        if before_reinstall != after_reinstall:
            raise BuildError("La réinstallation même version a modifié les données métier")
        report["same_version_launch"] = existing_state_smoke(install_dir, runtime_root, password)
        before_uninstall = business_hashes(runtime_root)
        uninstaller = install_dir / "unins000.exe"
        uninstall_result = run_process([
            uninstaller, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
            f"/LOG={logs / 'uninstall.log'}",
        ], timeout=300)
        if uninstall_result.returncode != 0:
            raise BuildError(f"Désinstallation échouée: {uninstall_result.returncode}")
        deadline = time.monotonic() + 15
        while install_dir.exists() and time.monotonic() < deadline:
            time.sleep(.2)
        after_uninstall = business_hashes(runtime_root)
        report["uninstall"] = {
            "result": "pass" if not install_dir.exists() else "fail",
            "install_directory_removed": not install_dir.exists(),
            "customer_data_preserved": before_uninstall == after_uninstall,
            "installed_apps_entry_removed": not uninstall_entry()["present"],
        }
        if install_dir.exists() or before_uninstall != after_uninstall:
            raise BuildError("La désinstallation n'a pas respecté le contrat de conservation")
        install(installer, install_dir, logs / "reinstall-after-uninstall.log")
        reinstall_preserved = before_uninstall == business_hashes(runtime_root)
        if not reinstall_preserved:
            raise BuildError("La réinstallation après désinstallation a modifié les données métier")
        report["reinstall_after_uninstall"] = existing_state_smoke(install_dir, runtime_root, password)
        report["reinstall_after_uninstall"]["customer_data_preserved"] = reinstall_preserved
        before_failed_install = business_hashes(runtime_root)
        corrupt_installer = VALIDATION_ROOT / "corrupt-installer.exe"
        with installer.open("rb") as source, corrupt_installer.open("wb") as target:
            target.write(source.read(1024 * 1024))
        try:
            failure = run_process([
                corrupt_installer, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
            ], timeout=120)
            failure_exit = failure.returncode
        except OSError:
            failure_exit = "os_loader_rejected"
        except subprocess.TimeoutExpired as exc:
            raise BuildError("Le test d'échec sûr de l'installateur tronqué a expiré") from exc
        failed_install_preserved = before_failed_install == business_hashes(runtime_root)
        report["safe_install_failure"] = {
            "result": "pass" if failure_exit != 0 and failed_install_preserved else "fail",
            "exit_code": failure_exit,
            "customer_data_preserved": failed_install_preserved,
        }
        if report["safe_install_failure"]["result"] != "pass":
            raise BuildError("L'échec d'installation simulé n'a pas été sans effet sur les données")
        report["port_occupied"] = {"result": "pending_manual_dialog_validation"}
        report["browser_launch"] = {"result": "covered_by_launcher_test_not_opened_during_automated_validation"}
        final_uninstaller = install_dir / "unins000.exe"
        cleanup = run_process([final_uninstaller, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], timeout=300)
        report["final_cleanup"] = {
            "uninstall_exit_code": cleanup.returncode,
            "customer_data_retained_for_evidence": runtime_root.is_dir(),
        }
    except Exception as exc:
        report["validation_error"] = type(exc).__name__
        raise
    finally:
        residual_uninstaller = install_dir / "unins000.exe"
        if residual_uninstaller.is_file():
            cleanup = run_process([
                residual_uninstaller, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
            ], timeout=300)
            report.setdefault("final_cleanup", {
                "uninstall_exit_code": cleanup.returncode,
                "customer_data_retained_for_evidence": runtime_root.is_dir(),
            })
        report_path = ROOT / "release" / "installer-validation-report.json"
        report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8", newline="\n")
        write_installer_sums(paths, report_path)
    return report_path, report


def main(argv=None):
    parser = argparse.ArgumentParser(description="Validate Aurelia's Inno Setup installer in an isolated local environment")
    parser.add_argument("installer", nargs="?")
    args = parser.parse_args(argv)
    version = read_product_version()
    installer = args.installer or phase6d_paths(version)["installer"]
    report_path, report = validate(installer)
    print(json.dumps({
        "status": "success",
        "report": str(report_path),
        "install": report.get("install_result"),
        "reinstall": report.get("same_version_reinstall", {}).get("result"),
        "uninstall": report.get("uninstall", {}).get("result"),
        "signing": report.get("signing_status"),
    }, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as exc:
        print(f"INSTALLER VALIDATION FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
