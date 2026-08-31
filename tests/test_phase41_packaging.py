import errno
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

from app import db
from app.auth import authenticate, create_admin, hash_password, verify_password
from app.db import connect, init_db
from app.instance_lock import InstanceLock
from app.local_config import ensure_local_directories, load_local_config
from app.resource_paths import resource_path
from app.services.data_migration import database_snapshot, migrate_legacy_database
from app.services.onboarding import create_initial_setup, setup_required
from app.services.session_secret import get_or_create_session_secret


ROOT = Path(__file__).resolve().parents[1]


class Phase41PackagingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.environment = patch.dict(os.environ, {
            "AURELIA_DATA_DIR": str(self.root / "data"),
            "AURELIA_DOCUMENTS_DIR": str(self.root / "documents"),
            "AURELIA_DB_PATH": str(self.root / "data" / "aurelia_v5.db"),
            "AURELIA_WATCHER_ENABLED": "0", "AURELIA_BACKUPS_ENABLED": "0",
        }, clear=False)
        self.environment.start()
        os.environ.pop("AURELIA_SESSION_SECRET", None)
        self.config = ensure_local_directories(load_local_config(self.root / "program"))
        self.db_patch = patch.object(db, "DB_PATH", self.config.sqlite_path)
        self.db_patch.start();init_db()

    def tearDown(self):
        self.db_patch.stop();self.environment.stop();self.temporary.cleanup()

    def test_session_secret_is_unique_between_installations_and_not_hardcoded(self):
        first = get_or_create_session_secret(self.config)
        other_environment = patch.dict(os.environ, {
            "AURELIA_DATA_DIR": str(self.root / "other-data"),
            "AURELIA_DB_PATH": str(self.root / "other-data" / "aurelia_v5.db"),
        }, clear=False)
        with other_environment:
            other = get_or_create_session_secret(load_local_config(self.root / "other-program"))
        self.assertGreaterEqual(len(first), 32);self.assertNotEqual(first, other)
        self.assertNotIn("DEV-CHANGE-ME", first)

    def test_session_secret_persists_and_is_not_logged(self):
        first = get_or_create_session_secret(self.config)
        self.assertEqual(first, get_or_create_session_secret(self.config))
        self.assertEqual(self.config.session_secret_path.read_text(encoding="ascii"), first)
        self.assertFalse(self.config.log_path.exists())

    def test_first_setup_creates_hashed_admin_and_company_atomically(self):
        self.assertTrue(setup_required())
        create_initial_setup("owner", "Strong-Test-Password!", "Strong-Test-Password!",
                             "Synthetic Company", "TEST-TAX-001", "FR", "EUR")
        self.assertFalse(setup_required())
        user = authenticate("owner", "Strong-Test-Password!")
        self.assertIsNotNone(user);self.assertNotEqual(user["password_hash"], "Strong-Test-Password!")
        self.assertTrue(verify_password("Strong-Test-Password!", user["password_hash"]))

    def test_first_setup_refuses_short_or_mismatched_password(self):
        with self.assertRaises(ValueError):
            create_initial_setup("owner", "short", "short", "Company", "TAX", "FR")
        with self.assertRaises(ValueError):
            create_initial_setup("owner", "Password-One!", "Password-Two!", "Company", "TAX", "FR")

    def test_existing_admin_is_preserved_by_reinitialisation(self):
        create_admin("existing", "Existing-Test-Password!")
        before = authenticate("existing", "Existing-Test-Password!")["password_hash"]
        init_db()
        after = authenticate("existing", "Existing-Test-Password!")["password_hash"]
        self.assertEqual(before, after)

    def test_legacy_admin_is_marked_for_password_renewal(self):
        legacy = self.root / "legacy-users.db"
        connection = sqlite3.connect(legacy)
        connection.execute("CREATE TABLE users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password_hash TEXT,role TEXT,active INTEGER,created_at TEXT)")
        connection.execute("INSERT INTO users VALUES(1,'legacy',?,'admin',1,CURRENT_TIMESTAMP)",
                           (hash_password("Legacy-Test-Password!"),))
        connection.commit();connection.close()
        with patch.object(db, "DB_PATH", legacy):
            init_db();connection=connect()
            flag=connection.execute("SELECT must_change_password FROM users WHERE id=1").fetchone()[0]
            connection.close()
        self.assertEqual(flag, 1)

    def test_windows_single_instance_and_release(self):
        first=InstanceLock(self.config.data_dir);second=InstanceLock(self.config.data_dir)
        self.assertTrue(first.acquire());self.assertFalse(second.acquire())
        self.assertTrue(first.release());self.assertTrue(second.acquire());self.assertTrue(second.release())

    def test_localappdata_default_is_configurable(self):
        os.environ.pop("AURELIA_DATA_DIR", None);os.environ.pop("AURELIA_DB_PATH", None)
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.root / "LocalAppData")}, clear=False):
            config=load_local_config(self.root / "installed-program")
        self.assertEqual(config.data_dir, (self.root / "LocalAppData" / "Aurelia").resolve())
        self.assertEqual(config.sqlite_path.parent, config.data_dir)

    def test_repository_dist_build_detects_phase4_legacy_database(self):
        project = self.root / "repository"
        program = project / "dist" / "Aurelia"
        legacy = project / "data" / "aurelia_v5.db"
        program.mkdir(parents=True);legacy.parent.mkdir(parents=True)
        legacy.write_bytes(b"synthetic database marker")
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AURELIA_LEGACY_DB_PATH", None)
            config = load_local_config(program)
        self.assertEqual(config.legacy_sqlite_path, legacy.resolve())
        self.assertTrue(config.legacy_data_layout)

    def _seed_legacy_database(self, path):
        with patch.object(db, "DB_PATH", path):
            init_db();connection=connect()
            try:
                connection.execute("INSERT INTO users(username,password_hash,role) VALUES('legacy-admin',?,'admin')",
                                   (hash_password("Legacy-Test-Password!"),))
                connection.execute("INSERT INTO companies(legal_name,currency,active) VALUES('Legacy Company','EUR',1)")
                connection.execute("INSERT INTO invoices(invoice_number,status,raw_json) VALUES('LEGACY-001','APPROVED','{}')")
                connection.commit()
            finally:
                connection.close()

    def test_legacy_migration_preserves_source_counts_and_integrity(self):
        legacy=self.root / "legacy" / "aurelia_v5.db";legacy.parent.mkdir()
        self._seed_legacy_database(legacy)
        self.config.sqlite_path.unlink(missing_ok=True)
        with patch.dict(os.environ,{"AURELIA_LEGACY_DB_PATH":str(legacy)},clear=False):
            config=load_local_config(self.root / "program")
            result=migrate_legacy_database(config,force=True)
        self.assertEqual(result["status"],"migrated");self.assertTrue(legacy.exists())
        self.assertEqual(database_snapshot(legacy),database_snapshot(config.sqlite_path))
        self.assertEqual(database_snapshot(config.sqlite_path)["integrity"],"ok")
        self.assertTrue(Path(result["safety_backup"]).is_file())

    def test_legacy_migration_cross_device_fallback_is_exclusive_and_verified(self):
        legacy=self.root / "legacy-cross-device.db";self._seed_legacy_database(legacy)
        self.config.sqlite_path.unlink(missing_ok=True)
        with patch.dict(os.environ,{"AURELIA_LEGACY_DB_PATH":str(legacy)},clear=False), \
             patch("app.services.data_migration.os.replace",side_effect=OSError(errno.EXDEV,"synthetic")):
            result=migrate_legacy_database(self.config,force=True)
        self.assertEqual(result["publication"],"exclusive_copy")
        self.assertEqual(database_snapshot(legacy),database_snapshot(self.config.sqlite_path))

    def test_existing_localappdata_database_is_never_overwritten(self):
        legacy=self.root / "legacy.db";self._seed_legacy_database(legacy)
        connection=sqlite3.connect(self.config.sqlite_path)
        connection.execute("INSERT INTO settings(key,value) VALUES('target-marker','keep-me')")
        connection.commit();connection.close()
        with patch.dict(os.environ,{"AURELIA_LEGACY_DB_PATH":str(legacy)},clear=False):
            result=migrate_legacy_database(self.config,force=True)
        self.assertEqual(result["status"],"target_exists")
        connection=sqlite3.connect(self.config.sqlite_path)
        self.assertEqual(connection.execute("SELECT value FROM settings WHERE key='target-marker'").fetchone()[0],"keep-me")
        connection.close()

    def test_development_resource_paths_resolve_templates_and_static(self):
        self.assertTrue(resource_path("app","templates","login.html").is_file())
        self.assertTrue(resource_path("app","static","style.css").is_file())

    def test_pyinstaller_resource_root_is_supported(self):
        bundle=self.root / "bundle";bundle.mkdir()
        with patch.object(sys,"frozen",True,create=True),patch.object(sys,"_MEIPASS",str(bundle),create=True):
            from app import resource_paths
            self.assertEqual(resource_paths.resource_path("app","templates"),bundle / "app" / "templates")

    def test_bundled_tesseract_has_priority(self):
        executable=self.root / "resources" / "tesseract" / "tesseract.exe"
        executable.parent.mkdir(parents=True);executable.write_bytes(b"synthetic executable")
        with patch("app.services.ocr.resource_path",return_value=executable):
            from app.services.ocr import _candidate_paths
            self.assertEqual(next(_candidate_paths()),("bundled",executable))

    def test_tesseract_development_fallback_remains_available(self):
        missing=self.root / "missing.exe";system=self.root / "system-tesseract.exe";system.write_bytes(b"x")
        with patch("app.services.ocr.resource_path",return_value=missing), \
             patch("app.services.ocr.get_setting",return_value=""), \
             patch("app.services.ocr.shutil.which",return_value=str(system)):
            from app.services.ocr import _candidate_paths
            self.assertIn(("path",system),list(_candidate_paths()))

    def test_browser_opens_only_after_health_is_ready(self):
        from aurelia_launcher import wait_and_open_browser
        opened=[]
        with patch("aurelia_launcher.aurelia_ready",side_effect=[False,False,True]), \
             patch("aurelia_launcher.time.sleep",return_value=None):
            self.assertTrue(wait_and_open_browser(opened.append,timeout=2))
        self.assertEqual(opened,["http://127.0.0.1:8000"])


class Phase41FirstLaunchHttpTest(unittest.TestCase):
    def test_fresh_http_install_requires_setup_then_accepts_new_admin(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);database=root / "data" / "fresh.db"
            with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as sock:
                sock.bind(("127.0.0.1",0));port=sock.getsockname()[1]
            environment=os.environ.copy();environment.update({
                "AURELIA_DB_PATH":str(database),"AURELIA_DATA_DIR":str(root / "data"),
                "AURELIA_DOCUMENTS_DIR":str(root / "documents"),"AURELIA_WATCHER_ENABLED":"0",
                "AURELIA_BACKUPS_ENABLED":"0","PYTHONUNBUFFERED":"1",
            })
            server=subprocess.Popen([sys.executable,"-m","uvicorn","app.main:app","--host","127.0.0.1",
                "--port",str(port),"--log-level","warning"],cwd=ROOT,env=environment,
                stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
            try:
                base=f"http://127.0.0.1:{port}";deadline=time.monotonic()+20
                while time.monotonic()<deadline:
                    try:
                        if requests.get(base+"/health",timeout=.4).status_code==200:break
                    except requests.RequestException:time.sleep(.1)
                login=requests.get(base+"/login",allow_redirects=False,timeout=5)
                self.assertEqual((login.status_code,login.headers.get("location")),(303,"/setup"))
                setup=requests.post(base+"/setup",data={"username":"owner","password":"Owner-Test-Password!",
                    "password_confirmation":"Owner-Test-Password!","legal_name":"Fresh Company",
                    "tax_id":"FRESH-TAX","country":"FR","currency":"EUR"},allow_redirects=False,timeout=5)
                self.assertEqual((setup.status_code,setup.headers.get("location")),(303,"/login?configured=1"))
                authenticated=requests.post(base+"/login",data={"username":"owner","password":"Owner-Test-Password!"},
                                            allow_redirects=False,timeout=5)
                self.assertEqual((authenticated.status_code,authenticated.headers.get("location")),(303,"/"))
            finally:
                server.terminate();server.wait(timeout=10)
                if server.stdout:server.stdout.close()


if __name__ == "__main__":unittest.main()
