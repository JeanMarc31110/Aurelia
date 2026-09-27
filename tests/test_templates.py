import os
import re
import sqlite3
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

from app import db
from app.auth import create_admin,hash_password,verify_password
from app.db import init_db


ROOT = Path(__file__).resolve().parents[1]


class TemplateCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.database_path = Path(cls.temporary.name) / "templates.db"
        cls.db_patch = patch.object(db, "DB_PATH", cls.database_path)
        cls.db_patch.start()
        init_db()
        create_admin("admin-test", "Admin-Test-Only!")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            cls.port = sock.getsockname()[1]

        env = os.environ.copy()
        env["AURELIA_DB_PATH"] = str(Path(cls.temporary.name) / "templates.db")
        env["AURELIA_DATA_DIR"] = str(Path(cls.temporary.name) / "data")
        env["AURELIA_DOCUMENTS_DIR"] = str(Path(cls.temporary.name) / "documents")
        env["AURELIA_WATCHER_ENABLED"] = "0"
        env["AURELIA_BACKUPS_ENABLED"] = "0"
        env["PYTHONUNBUFFERED"] = "1"
        cls.server = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(cls.port),
                "--log-level",
                "warning",
            ],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        cls.base_url = f"http://127.0.0.1:{cls.port}"
        cls.session = requests.Session()

        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if cls.server.poll() is not None:
                output = cls.server.stdout.read() if cls.server.stdout else ""
                raise RuntimeError(f"Aurelia server exited during startup:\n{output}")
            try:
                response = cls.session.get(f"{cls.base_url}/login", timeout=0.5)
                if response.status_code == 200:
                    return
            except requests.RequestException:
                pass
            time.sleep(0.1)

        cls._stop_server()
        raise RuntimeError("Aurelia server did not expose /login within 20 seconds")

    @classmethod
    def _stop_server(cls):
        server = getattr(cls, "server", None)
        if server and server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)

    @classmethod
    def tearDownClass(cls):
        cls.session.close()
        cls._stop_server()
        cls.db_patch.stop()
        cls.temporary.cleanup()

    def test_login_page_and_invalid_credentials(self):
        page = self.session.get(f"{self.base_url}/login", timeout=5)
        self.assertEqual(page.status_code, 200)
        self.assertIn("AURELIA V5", page.text)
        self.assertIn('name="username"', page.text)
        self.assertIn('name="password"', page.text)

        invalid = self.session.post(
            f"{self.base_url}/login",
            data={"username": "admin", "password": "mot-de-passe-incorrect"},
            timeout=5,
        )
        self.assertEqual(invalid.status_code, 401)
        self.assertIn("AURELIA V5", invalid.text)
        self.assertIn("Identifiants incorrects", invalid.text)

    def test_password_change_is_persisted_after_database_reopen(self):
        temporary_password="Temporary-Admin-Only!"
        final_password="Persisted-Admin-Only!"
        connection=sqlite3.connect(self.database_path)
        connection.execute(
            "UPDATE users SET password_hash=?,must_change_password=1 WHERE username=?",
            (hash_password(temporary_password),"admin-test"),
        )
        connection.commit();connection.close()

        browser=requests.Session()
        try:
            login=browser.post(
                f"{self.base_url}/login",
                data={"username":"admin-test","password":temporary_password},
                allow_redirects=False,timeout=5,
            )
            self.assertEqual(login.status_code,303)
            self.assertEqual(login.headers.get("location"),"/change-password")
            changed=browser.post(
                f"{self.base_url}/change-password",
                data={"current_password":temporary_password,"new_password":final_password,
                      "confirmation":final_password},
                allow_redirects=False,timeout=5,
            )
            self.assertEqual(changed.status_code,303)
            self.assertEqual(changed.headers.get("location"),"/login?configured=1")
        finally:
            browser.close()

        connection=sqlite3.connect(self.database_path)
        row=connection.execute(
            "SELECT password_hash,must_change_password FROM users WHERE username=?",("admin-test",)
        ).fetchone()
        connection.close()
        self.assertTrue(verify_password(final_password,row[0]))
        self.assertFalse(verify_password(temporary_password,row[0]))
        self.assertEqual(row[1],0)

        restarted_session=requests.Session()
        try:
            persisted=restarted_session.post(
                f"{self.base_url}/login",data={"username":"admin-test","password":final_password},
                allow_redirects=False,timeout=5,
            )
            rejected=restarted_session.post(
                f"{self.base_url}/login",data={"username":"admin-test","password":temporary_password},
                allow_redirects=False,timeout=5,
            )
            self.assertEqual(persisted.status_code,303)
            self.assertEqual(persisted.headers.get("location"),"/")
            self.assertEqual(rejected.status_code,401)
        finally:
            restarted_session.close()

    def test_admin_dashboard_logout_cycle(self):
        authenticated = self.session.post(
            f"{self.base_url}/login",
            data={"username": "admin-test", "password": "Admin-Test-Only!"},
            allow_redirects=False,
            timeout=5,
        )
        self.assertEqual(authenticated.status_code, 303)
        self.assertEqual(authenticated.headers.get("location"), "/")

        dashboard = self.session.get(f"{self.base_url}/", timeout=5)
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn("Tableau de bord", dashboard.text)
        self.assertIn("Déconnexion", dashboard.text)
        self.assertIn("Aucune société active n’est configurée", dashboard.text)

        for path, marker in (("/bank", "Importer des transactions bancaires"),
                             ("/payments", "Échéances et paiements"),
                             ("/exports/accounting", "Comptabilité"),
                             ("/integrations", "Import e-mail local"),
                             ("/settings", "Configuration locale"),
                             ("/emails", "Importer un fichier .eml"),
                             ("/settings/company", "Informations de l’entreprise"),
                             ("/settings/ocr", "Configuration technique locale"),
                             ("/settings/supplier-banks", "Historique des coordonnées bancaires détectées")):
            with self.subTest(path=path):
                page = self.session.get(f"{self.base_url}{path}", timeout=5)
                self.assertEqual(page.status_code, 200)
                self.assertIn(marker, page.text)
                if path == "/exports/accounting":
                    self.assertIn("Avant le premier export", page.text)
                    self.assertIn("Format EBP CSV à valider", page.text)

        dashboard = self.session.get(f"{self.base_url}/", timeout=5)
        nav_labels = re.findall(r'<a class="nav-link[^>]*>.*?<span>(.*?)</span></a>', dashboard.text, re.S)
        self.assertEqual(nav_labels, ["Accueil", "À traiter", "Banque", "Comptabilité", "Intégrations", "Paramètres"])

        logout = self.session.get(f"{self.base_url}/logout", timeout=5)
        self.assertEqual(logout.status_code, 200)
        self.assertIn("AURELIA V5", logout.text)

        protected = self.session.get(
            f"{self.base_url}/",
            allow_redirects=False,
            timeout=5,
        )
        self.assertEqual(protected.status_code, 303)
        self.assertEqual(protected.headers.get("location"), "/login")


if __name__ == "__main__":
    unittest.main()
