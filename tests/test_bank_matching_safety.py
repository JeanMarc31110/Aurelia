import json
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from app import db
from app.connectors.bank_csv import bank_overview, propose_matches
from app.db import connect, init_db


class ConservativeBankMatchingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(db, "DB_PATH", Path(self.temporary.name) / "bank-safety.db")
        self.db_patch.start()
        init_db()

    def tearDown(self):
        self.db_patch.stop()
        self.temporary.cleanup()

    def invoice(self, number, party, amount=180, status="APPROVED", currency="EUR"):
        raw = {"invoice_number": number, "supplier": {"name": party}, "gross_amount": amount}
        connection = connect()
        cursor = connection.execute(
            """INSERT INTO invoices(
                 document_type,fingerprint,source_file,format,direction,invoice_number,supplier_name,
                 customer_name,issue_date,due_date,net_amount,vat_amount,gross_amount,currency,status,
                 payment_status,amount_paid,amount_remaining,lines_json,raw_json)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("invoice", uuid.uuid4().hex, "bank-test.pdf", "PDF_TEXT", "purchase", number, party,
             "INNOVATECH SOFTWARE E IA SL", "2026-08-27", "2026-09-20", amount / 1.2,
             amount - amount / 1.2, amount, currency, status, "UNPAID", 0, amount, "[]", json.dumps(raw)),
        )
        connection.commit();connection.close()
        return cursor.lastrowid

    def transaction(self, reference, label, amount=-180, currency="EUR"):
        connection = connect()
        cursor = connection.execute(
            """INSERT INTO bank_transactions(
                 booking_date,label,amount,currency,reference,fingerprint,status)
               VALUES('2026-09-20',?,?,?,?,?,'PENDING')""",
            (label, amount, currency, reference, uuid.uuid4().hex),
        )
        connection.commit();connection.close()
        return cursor.lastrowid

    def test_a_reference_amount_and_correct_party_remain_high_confidence(self):
        invoice_id = self.invoice("IBAN-2026-001", "SÉCUREHOST S.A.S.", 120)
        transaction_id = self.transaction("IBAN-2026-001", "VIREMENT SECUREHOST SAS", -120)
        proposal = next(row for row in propose_matches() if row["transaction_id"] == transaction_id)
        self.assertEqual(proposal["invoice_id"], invoice_id)
        self.assertGreaterEqual(proposal["score"], 95)
        self.assertIn("PARTY_MATCH", proposal["positive_signals"])
        self.assertEqual(proposal["contradictions"], [])

    def test_b_party_conflict_caps_reference_and_amount_candidate(self):
        self.invoice("COMMUN-2026-777", "ALPHA DIGITAL SAS", status="REVIEW_REQUIRED")
        beta_id = self.invoice("COMMUN-2026-777", "BETA OFFICE SAS")
        transaction_id = self.transaction("COMMUN-2026-777", "VIREMENT ALPHA DIGITAL SAS")
        beta = next(row for row in propose_matches() if row["invoice_id"] == beta_id)
        self.assertEqual(beta["transaction_id"], transaction_id)
        self.assertLessEqual(beta["score"], 35)
        self.assertIn("PARTY_CONFLICT", beta["contradictions"])
        self.assertNotEqual(beta["classification"], "proposed_high")

    def test_c_correct_party_wins_when_reference_and_amount_are_shared(self):
        alpha_id = self.invoice("COMMUN-2026-777", "ALPHA DIGITAL SAS")
        beta_id = self.invoice("COMMUN-2026-777", "BETA OFFICE SAS")
        transaction_id = self.transaction("COMMUN-2026-777", "VIREMENT ALPHA DIGITAL SAS")
        overview = bank_overview()
        transaction = next(row for row in overview["pending"] if row["id"] == transaction_id)
        self.assertEqual(transaction["proposal"]["invoice_id"], alpha_id)
        self.assertFalse(transaction["ambiguous"])
        beta = next(row for row in transaction["candidates"] if row["invoice_id"] == beta_id)
        self.assertIn("PARTY_CONFLICT", beta["contradictions"])
        self.assertLessEqual(beta["score"], 35)

    def test_d_truly_equal_candidates_require_human_review(self):
        self.invoice("COMMUN-2026-777", "ALPHA DIGITAL SAS")
        self.invoice("COMMUN-2026-777", "BETA OFFICE SAS")
        transaction_id = self.transaction("COMMUN-2026-777", "VIREMENT FACTURE COMMUN-2026-777")
        transaction = next(row for row in bank_overview()["pending"] if row["id"] == transaction_id)
        self.assertTrue(transaction["ambiguous"])
        self.assertIsNone(transaction["proposal"])
        self.assertEqual(len(transaction["ambiguous_candidates"]), 2)

    def test_e_unknown_reference_has_no_match(self):
        self.invoice("KNOWN-001", "SECUREHOST SAS", 120)
        transaction_id = self.transaction("UNKNOWN-001", "PAIEMENT INCONNU", -49.99)
        transaction = next(row for row in bank_overview()["pending"] if row["id"] == transaction_id)
        self.assertIsNone(transaction["proposal"])
        self.assertFalse(transaction["ambiguous"])
        self.assertEqual(transaction["candidates"], [])

    def test_f_non_approved_invoice_is_visible_but_not_eligible(self):
        invoice_id = self.invoice("DUP-2026-010", "NEXCLOUD SAS", 108, "REVIEW_REQUIRED")
        transaction_id = self.transaction("DUP-2026-010", "VIREMENT NEXCLOUD SAS", -108)
        self.assertFalse(any(row["invoice_id"] == invoice_id for row in propose_matches()))
        transaction = next(row for row in bank_overview()["pending"] if row["id"] == transaction_id)
        self.assertIsNone(transaction["proposal"])
        self.assertEqual(transaction["ineligible_matches"][0]["id"], invoice_id)
        self.assertTrue(transaction["ineligible_matches"][0]["party_match"])


if __name__ == "__main__":
    unittest.main()
