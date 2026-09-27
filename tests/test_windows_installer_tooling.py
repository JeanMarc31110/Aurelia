import tempfile
import unittest
from pathlib import Path

from tools.build_windows_installer import (
    BuildError,
    PHASE6E_ONLY_PATHS,
    inno_setup_version,
    parse_sha256sums,
    validate_inno_contract,
    verify_phase6d_release,
)
from tools.build_windows_release import read_product_version, sha256_file
from tools.validate_windows_installer import path_is_business


ROOT = Path(__file__).resolve().parents[1]


class WindowsInstallerToolingTests(unittest.TestCase):
    def test_inno_contract_uses_authoritative_version_and_per_user_install(self):
        version = read_product_version(ROOT)
        content = (ROOT / "installer" / "Aurelia.iss").read_text(encoding="utf-8-sig")
        checks = validate_inno_contract(content, version)
        self.assertTrue(all(checks.values()))
        self.assertIn('FileOpen(SourcePath + "\\..\\VERSION.txt")', content)
        self.assertIn("DefaultDirName={localappdata}\\Programs\\Aurelia", content)
        self.assertIn("PrivilegesRequired=lowest", content)

    def test_inno_consumes_exact_phase6d_folder_and_names_versioned_installer(self):
        content = (ROOT / "installer" / "Aurelia.iss").read_text(encoding="utf-8-sig")
        self.assertIn('Source: "..\\release\\Aurelia-{#MyAppVersion}\\app\\*"', content)
        self.assertIn("OutputBaseFilename=Aurelia-Setup-{#MyAppVersion}", content)
        self.assertIn("OutputDir=..\\release", content)

    def test_uninstall_contract_never_deletes_customer_data(self):
        content = (ROOT / "installer" / "Aurelia.iss").read_text(encoding="utf-8-sig")
        self.assertNotIn("[UninstallDelete]", content)
        self.assertNotIn("{localappdata}\\Aurelia", content)
        self.assertNotIn("{userdocs}\\Aurelia", content)

    def test_canonical_and_legacy_entrypoints_are_unambiguous(self):
        canonical = (ROOT / "BUILD_WINDOWS_INSTALLER.bat").read_text(encoding="utf-8").lower()
        self.assertIn("tools\\build_windows_installer.py", canonical)
        for name in ("INSTALLER_AURELIA.bat", "INSTALLER_AURELIA.ps1"):
            legacy = (ROOT / name).read_text(encoding="utf-8-sig").lower()
            self.assertIn("deprecated", legacy)
            self.assertNotIn("pip install", legacy)
            self.assertNotIn("aurelia-changeme", legacy)

    def test_signing_script_is_dynamic_and_contains_no_stale_artifact_name(self):
        content = (ROOT / "SIGNER_SETUP_FEWURA.bat").read_text(encoding="utf-8-sig").lower()
        self.assertIn("version.txt", content)
        self.assertIn("aurelia-setup-%aurelia_version%.exe", content)
        self.assertIn("/fd sha256", content)
        self.assertIn("/td sha256", content)
        self.assertNotIn("5.0.1", content)

    def test_inno_version_is_read_from_the_compiler_distribution(self):
        with tempfile.TemporaryDirectory() as temporary:
            compiler = Path(temporary) / "ISCC.exe"
            compiler.write_bytes(b"synthetic")
            (compiler.parent / "whatsnew.htm").write_text(
                '<p><a name="6.7.3"></a><span class="ver">6.7.3</span></p>', encoding="utf-8",
            )
            self.assertEqual(inno_setup_version(compiler), "6.7.3")

    def test_phase6d_release_integrity_is_verified_before_installer_build(self):
        result = verify_phase6d_release(read_product_version(ROOT))
        self.assertEqual(result["manifest"]["build_mode"], "onedir")
        self.assertFalse(result["manifest"]["source_tree_dirty"])

    def test_phase6d_commit_exception_is_limited_to_phase6e_tooling(self):
        self.assertIn("installer/Aurelia.iss", PHASE6E_ONLY_PATHS)
        self.assertIn("tools/build_windows_installer.py", PHASE6E_ONLY_PATHS)
        self.assertFalse(any(path.startswith("app/") for path in PHASE6E_ONLY_PATHS))
        self.assertFalse(any(path.startswith("config/") for path in PHASE6E_ONLY_PATHS))

    def test_sha256sum_parser_rejects_malformed_digests(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "SHA256SUMS.txt"
            payload = Path(temporary) / "artifact.bin"
            payload.write_bytes(b"synthetic")
            path.write_text(f"{sha256_file(payload)}  artifact.bin\n", encoding="ascii")
            self.assertEqual(parse_sha256sums(path)["artifact.bin"], sha256_file(payload))
            path.write_text("not-a-hash  artifact.bin\n", encoding="ascii")
            with self.assertRaises(BuildError):
                parse_sha256sums(path)

    def test_runtime_evidence_hashes_exclude_logs_locks_and_backups(self):
        self.assertTrue(path_is_business(Path("state/aurelia_v5.db")))
        self.assertTrue(path_is_business(Path("documents/invoice.xml")))
        self.assertFalse(path_is_business(Path("state/aurelia.log")))
        self.assertFalse(path_is_business(Path("state/aurelia.lock")))
        self.assertFalse(path_is_business(Path("documents/Backups/archive.zip")))


if __name__ == "__main__":
    unittest.main()
