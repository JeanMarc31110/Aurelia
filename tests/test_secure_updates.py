import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from app.services.updates import (
    MANIFEST_FORMAT_VERSION,
    SignatureResult,
    UpdateError,
    UpdateManifest,
    compare_versions,
    create_application_recovery_point,
    execute_prepared_update,
    fetch_manifest,
    prepare_update,
    read_update_state,
    stage_package,
    validate_manifest_for_install,
)


class FakeProcess:
    def __init__(self):
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 0

    def wait(self, timeout=None):
        self.returncode = 0
        return 0

    def kill(self):
        self.returncode = -9


class SecureUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.app = self.root / "live" / "Aurelia"
        self.runtime = self.root / "runtime"
        self.update_root = self.runtime / "Updates"
        self.package_source = self.root / "Aurelia-Setup-5.3.1.exe"
        self.package_source.write_bytes(b"synthetic signed installer 5.3.1")
        self.data = self.runtime / "state" / "customer-data.bin"
        self.data.parent.mkdir(parents=True)
        self.data.write_bytes(b"customer data must remain unchanged")
        self._make_app(self.app, "5.3.0")

    def tearDown(self):
        self.temporary.cleanup()

    def _make_app(self, directory, version):
        (directory / "_internal" / "app" / "templates").mkdir(parents=True, exist_ok=True)
        (directory / "_internal" / "app" / "static").mkdir(parents=True, exist_ok=True)
        (directory / "Aurelia.exe").write_bytes(f"exe-{version}".encode())
        (directory / "_internal" / "VERSION.txt").write_text(version, encoding="ascii")
        (directory / "_internal" / "app" / "templates" / "login.html").write_text("login", encoding="ascii")
        (directory / "_internal" / "app" / "static" / "style.css").write_text("css", encoding="ascii")
        (directory / "installed-version.txt").write_text(version, encoding="ascii")

    def _manifest(self, version="5.3.1", **changes):
        values = {
            "manifest_format_version": MANIFEST_FORMAT_VERSION,
            "aurelia_version": version,
            "release_timestamp": "2026-09-27T20:00:00Z",
            "package_url": "https://updates.example.invalid/Aurelia-Setup-5.3.1.exe",
            "file_size": self.package_source.stat().st_size,
            "sha256": hashlib.sha256(self.package_source.read_bytes()).hexdigest(),
            "source_commit": "a" * 40,
            "minimum_app_version": "5.3.0",
            "minimum_schema_version": 1,
            "maximum_schema_version": 1,
            "target_schema_version": 1,
            "rollback_schema_compatible": True,
            "release_summary": "Synthetic Phase 6F update",
            "signature_required": True,
            "publisher": "FEWURA",
        }
        values.update(changes)
        return UpdateManifest.from_dict(values)

    def _copy_download(self, url, destination, expected_size):
        shutil.copyfile(self.package_source, destination)

    def _unsigned(self, path):
        return SignatureResult("NotSigned")

    def _valid(self, path):
        return SignatureResult("Valid", "CN=FEWURA", "AA11")

    def _space(self, path):
        return SimpleNamespace(free=1024 * 1024 * 1024)

    def _backup(self):
        target = self.runtime / "backups" / "pre-update.aurelia-backup"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.data, target)
        return target

    def _prepare(self, manifest=None):
        return prepare_update(
            manifest or self._manifest(), "5.3.0", 1, self.app, self.update_root, self._backup,
            mode="test", allow_unsigned_test=True, downloader=self._copy_download,
            signature_verifier=self._unsigned, disk_usage=self._space,
        )

    def test_scenario_a_530_to_531_success_preserves_customer_data(self):
        original_data = self.data.read_bytes()
        state_path, _ = self._prepare()

        def install(package, live):
            (Path(live) / "_internal" / "VERSION.txt").write_text("5.3.1", encoding="ascii")
            (Path(live) / "installed-version.txt").write_text("5.3.1", encoding="ascii")
            return 0

        result = execute_prepared_update(
            state_path, lambda: 1, installer_runner=install, launcher=lambda app: FakeProcess(),
            health_checker=lambda process, version: version == "5.3.1", signature_verifier=self._unsigned,
        )
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(self.data.read_bytes(), original_data)

    def test_scenario_b_bad_sha_rejected_before_install(self):
        manifest = self._manifest(sha256="0" * 64)
        with self.assertRaisesRegex(UpdateError, "SHA-256") as raised:
            stage_package(
                manifest, self.update_root, "test", True, downloader=self._copy_download,
                signature_verifier=self._unsigned, disk_usage=self._space,
            )
        self.assertEqual(raised.exception.code, "PACKAGE_HASH_MISMATCH")

    def test_scenario_c_unsigned_package_rejected_in_production(self):
        with self.assertRaises(UpdateError) as raised:
            stage_package(
                self._manifest(), self.update_root, "production", False, downloader=self._copy_download,
                signature_verifier=self._unsigned, disk_usage=self._space,
            )
        self.assertEqual(raised.exception.code, "SIGNATURE_REJECTED")

    def test_scenario_d_unsigned_package_allowed_only_by_explicit_test_override(self):
        staged = stage_package(
            self._manifest(), self.update_root, "test", True, downloader=self._copy_download,
            signature_verifier=self._unsigned, disk_usage=self._space,
        )
        self.assertTrue(staged["package"].is_file())
        with self.assertRaises(UpdateError):
            stage_package(
                self._manifest(), self.update_root, "test", False, downloader=self._copy_download,
                signature_verifier=self._unsigned, disk_usage=self._space,
            )

    def test_scenario_e_downgrade_531_to_530_rejected(self):
        with self.assertRaises(UpdateError) as raised:
            validate_manifest_for_install(self._manifest(version="5.3.0"), "5.3.1", 1, "test")
        self.assertEqual(raised.exception.code, "UPDATE_DOWNGRADE_REJECTED")

    def test_scenario_f_installer_failure_keeps_previous_version_recoverable(self):
        state_path, state = self._prepare()
        result = execute_prepared_update(
            state_path, lambda: 1, installer_runner=lambda package, live: 1,
            launcher=lambda app: FakeProcess(), health_checker=lambda process, version: version == "5.3.0",
            signature_verifier=self._unsigned,
        )
        self.assertEqual(result["status"], "ROLLED_BACK")
        self.assertEqual((self.app / "installed-version.txt").read_text(), "5.3.0")
        self.assertTrue(Path(state["recovery_dir"]).is_dir())

    def test_scenario_g_failed_health_rolls_application_back_to_530(self):
        state_path, _ = self._prepare()
        calls = []

        def health(process, version):
            calls.append(version)
            return version == "5.3.0"

        result = execute_prepared_update(
            state_path, lambda: 1, installer_runner=lambda package, live: 0,
            launcher=lambda app: FakeProcess(), health_checker=health, signature_verifier=self._unsigned,
        )
        self.assertEqual(result["status"], "ROLLED_BACK")
        self.assertEqual(calls, ["5.3.1", "5.3.0"])
        self.assertEqual((self.app / "installed-version.txt").read_text(), "5.3.0")

    def test_scenario_h_interruption_before_replacement_leaves_current_app_usable(self):
        state_path, _ = self._prepare()
        self.assertEqual(read_update_state(state_path)["status"], "READY_TO_INSTALL")
        self.assertEqual((self.app / "installed-version.txt").read_text(), "5.3.0")

    def test_scenario_i_incompatible_schema_prevents_automatic_rollback(self):
        state_path, _ = self._prepare(self._manifest(target_schema_version=2, rollback_schema_compatible=False))
        schemas = iter([2])
        result = execute_prepared_update(
            state_path, lambda: next(schemas), installer_runner=lambda package, live: 0,
            launcher=lambda app: FakeProcess(), health_checker=lambda process, version: False,
            signature_verifier=self._unsigned,
        )
        self.assertEqual(result["status"], "RECOVERY_REQUIRED")
        self.assertEqual(result["automatic_rollback"], "prevented_incompatible_schema")
        self.assertEqual((self.app / "installed-version.txt").read_text(), "5.3.0")

    def test_scenario_j_recovery_retains_only_one_previous_application(self):
        recovery_root = self.update_root / "recovery"
        create_application_recovery_point(self.app, recovery_root, "5.3.0")
        create_application_recovery_point(self.app, recovery_root, "5.2.9")
        retained = sorted(path.name for path in recovery_root.iterdir() if path.is_dir())
        self.assertEqual(retained, ["5.2.9"])

    def test_version_comparison_is_numeric_and_rejects_malformed_values(self):
        self.assertGreater(compare_versions("5.10.0", "5.9.9"), 0)
        with self.assertRaises(UpdateError):
            compare_versions("5.3", "5.3.0")

    def test_unsupported_manifest_format_and_unsafe_path_are_rejected(self):
        with self.assertRaises(UpdateError):
            self._manifest(manifest_format_version=99)
        with self.assertRaises(UpdateError) as raised:
            self._manifest(package_url="https://updates.example.invalid/releases/../evil.exe")
        self.assertEqual(raised.exception.code, "MANIFEST_PATH_UNSAFE")

    def test_production_manifest_transport_and_signer_identity_are_strict(self):
        with self.assertRaises(UpdateError) as raised:
            validate_manifest_for_install(self._manifest(package_url="http://localhost/update.exe"), "5.3.0", 1, "production")
        self.assertEqual(raised.exception.code, "TRANSPORT_INSECURE")
        with self.assertRaises(UpdateError) as raised:
            stage_package(
                self._manifest(), self.update_root, "production", False, downloader=self._copy_download,
                signature_verifier=lambda path: SignatureResult("Valid", "CN=ATTACKER", "BAD"),
                disk_usage=self._space,
            )
        self.assertEqual(raised.exception.code, "SIGNER_IDENTITY_MISMATCH")

    def test_package_substitution_after_verification_is_detected_before_installer(self):
        state_path, state = self._prepare()
        package = Path(state["package_path"])
        package.chmod(0o666)
        package.write_bytes(b"malicious substitution")
        called = []
        with self.assertRaises(UpdateError) as raised:
            execute_prepared_update(
                state_path, lambda: 1, installer_runner=lambda package, live: called.append(True) or 0,
                signature_verifier=self._unsigned,
            )
        self.assertIn(raised.exception.code, {"PACKAGE_SIZE_MISMATCH", "PACKAGE_HASH_MISMATCH"})
        self.assertEqual(called, [])

    def test_manifest_parser_rejects_tampering_and_unknown_future_format(self):
        manifest = self._manifest().to_dict()
        manifest["aurelia_version"] = "not-a-version"
        with self.assertRaises(UpdateError):
            UpdateManifest.from_dict(manifest)

    def test_network_interruption_and_partial_download_leave_no_valid_stage(self):
        def interrupted(url, destination, expected_size):
            Path(destination).write_bytes(b"partial")
            raise OSError("synthetic network interruption")

        with self.assertRaises(OSError):
            stage_package(
                self._manifest(), self.update_root, "test", True, downloader=interrupted,
                signature_verifier=self._unsigned, disk_usage=self._space,
            )
        self.assertEqual(list(self.update_root.iterdir()), [])

    def test_disk_preflight_blocks_before_download(self):
        called = []
        with self.assertRaises(UpdateError) as raised:
            stage_package(
                self._manifest(), self.update_root, "test", True,
                downloader=lambda *args: called.append(True), signature_verifier=self._unsigned,
                disk_usage=lambda path: SimpleNamespace(free=1),
            )
        self.assertEqual(raised.exception.code, "DISK_SPACE_INSUFFICIENT")
        self.assertEqual(called, [])

    def test_tampered_state_cannot_redirect_application_replacement(self):
        state_path, state = self._prepare()
        state["live_app_dir"] = str(self.root)
        state_path.write_text(json.dumps(state), encoding="utf-8")
        with self.assertRaises(UpdateError) as raised:
            execute_prepared_update(state_path, lambda: 1, signature_verifier=self._unsigned)
        self.assertEqual(raised.exception.code, "STATE_PATH_UNSAFE")

    def test_file_backed_manifest_provider_is_available_only_for_test_mode(self):
        manifest_path = self.root / "update.json"
        manifest_path.write_text(json.dumps(self._manifest().to_dict()), encoding="utf-8")
        loaded = fetch_manifest(manifest_path.as_uri(), "test")
        self.assertEqual(loaded.aurelia_version, "5.3.1")
        with self.assertRaises(UpdateError) as raised:
            fetch_manifest(manifest_path.as_uri(), "production")
        self.assertEqual(raised.exception.code, "TRANSPORT_INSECURE")

    def test_update_ui_is_a_settings_only_explicit_admin_flow(self):
        root = Path(__file__).resolve().parents[1]
        settings = (root / "app" / "templates" / "settings.html").read_text(encoding="utf-8")
        update_page = (root / "app" / "templates" / "update_settings.html").read_text(encoding="utf-8")
        main = (root / "app" / "main.py").read_text(encoding="utf-8")
        self.assertIn('href="/settings/update"', settings)
        self.assertIn('action="/settings/update/install"', update_page)
        self.assertIn('type="submit"', update_page)
        self.assertIn('require_admin(request)', main[main.index('def update_install'):main.index('@app.post("/settings/company")')])
        self.assertNotIn("Mise à jour", (root / "app" / "templates" / "base.html").read_text(encoding="utf-8"))
        manifest = self._manifest().to_dict()
        manifest["manifest_format_version"] = 2
        with self.assertRaises(UpdateError):
            UpdateManifest.from_dict(manifest)


if __name__ == "__main__":
    unittest.main()
