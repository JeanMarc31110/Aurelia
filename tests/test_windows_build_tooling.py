import json
import tempfile
import unittest
from pathlib import Path

from tools.build_windows_release import (
    BuildError,
    expected_python_version,
    generate_version_file,
    inspect_bundle,
    make_build_manifest,
    parse_pinned_requirements,
    read_product_version,
    required_bundle_paths,
    schema_version_from_source,
)


ROOT = Path(__file__).resolve().parents[1]


class WindowsBuildToolingTests(unittest.TestCase):
    def test_version_txt_is_the_product_version_authority(self):
        self.assertEqual(read_product_version(ROOT), "5.3.0")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "VERSION.txt").write_text("5.4\n", encoding="utf-8")
            with self.assertRaises(BuildError):
                read_product_version(root)

    def test_python_and_all_build_dependencies_are_pinned(self):
        self.assertEqual(expected_python_version(), "3.14.6")
        requirements = parse_pinned_requirements()
        self.assertEqual(requirements["pyinstaller"], "6.22.2")
        self.assertEqual(requirements["pyinstaller-hooks-contrib"], "2026.7")
        self.assertGreater(len(requirements), 20)

    def test_schema_version_is_read_from_source_for_manifest(self):
        expected = schema_version_from_source()
        namespace = {}
        source = (ROOT / "app" / "db.py").read_text(encoding="utf-8")
        line = next(row for row in source.splitlines() if row.startswith("CURRENT_SCHEMA_VERSION"))
        exec(line, namespace)
        self.assertEqual(expected, namespace["CURRENT_SCHEMA_VERSION"])

    def test_required_bundle_inventory_covers_runtime_resources(self):
        required = required_bundle_paths()
        self.assertIn("Aurelia.exe", required)
        self.assertIn("_internal/app/templates/login.html", required)
        self.assertIn("_internal/app/static/style.css", required)
        self.assertIn("_internal/config/policy.json", required)
        for language in ("eng", "fra", "spa", "osd"):
            self.assertIn(f"_internal/resources/tesseract/tessdata/{language}.traineddata", required)

    def _fake_bundle(self, root):
        for relative in required_bundle_paths():
            path = root.joinpath(*relative.split("/"))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"synthetic")

    def test_bundle_inspection_blocks_forbidden_data_and_text_path_leaks(self):
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary)
            self._fake_bundle(bundle)
            self.assertEqual(inspect_bundle(bundle)["forbidden_files"], [])
            forbidden = bundle / "_internal" / ".env"
            forbidden.write_text("SECRET=synthetic", encoding="utf-8")
            with self.assertRaises(BuildError):
                inspect_bundle(bundle)
            forbidden.unlink()
            certificate = bundle / "_internal" / "certifi" / "cacert.pem"
            certificate.parent.mkdir(parents=True)
            certificate.write_text("public CA bundle", encoding="ascii")
            self.assertEqual(inspect_bundle(bundle)["forbidden_files"], [])
            private_pem = bundle / "_internal" / "unexpected.pem"
            private_pem.write_text("synthetic private material", encoding="ascii")
            with self.assertRaises(BuildError):
                inspect_bundle(bundle)
            private_pem.unlink()
            resource = bundle / "_internal" / "config" / "policy.json"
            resource.write_text(r'C:\Users\developer\Desktop\Aurelia', encoding="utf-8")
            with self.assertRaises(BuildError):
                inspect_bundle(bundle, [r"C:\Users\developer"])

    def test_manifest_contract_contains_required_provenance(self):
        manifest = make_build_manifest(
            aurelia_version="5.3.0", source_commit_sha="a" * 40,
            build_utc="2026-09-26T00:00:00+00:00", python_version="3.14.6",
            pyinstaller_version="6.22.2", requirements_lock_sha256="b" * 64,
            build_spec_sha256="c" * 64, executable_sha256="d" * 64,
            file_count=10, total_artifact_size=100, tesseract_version="tesseract 5.4.0",
            database_schema_version=2, build_mode="onedir",
        )
        self.assertEqual(manifest["build_manifest_version"], 1)
        self.assertEqual(manifest["build_mode"], "onedir")
        json.dumps(manifest)
        with self.assertRaises(BuildError):
            make_build_manifest(aurelia_version="5.3.0")

    def test_generated_windows_metadata_uses_dynamic_version(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "version.txt"
            generate_version_file("7.8.9", "FEWURA", target)
            content = target.read_text(encoding="utf-8")
            self.assertIn("FileVersion', '7.8.9.0'", content)
            self.assertIn("ProductVersion', '7.8.9.0'", content)
            self.assertIn("ProductName', 'Aurelia'", content)
            self.assertIn("CompanyName', 'FEWURA'", content)
            self.assertIn("OriginalFilename', 'Aurelia.exe'", content)

    def test_canonical_script_does_not_build_an_installer(self):
        canonical = (ROOT / "BUILD_WINDOWS_RELEASE.bat").read_text(encoding="utf-8").lower()
        legacy = (ROOT / "CONSTRUIRE_SETUP_WINDOWS.bat").read_text(encoding="utf-8").lower()
        self.assertIn("requirements-build.txt", canonical)
        self.assertIn("tools\\build_windows_release.py", canonical)
        self.assertNotIn("iscc", canonical)
        self.assertIn("deprecated", legacy)


if __name__ == "__main__":
    unittest.main()
