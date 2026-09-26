import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import requests
from reportlab.pdfgen import canvas

from app import db
from app.auth import create_admin,hash_password
from app.db import connect, init_db
from app.services.company import save_active_company


ROOT = Path(__file__).resolve().parents[1]


def pdf_bytes(text_lines):
    stream = io.BytesIO()
    document = canvas.Canvas(stream)
    y = 800
    for line in text_lines:
        document.drawString(50, y, line)
        y -= 20
    document.save()
    return stream.getvalue()


class Phase2InterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.database_path = Path(cls.temporary.name) / "phase2.db"
        cls.db_patch = patch.object(db, "DB_PATH", cls.database_path)
        cls.db_patch.start()
        init_db()
        create_admin("admin-test", "Admin-Test-Only!")
        save_active_company({"legal_name": "INNOVATECH SOFTWARE E IA SL", "nif": "B22714539"})
        con = connect()
        con.execute(
            "INSERT INTO users(username,password_hash,role) VALUES(?,?,?)",
            ("validateur", hash_password("Validation-Test-Only!"), "validateur"),
        )
        con.commit();con.close()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            cls.port = sock.getsockname()[1]
        environment = os.environ.copy()
        environment["AURELIA_DB_PATH"] = str(cls.database_path)
        environment["AURELIA_UPLOADS_PATH"] = str(Path(cls.temporary.name) / "uploads")
        environment["AURELIA_DATA_DIR"] = str(Path(cls.temporary.name) / "data")
        environment["AURELIA_DOCUMENTS_DIR"] = str(Path(cls.temporary.name) / "documents")
        environment["AURELIA_WATCHER_ENABLED"] = "0"
        environment["AURELIA_BACKUPS_ENABLED"] = "0"
        environment["PYTHONUNBUFFERED"] = "1"
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        cls.server = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
             "--port", str(cls.port), "--log-level", "warning"],
            cwd=ROOT, env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        cls.base_url = f"http://127.0.0.1:{cls.port}"
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if cls.server.poll() is not None:
                output = cls.server.stdout.read() if cls.server.stdout else ""
                raise RuntimeError(f"Aurelia server exited during startup:\n{output}")
            try:
                if requests.get(f"{cls.base_url}/login", timeout=.5).status_code == 200:
                    break
            except requests.RequestException:
                pass
            time.sleep(.1)
        else:
            raise RuntimeError("Aurelia server did not start")
        cls.session = requests.Session()
        login = cls.session.post(
            f"{cls.base_url}/login", data={"username": "admin-test", "password": "Admin-Test-Only!"},
            allow_redirects=False, timeout=5,
        )
        if login.status_code != 303:
            raise RuntimeError("Test admin login failed")

    @classmethod
    def tearDownClass(cls):
        cls.session.close()
        if cls.server.poll() is None:
            cls.server.terminate()
            try:
                cls.server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                cls.server.kill();cls.server.wait(timeout=5)
        if cls.server.stdout:
            cls.server.stdout.close()
        cls.db_patch.stop()
        cls.temporary.cleanup()

    def seed_invoice(self, status="REVIEW_REQUIRED", number=None):
        number = number or f"P2-{uuid.uuid4().hex[:8]}"
        raw = {
            "document_type": "invoice", "format": "PDF_TEXT", "invoice_number": number,
            "supplier": {"name": "FOURNISSEUR SYNTHETIQUE SAS", "siret": "11111111111111"},
            "customer": {"name": "INNOVATECH SOFTWARE E IA SL", "nif": "B22714539"},
            "direction": "purchase", "issue_date": "2026-08-27", "due_date": "2026-09-27",
            "net_amount": 100.0, "vat_amount": 20.0, "gross_amount": 120.0, "currency": "EUR",
            "lines": [{"description": "Service synthétique", "quantity": 1.0,
                       "unit_price_net": 100.0, "line_total_net": 100.0}],
            "structured_extraction_confidence": 92.0,
        }
        con = connect()
        cursor = con.execute(
            """INSERT INTO invoices(company_id,document_type,fingerprint,source_file,format,direction,
               invoice_number,supplier_name,customer_name,issue_date,due_date,net_amount,vat_amount,
               gross_amount,currency,status,risk_score,document_risk_score,fraud_risk_score,lines_json,raw_json)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (1, "invoice", uuid.uuid4().hex, "phase2-test.pdf", "PDF_TEXT", "purchase", number,
             "FOURNISSEUR SYNTHETIQUE SAS", "INNOVATECH SOFTWARE E IA SL", "2026-08-27",
             "2026-09-27", 100.0, 20.0, 120.0, "EUR", status, 0, 0, 0,
             json.dumps(raw["lines"]), json.dumps(raw)),
        )
        invoice_id = cursor.lastrowid
        for finding in (
            {"agent": "FOURNISSEURS", "code": "supplier", "severity": "error", "ok": True,
             "message": "Fournisseur identifié"},
            {"agent": "TVA", "code": "totals", "severity": "error", "ok": True,
             "message": "HT + TVA = TTC"},
        ):
            con.execute(
                "INSERT INTO audit_events(invoice_id,username,agent,event,details) VALUES(?,?,?,?,?)",
                (invoice_id, "system", finding["agent"], finding["code"], json.dumps(finding)),
            )
        con.commit();con.close()
        return invoice_id

    def test_pdf_import_redirects_to_readable_invoice_detail(self):
        number = f"WEB-{uuid.uuid4().hex[:7]}"
        body = pdf_bytes([
            f"FACTURE N° : {number}", "Date : 27/08/2026", "FOURNISSEUR",
            "WEB TEST SUPPLIER SAS", "SIRET : 22222222222222", "CLIENT",
            "INNOVATECH SOFTWARE E IA SL", "NIF : B22714539", "Designation", "Qte",
            "Prix unitaire HT", "Total HT", "Service web", "1", "100,00 EUR", "100,00 EUR",
            "Total HT : 100,00 EUR", "TVA : 20,00 EUR", "Total TTC : 120,00 EUR",
        ])
        response = self.session.post(
            f"{self.base_url}/invoices/import", files={"file": (f"{number}.pdf", body, "application/pdf")},
            allow_redirects=False, timeout=10,
        )
        self.assertEqual(response.status_code, 303)
        self.assertRegex(response.headers["location"], r"^/invoices/\d+$")
        page = self.session.get(self.base_url + response.headers["location"], timeout=5)
        self.assertEqual(page.status_code, 200)
        self.assertIn(number.upper(), page.text)
        self.assertIn("Alertes et contrôles", page.text)
        self.assertNotIn("raw_json", page.text)

    def test_work_queue_uses_real_invoice_state_and_links_to_review(self):
        invoice_id = self.seed_invoice(status="REVIEW_REQUIRED", number="WORK-QUEUE-001")
        page = self.session.get(f"{self.base_url}/work", timeout=5)
        self.assertEqual(page.status_code, 200)
        self.assertIn("Tout ce qui nécessite votre attention", page.text)
        self.assertIn("WORK-QUEUE-001", page.text)
        self.assertIn(f'href="/invoices/{invoice_id}"', page.text)
        self.assertIn("À vérifier", page.text)

        detail = self.session.get(f"{self.base_url}/invoices/{invoice_id}", timeout=5)
        self.assertEqual(detail.status_code, 200)
        self.assertIn('class="invoice-decision-bar"', detail.text)
        self.assertLess(detail.text.index("Valider"), detail.text.index("Informations principales"))

    def test_validation_correction_rejection_and_history(self):
        approved_id = self.seed_invoice()
        approved = self.session.post(
            f"{self.base_url}/invoices/{approved_id}/approve",
            data={"comment": "Contrôle humain synthétique", "account": "606000"},
            allow_redirects=False, timeout=5,
        )
        self.assertEqual(approved.status_code, 303)
        con = connect();row = con.execute("SELECT status,approved_by FROM invoices WHERE id=?", (approved_id,)).fetchone();con.close()
        self.assertEqual(tuple(row), ("APPROVED", "admin-test"))

        corrected_id = self.seed_invoice()
        corrected = self.session.post(
            f"{self.base_url}/invoices/{corrected_id}/correct",
            data={"supplier_name": "FOURNISSEUR CORRIGE SAS", "customer_name": "INNOVATECH SOFTWARE E IA SL",
                  "invoice_number": "CORR-2026-001", "issue_date": "2026-08-27", "due_date": "2026-09-27",
                  "net_amount": "100.00", "vat_amount": "20.00", "gross_amount": "120.00",
                  "direction": "purchase", "lines_text": "Service corrigé | 1 | 100 | 100",
                  "justification": "Correction vérifiée sur le document synthétique"},
            allow_redirects=False, timeout=5,
        )
        self.assertEqual(corrected.status_code, 303)
        page = self.session.get(f"{self.base_url}/invoices/{corrected_id}", timeout=5)
        self.assertIn("FOURNISSEUR CORRIGE SAS", page.text)
        self.assertIn("Extraction originale conservée", page.text)
        self.assertIn("Correction vérifiée", page.text)
        con = connect();raw = json.loads(con.execute("SELECT raw_json FROM invoices WHERE id=?", (corrected_id,)).fetchone()[0]);con.close()
        self.assertEqual(raw["supplier"]["name"], "FOURNISSEUR SYNTHETIQUE SAS")

        rejected_id = self.seed_invoice()
        rejected = self.session.post(
            f"{self.base_url}/invoices/{rejected_id}/reject",
            data={"reason": "incorrect_data", "comment": "Test synthétique"},
            allow_redirects=False, timeout=5,
        )
        self.assertEqual(rejected.status_code, 303)
        rejected_page = self.session.get(f"{self.base_url}/invoices/{rejected_id}", timeout=5)
        self.assertIn("rejected", rejected_page.text.lower())
        self.assertIn("Données incorrectes", rejected_page.text)

    def test_quote_redirects_to_non_accounting_document_page(self):
        marker = uuid.uuid4().hex[:7]
        body = pdf_bytes(["DEVIS", f"DEVIS N° : Q-{marker}", "FOURNISSEUR", "TEST DEVIS SAS",
                          "CLIENT", "INNOVATECH SOFTWARE E IA SL", "Total : 120,00 EUR"])
        response = self.session.post(
            f"{self.base_url}/invoices/import", files={"file": (f"quote-{marker}.pdf", body, "application/pdf")},
            allow_redirects=False, timeout=10,
        )
        self.assertEqual(response.status_code, 303)
        self.assertRegex(response.headers["location"], r"^/documents/\d+$")
        page = self.session.get(self.base_url + response.headers["location"], timeout=5)
        self.assertEqual(page.status_code, 200)
        self.assertIn("n’est pas une facture", page.text)
        self.assertNotIn("Valider la facture", page.text)

    def test_rib_changed_card_and_admin_action(self):
        invoice_id = self.seed_invoice()
        con = connect()
        con.execute("""INSERT INTO supplier_bank_accounts(
          supplier_identity_type,supplier_identity_value,supplier_name,iban,status,active)
          VALUES('siret','11111111111111','FOURNISSEUR SYNTHETIQUE SAS','FR7611111111111111111111111','KNOWN',1)""")
        cursor = con.execute("""INSERT INTO supplier_bank_accounts(
          supplier_identity_type,supplier_identity_value,supplier_name,iban,status,active)
          VALUES('siret','11111111111111','FOURNISSEUR SYNTHETIQUE SAS','FR7699999999999999999999999','PENDING',0)""")
        pending_id = cursor.lastrowid
        finding = {"agent": "TRESORERIE", "code": "RIB_CHANGED", "severity": "critical", "ok": False,
                   "message": "Changement de RIB détecté", "details": {"pending_account_id": pending_id,
                   "supplier": "FOURNISSEUR SYNTHETIQUE SAS", "old_iban_masked": "FR76*************1111",
                   "new_iban_masked": "FR76*************9999"}}
        con.execute(
            "INSERT INTO audit_events(invoice_id,username,agent,event,details) VALUES(?,?,?,?,?)",
            (invoice_id, "system", "TRESORERIE", "RIB_CHANGED", json.dumps(finding)),
        )
        con.commit();con.close()
        page = self.session.get(f"{self.base_url}/invoices/{invoice_id}", timeout=5)
        self.assertIn("CHANGEMENT DE RIB DÉTECTÉ", page.text)
        self.assertNotIn("FR7699999999999999999999999", page.text)
        reviewer = requests.Session()
        reviewer.post(
            f"{self.base_url}/login",
            data={"username": "validateur", "password": "Validation-Test-Only!"}, timeout=5,
        )
        forbidden = reviewer.post(
            f"{self.base_url}/invoices/{invoice_id}/rib/{pending_id}/accept", timeout=5,
        )
        reviewer.close()
        self.assertEqual(forbidden.status_code, 403)
        accepted = self.session.post(
            f"{self.base_url}/invoices/{invoice_id}/rib/{pending_id}/accept",
            allow_redirects=False, timeout=5,
        )
        self.assertEqual(accepted.status_code, 303)
        con = connect();status = con.execute("SELECT status FROM supplier_bank_accounts WHERE id=?", (pending_id,)).fetchone()[0];con.close()
        self.assertEqual(status, "KNOWN")

    def test_unauthenticated_user_is_refused(self):
        invoice_id = self.seed_invoice()
        anonymous = requests.Session()
        page = anonymous.get(f"{self.base_url}/invoices/{invoice_id}", timeout=5)
        action = anonymous.post(
            f"{self.base_url}/invoices/{invoice_id}/approve", data={"comment": "", "account": ""}, timeout=5,
        )
        anonymous.close()
        self.assertEqual(page.status_code, 401)
        self.assertEqual(action.status_code, 401)


if __name__ == "__main__":
    unittest.main()
