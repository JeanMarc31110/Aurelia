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
from app.auth import create_admin
from app.db import connect, init_db
from app.parsers.pdf_parser import _parse_text
from app.services.company import get_active_company
from app.services.orchestrator import process_invoice


ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC_INVOICE = """FACTURE N° INV-2026-001
Date : 15 février 2026
ÉMETTEUR
INNOVATECH SOFTWARE E IA SL
Calle de la Innovación 12, Madrid
NIF : B22714539
CLIENT
Captain Guillaume
10 rue des Tests, Toulouse
Désignation
Qté
Prix unitaire HT
Total HT
Accompagnement Google Ads SEA
1
75 €
75 €
Assistance technique & intégration SEO
1
75 €
75 €
Total HT : 150 €
TVA : 0 €
Total à payer : 150 €
TVA non applicable – autoliquidation (art. 196 directive 2006/112/CE)
"""


class CompanyProfileTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "test.db"
        self.db_patch = patch.object(db, "DB_PATH", self.database_path)
        self.db_patch.start()
        init_db()
        create_admin("admin-test", "Admin-Test-Only!")

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        environment = os.environ.copy()
        environment["AURELIA_DB_PATH"] = str(self.database_path)
        environment["AURELIA_DATA_DIR"] = str(Path(self.temporary_directory.name) / "data")
        environment["AURELIA_DOCUMENTS_DIR"] = str(Path(self.temporary_directory.name) / "documents")
        environment["AURELIA_WATCHER_ENABLED"] = "0"
        environment["AURELIA_BACKUPS_ENABLED"] = "0"
        environment["PYTHONUNBUFFERED"] = "1"
        self.server = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
             "--port", str(self.port), "--log-level", "warning"],
            cwd=ROOT, env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.session = requests.Session()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if self.server.poll() is not None:
                output = self.server.stdout.read() if self.server.stdout else ""
                raise RuntimeError(f"Aurelia server exited during startup:\n{output}")
            try:
                if self.session.get(f"{self.base_url}/login", timeout=.5).status_code == 200:
                    break
            except requests.RequestException:
                pass
            time.sleep(.1)
        else:
            self._stop_server()
            raise RuntimeError("Aurelia server did not start within 20 seconds")

        login = self.session.post(
            f"{self.base_url}/login", data={"username": "admin-test", "password": "Admin-Test-Only!"},
            allow_redirects=False, timeout=5,
        )
        self.assertEqual(login.status_code, 303)

    def _stop_server(self):
        if getattr(self, "server", None) and self.server.poll() is None:
            self.server.terminate()
            try:
                self.server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.server.kill()
                self.server.wait(timeout=5)
        if getattr(self, "server", None) and self.server.stdout:
            self.server.stdout.close()

    def tearDown(self):
        self.session.close()
        self._stop_server()
        self.db_patch.stop()
        self.temporary_directory.cleanup()

    def test_company_form_sale_direction_totals_and_duplicate(self):
        empty_page = self.session.get(f"{self.base_url}/settings/company", timeout=5)
        self.assertEqual(empty_page.status_code, 200)
        self.assertIn("Mon entreprise", empty_page.text)

        saved = self.session.post(
            f"{self.base_url}/settings/company",
            data={
                "legal_name": "INNOVATECH SOFTWARE E IA SL", "trade_name": "Innovatech",
                "country": "ES", "nif": "B22714539", "aliases": "INNOVATECH SOFTWARE IA",
            },
            allow_redirects=False, timeout=5,
        )
        self.assertEqual(saved.status_code, 303)
        self.assertEqual(saved.headers["location"], "/settings/company?saved=1")
        company = get_active_company()
        self.assertEqual(company["legal_name"], "INNOVATECH SOFTWARE E IA SL")
        self.assertEqual(company["nif"], "B22714539")

        updated = self.session.post(
            f"{self.base_url}/settings/company",
            data={
                "legal_name": "INNOVATECH SOFTWARE E IA SL", "trade_name": "Innovatech Europe",
                "country": "ES", "nif": "B22714539", "aliases": "INNOVATECH SOFTWARE IA",
            },
            allow_redirects=False, timeout=5,
        )
        self.assertEqual(updated.status_code, 303)
        updated_company = get_active_company()
        self.assertEqual(updated_company["id"], company["id"])
        self.assertEqual(updated_company["trade_name"], "Innovatech Europe")

        parsed = _parse_text(SYNTHETIC_INVOICE)
        self.assertEqual(parsed["direction"], "sale")
        self.assertEqual(parsed["vat_amount"], 0.0)
        self.assertEqual(parsed["vat_mechanism"], "reverse_charge")
        self.assertEqual(len(parsed["lines"]), 2)

        first = process_invoice(parsed, "admin")
        second = process_invoice(parsed, "admin")
        self.assertNotEqual(first["status"], "DUPLICATE")
        self.assertEqual(second["status"], "DUPLICATE")
        self.assertLess(first["accounting_proposal"]["confidence"], 0.85)
        totals = next(item for item in first["findings"] if item["code"] == "totals")
        self.assertTrue(totals["ok"])

        con = connect()
        invoice = con.execute("SELECT company_id,direction FROM invoices WHERE fingerprint=?", (first["fingerprint"],)).fetchone()
        con.close()
        self.assertEqual(invoice["company_id"], company["id"])
        self.assertEqual(invoice["direction"], "sale")

    def test_general_extraction_finding_is_not_labeled_ocr(self):
        parsed = _parse_text(SYNTHETIC_INVOICE, company={})
        parsed["invoice_number"] = "UNKNOWN-DIRECTION-001"
        result = process_invoice(parsed, "admin")
        agents = {finding["agent"] for finding in result["findings"]}
        self.assertIn("EXTRACTION", agents)
        self.assertNotIn("OCR", agents)

    def test_additive_migration_preserves_existing_invoice(self):
        legacy_path = Path(self.temporary_directory.name) / "legacy.db"
        legacy = sqlite3.connect(legacy_path)
        legacy.execute("CREATE TABLE invoices(id INTEGER PRIMARY KEY, fingerprint TEXT, raw_json TEXT NOT NULL)")
        legacy.execute("INSERT INTO invoices(fingerprint,raw_json) VALUES('legacy-fingerprint','{}')")
        legacy.commit()
        legacy.close()

        with patch.object(db, "DB_PATH", legacy_path):
            init_db()
            migrated = db.connect()
            columns = {row["name"] for row in migrated.execute("PRAGMA table_info(invoices)")}
            row = migrated.execute("SELECT fingerprint,company_id FROM invoices WHERE id=1").fetchone()
            migrated.close()
        self.assertIn("company_id", columns)
        self.assertEqual(row["fingerprint"], "legacy-fingerprint")
        self.assertIsNone(row["company_id"])


if __name__ == "__main__":
    unittest.main()
