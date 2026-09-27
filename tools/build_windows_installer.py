import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.build_windows_release import BuildError, read_product_version, sha256_file


ISS = ROOT / "installer" / "Aurelia.iss"
RELEASE_ROOT = ROOT / "release"
PHASE6E_ONLY_PATHS = {
    "BUILD_WINDOWS_INSTALLER.bat",
    "INSTALLER_AURELIA.bat",
    "INSTALLER_AURELIA.ps1",
    "README_INSTALLATION.txt",
    "README_WINDOWS_PRO.txt",
    "SIGNER_SETUP_FEWURA.bat",
    "docs/WINDOWS_PACKAGING.md",
    "installer/Aurelia.iss",
    "tests/test_windows_installer_tooling.py",
    "tools/build_windows_installer.py",
    "tools/validate_windows_installer.py",
}


def run(command, capture=False, timeout=None, env=None):
    result = subprocess.run(
        [str(part) for part in command], cwd=ROOT, env=env, text=True,
        capture_output=capture, timeout=timeout,
    )
    if result.returncode:
        output = ((result.stdout or "") + (result.stderr or "")).strip()
        raise BuildError(f"Commande échouée ({result.returncode}): {' '.join(map(str, command))}\n{output}")
    return result


def git_output(*arguments):
    return run(
        ["git", "-c", f"safe.directory={ROOT}", "-C", ROOT, *arguments],
        capture=True,
    ).stdout.strip()


def phase6d_paths(version, root=ROOT):
    root = Path(root)
    release = root / "release" / f"Aurelia-{version}"
    return {
        "release": release,
        "app": release / "app",
        "manifest": release / "build-manifest.json",
        "components": release / "THIRD_PARTY_COMPONENTS.json",
        "sums": release / "SHA256SUMS.txt",
        "archive": root / "release" / f"Aurelia-{version}-portable.zip",
        "installer": root / "release" / f"Aurelia-Setup-{version}.exe",
    }


def parse_sha256sums(path):
    values = {}
    for raw in Path(path).read_text(encoding="ascii").splitlines():
        if not raw.strip():
            continue
        digest, name = raw.split("  ", 1)
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise BuildError(f"Empreinte SHA-256 invalide dans {path}: {name}")
        values[name] = digest
    return values


def verified_phase6d_source(manifest_commit):
    head = git_output("rev-parse", "HEAD")
    if manifest_commit == head:
        return manifest_commit
    run(["git", "-c", f"safe.directory={ROOT}", "-C", ROOT,
         "merge-base", "--is-ancestor", manifest_commit, head], capture=True)
    changed = set(git_output("diff", "--name-only", f"{manifest_commit}..{head}").splitlines())
    unexpected = sorted(changed - PHASE6E_ONLY_PATHS)
    if unexpected:
        raise BuildError(
            "Le code produit diffère de l'artefact Phase 6D; reconstruisez d'abord la Phase 6D: "
            f"{unexpected}"
        )
    return manifest_commit


def verify_phase6d_release(version, root=ROOT):
    paths = phase6d_paths(version, root)
    missing = [name for name, path in paths.items() if name != "installer" and not path.exists()]
    if missing:
        raise BuildError(f"Artefacts Phase 6D absents: {missing}")
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    source_commit = (
        verified_phase6d_source(manifest.get("source_commit_sha"))
        if Path(root).resolve() == ROOT else manifest.get("source_commit_sha")
    )
    required = {
        "aurelia_version": version,
        "source_commit_sha": source_commit,
        "source_tree_dirty": False,
        "build_mode": "onedir",
        "forbidden_file_scan": "pass",
        "developer_text_path_scan": "pass",
    }
    mismatches = {
        key: {"expected": expected, "actual": manifest.get(key)}
        for key, expected in required.items() if manifest.get(key) != expected
    }
    if mismatches:
        raise BuildError(f"Manifest Phase 6D incompatible: {mismatches}")
    if sha256_file(paths["app"] / "Aurelia.exe") != manifest["executable_sha256"]:
        raise BuildError("Aurelia.exe ne correspond pas au manifest Phase 6D")
    actual_files = sorted(path for path in paths["app"].rglob("*") if path.is_file())
    if len(actual_files) != manifest["file_count"]:
        raise BuildError("Le nombre de fichiers onedir diffère du manifest Phase 6D")
    sums = parse_sha256sums(paths["sums"])
    sum_targets = {
        "app/Aurelia.exe": paths["app"] / "Aurelia.exe",
        "build-manifest.json": paths["manifest"],
        "THIRD_PARTY_COMPONENTS.json": paths["components"],
        f"../{paths['archive'].name}": paths["archive"],
    }
    for name, target in sum_targets.items():
        if sums.get(name) != sha256_file(target):
            raise BuildError(f"Échec de vérification Phase 6D: {name}")
    with zipfile.ZipFile(paths["archive"]) as archive:
        corrupt = archive.testzip()
        if corrupt:
            raise BuildError(f"Archive Phase 6D corrompue: {corrupt}")
        prefix = f"Aurelia-{version}/app/"
        archived = {
            info.filename[len(prefix):]: info
            for info in archive.infolist()
            if not info.is_dir() and info.filename.startswith(prefix)
        }
        expected_names = {path.relative_to(paths["app"]).as_posix() for path in actual_files}
        if set(archived) != expected_names:
            raise BuildError("L'archive portable et le dossier onedir n'ont pas le même contenu")
        for relative, info in archived.items():
            digest = hashlib.sha256()
            with archive.open(info) as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            if digest.hexdigest() != sha256_file(paths["app"] / Path(*relative.split("/"))):
                raise BuildError(f"Contenu Phase 6D divergent dans l'archive: {relative}")
    return {"paths": paths, "manifest": manifest, "source_commit": source_commit}


def validate_inno_contract(content, version):
    checks = {
        "dynamic_version": 'FileOpen(SourcePath + "\\..\\VERSION.txt")' in content,
        "per_user": "PrivilegesRequired=lowest" in content,
        "install_location": "DefaultDirName={localappdata}\\Programs\\Aurelia" in content,
        "x64": "ArchitecturesAllowed=x64compatible" in content,
        "phase6d_source": 'Source: "..\\release\\Aurelia-{#MyAppVersion}\\app\\*"' in content,
        "artifact_name": "OutputBaseFilename=Aurelia-Setup-{#MyAppVersion}" in content,
        "release_output": "OutputDir=..\\release" in content,
        "start_menu": 'Name: "{autoprograms}\\Aurelia"' in content,
        "desktop_optional": 'Tasks: desktopicon' in content,
        "no_data_delete": "[UninstallDelete]" not in content,
        "no_stale_version": not re.search(r"5\.(?:0|1|2)\.\d+", content),
        "expected_version": bool(re.fullmatch(r"\d+\.\d+\.\d+", version)),
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise BuildError(f"Contrat Inno Setup invalide: {failed}")
    return checks


def find_iscc(explicit=None):
    candidates = [
        explicit,
        os.getenv("INNO_ISCC"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"),
        os.path.expandvars(r"%ProgramFiles%\Inno Setup 6\ISCC.exe"),
    ]
    return next((Path(value).resolve() for value in candidates if value and Path(value).is_file()), None)


def powershell_json(script):
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    executable = system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if not executable.is_file():
        raise BuildError("Windows PowerShell système introuvable pour valider les métadonnées PE")
    environment = os.environ.copy()
    environment["PSModulePath"] = os.pathsep.join((
        str(system_root / "System32" / "WindowsPowerShell" / "v1.0" / "Modules"),
        str(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "WindowsPowerShell" / "Modules"),
    ))
    result = run([executable, "-NoProfile", "-Command", script], capture=True, env=environment)
    return json.loads(result.stdout)


def installer_metadata(path):
    escaped = str(Path(path).resolve()).replace("'", "''")
    metadata = powershell_json(
        f"$v=(Get-Item -LiteralPath '{escaped}').VersionInfo; "
        "$s=Get-AuthenticodeSignature -LiteralPath '" + escaped + "'; "
        "[pscustomobject]@{FileVersion=$v.FileVersion;ProductVersion=$v.ProductVersion;"
        "ProductName=$v.ProductName;CompanyName=$v.CompanyName;Description=$v.FileDescription;"
        "SignatureStatus=$s.Status.ToString();Signer=if($s.SignerCertificate){$s.SignerCertificate.Subject}else{$null}} "
        "| ConvertTo-Json -Compress"
    )
    return {key: value.strip() if isinstance(value, str) else value for key, value in metadata.items()}


def executable_version(path):
    escaped = str(Path(path).resolve()).replace("'", "''")
    metadata = powershell_json(
        f"$v=(Get-Item -LiteralPath '{escaped}').VersionInfo; "
        "[pscustomobject]@{FileVersion=$v.FileVersion;ProductVersion=$v.ProductVersion} "
        "| ConvertTo-Json -Compress"
    )
    return {key: value.strip() if isinstance(value, str) else value for key, value in metadata.items()}


def inno_setup_version(compiler):
    history = Path(compiler).parent / "whatsnew.htm"
    if history.is_file():
        match = re.search(r'<a\s+name="(\d+\.\d+\.\d+)"', history.read_text(encoding="utf-8"))
        if match:
            return match.group(1)
    metadata = executable_version(compiler)
    return metadata.get("ProductVersion") or metadata.get("FileVersion") or "unknown"


def write_installer_sums(paths, report_path=None):
    rows = [(paths["installer"].name, sha256_file(paths["installer"]))]
    if report_path and Path(report_path).is_file():
        rows.append((Path(report_path).name, sha256_file(report_path)))
    target = RELEASE_ROOT / "SHA256SUMS-INSTALLER.txt"
    target.write_text("".join(f"{digest}  {name}\n" for name, digest in rows), encoding="ascii", newline="\n")
    return target


def build_installer(iscc=None, allow_dirty=False, run_tests=True):
    status = git_output("status", "--porcelain", "--untracked-files=all")
    if status and not allow_dirty:
        raise BuildError("La compilation installateur exige un dépôt Git propre")
    version = read_product_version()
    phase6d = verify_phase6d_release(version)
    contract = validate_inno_contract(ISS.read_text(encoding="utf-8-sig"), version)
    compiler = find_iscc(iscc)
    if not compiler:
        raise BuildError("ISCC.exe introuvable; installez Inno Setup 6 ou définissez INNO_ISCC")
    if run_tests:
        run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"])
    paths = phase6d["paths"]
    paths["installer"].unlink(missing_ok=True)
    run([compiler, "/Qp", ISS])
    if not paths["installer"].is_file():
        raise BuildError("L'installateur Inno Setup attendu n'a pas été produit")
    metadata = installer_metadata(paths["installer"])
    valid_versions = {version, f"{version}.0"}
    if (
        metadata.get("FileVersion") not in valid_versions
        or metadata.get("ProductVersion") not in valid_versions
        or metadata.get("ProductName") != "Aurelia"
        or metadata.get("CompanyName") != "FEWURA"
    ):
        raise BuildError(f"Métadonnées installateur incorrectes: {metadata}")
    build_report = {
        "installer_build_report_version": 1,
        "aurelia_version": version,
        "source_commit": phase6d["source_commit"],
        "build_utc": datetime.now(timezone.utc).isoformat(),
        "inno_setup_version": inno_setup_version(compiler),
        "phase6d_manifest_sha256": sha256_file(paths["manifest"]),
        "phase6d_archive_sha256": sha256_file(paths["archive"]),
        "phase6d_app_file_count": phase6d["manifest"]["file_count"],
        "installer_filename": paths["installer"].name,
        "installer_size": paths["installer"].stat().st_size,
        "installer_sha256": sha256_file(paths["installer"]),
        "installer_metadata": metadata,
        "inno_contract": contract,
        "signing_status": "signed" if metadata.get("SignatureStatus") == "Valid" else "blocked_certificate_unavailable",
    }
    report_path = RELEASE_ROOT / "installer-build-report.json"
    report_path.write_text(json.dumps(build_report, indent=2, sort_keys=True), encoding="utf-8", newline="\n")
    sums = write_installer_sums(paths, report_path)
    return {"paths": paths, "report": build_report, "report_path": report_path, "sums": sums}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Compile the validated Aurelia Phase 6D artifact with Inno Setup")
    parser.add_argument("--iscc")
    parser.add_argument("--allow-dirty", action="store_true", help="Development validation only")
    parser.add_argument("--skip-tests", action="store_true", help="Development iteration only")
    args = parser.parse_args(argv)
    result = build_installer(args.iscc, args.allow_dirty, not args.skip_tests)
    print(json.dumps({
        "status": "success",
        "installer": str(result["paths"]["installer"]),
        "sha256": result["report"]["installer_sha256"],
        "size": result["report"]["installer_size"],
        "signing_status": result["report"]["signing_status"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as exc:
        print(f"INSTALLER BUILD FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
