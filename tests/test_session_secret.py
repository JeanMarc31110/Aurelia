import io
import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.local_config import ensure_local_directories, load_local_config
from app.services.session_secret import get_or_create_session_secret


class SessionSecretTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def config(self, name="installation"):
        data_dir = self.root / name
        environment = {
            "AURELIA_DATA_DIR": str(data_dir),
            "AURELIA_DB_PATH": str(data_dir / "aurelia_v5.db"),
            "AURELIA_DOCUMENTS_DIR": str(self.root / f"{name}-documents"),
        }
        with patch.dict(os.environ, environment, clear=False):
            return ensure_local_directories(load_local_config(self.root / f"{name}-program"))

    def test_absent_secret_is_created(self):
        config = self.config()
        secret = get_or_create_session_secret(config)
        self.assertTrue(config.session_secret_path.is_file())
        self.assertGreaterEqual(len(secret), 32)
        self.assertEqual(config.session_secret_path.read_text(encoding="ascii"), secret)

    def test_existing_secret_is_reused(self):
        config = self.config()
        expected = "existing-installation-secret-0123456789abcdef"
        config.session_secret_path.write_text(expected, encoding="ascii")
        self.assertEqual(get_or_create_session_secret(config), expected)
        self.assertEqual(config.session_secret_path.read_text(encoding="ascii"), expected)

    def test_different_installation_directories_get_different_secrets(self):
        first = get_or_create_session_secret(self.config("first"))
        second = get_or_create_session_secret(self.config("second"))
        self.assertNotEqual(first, second)

    def test_simulated_restart_reuses_the_same_secret(self):
        config = self.config()
        first = get_or_create_session_secret(config)
        restarted_config = self.config()
        self.assertEqual(get_or_create_session_secret(restarted_config), first)

    def test_environment_secret_is_not_a_production_fallback(self):
        config = self.config()
        hardcoded = "hardcoded-environment-secret-must-never-be-used"
        with patch.dict(os.environ, {"AURELIA_SESSION_SECRET": hardcoded}, clear=False):
            actual = get_or_create_session_secret(config)
        self.assertNotEqual(actual, hardcoded)
        self.assertEqual(config.session_secret_path.read_text(encoding="ascii"), actual)

    def test_default_production_path_is_localappdata(self):
        local_app_data = self.root / "LocalAppData"
        with patch.dict(os.environ, {
            "LOCALAPPDATA": str(local_app_data), "USERPROFILE": str(self.root / "Profile")
        }, clear=False):
            os.environ.pop("AURELIA_DATA_DIR", None)
            os.environ.pop("AURELIA_DB_PATH", None)
            config = load_local_config(self.root / "program")
        self.assertEqual(
            config.session_secret_path,
            (local_app_data / "Aurelia" / "Secrets" / "session.secret").resolve(),
        )

    def test_pyinstaller_runtime_uses_localappdata_secret(self):
        local_app_data = self.root / "FrozenLocalAppData"
        executable = self.root / "installed" / "Aurelia.exe"
        executable.parent.mkdir(parents=True)
        with patch.dict(os.environ, {
                "LOCALAPPDATA": str(local_app_data), "USERPROFILE": str(self.root / "FrozenProfile")
            }, clear=False), \
                patch.object(sys, "frozen", True, create=True), \
                patch.object(sys, "executable", str(executable)):
            os.environ.pop("AURELIA_DATA_DIR", None)
            os.environ.pop("AURELIA_DB_PATH", None)
            config = load_local_config(executable.parent)
            secret = get_or_create_session_secret(config)
        self.assertTrue(config.session_secret_path.is_file())
        self.assertEqual(config.session_secret_path.parent, (local_app_data / "Aurelia" / "Secrets").resolve())
        self.assertEqual(config.session_secret_path.read_text(encoding="ascii"), secret)

    def test_secret_value_is_never_logged(self):
        config = self.config()
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        root_logger = logging.getLogger()
        root_logger.addHandler(handler)
        try:
            secret = get_or_create_session_secret(config)
            logging.getLogger("aurelia.test").info("session secret initialized")
        finally:
            root_logger.removeHandler(handler)
        self.assertNotIn(secret, stream.getvalue())
        self.assertFalse(config.log_path.exists())


if __name__ == "__main__":
    unittest.main()
