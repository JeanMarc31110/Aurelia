import csv
import json
import tempfile
import unittest
import uuid
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from app import db
from app.connectors.bank_csv import bank_overview, import_bank_csv, propose_matches
from app.db import connect, init_db
from app.services.accounting_exports import accounting_config, create_accounting_export, export_preview, save_accounting_config
from app.services.company import save_active_company
from app.services.payments import refresh_payment_status, validate_payment_match
from app.services.workflow import approve_invoice, reject_invoice


class Phase3WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.db_patch = patch.object(db, "DB_PATH", self.root / "phase3.db")
        self.db_patch.start();init_db()
        save_active_company({"legal_name": "INNOVATECH SOFTWARE E IA SL", "nif": "B22714539"})

    def tearDown(self):
        self.db_patch.stop();self.temporary.cleanup()

    def invoice(self, number, gross=120, direction="purchase", due_date=None, status="REVIEW_REQUIRED",
                account=None, supplier="SECUREHOST SAS"):
        net = round(gross / 1.2, 2);vat = round(gross-net, 2)
        raw = {"document_type":"invoice","invoice_number":number,"supplier":{"name":supplier},
               "customer":{"name":"INNOVATECH SOFTWARE E IA SL"},"direction":direction,
               "issue_date":"2026-08-01","due_date":due_date,"net_amount":net,"vat_amount":vat,
               "gross_amount":gross,"currency":"EUR","lines":[{"description":"Service test",
               "quantity":1,"unit_price_net":net,"line_total_net":net}]}
        con=connect();cursor=con.execute("""INSERT INTO invoices(
          company_id,document_type,fingerprint,source_file,format,direction,invoice_number,supplier_name,
          customer_name,issue_date,due_date,net_amount,vat_amount,gross_amount,currency,status,
          payment_status,proposed_account,account_final,lines_json,raw_json)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (1,"invoice",uuid.uuid4().hex,"phase3.pdf","PDF_TEXT",direction,number,supplier,
           "INNOVATECH SOFTWARE E IA SL","2026-08-01",due_date,net,vat,gross,"EUR",status,
           "UNPAID",account,account,json.dumps(raw["lines"]),json.dumps(raw)))
        invoice_id=cursor.lastrowid;con.commit();con.close();return invoice_id

    def bank_csv(self, name, rows):
        path=self.root/name
        with path.open("w",encoding="utf-8",newline="") as handle:
            writer=csv.DictWriter(handle,fieldnames=["date","libelle","montant","devise","reference"])
            writer.writeheader();writer.writerows(rows)
        return path

    def test_approved_invoice_is_unpaid_and_overdue_is_derived_separately(self):
        invoice_id=self.invoice("DUE-001",due_date="2020-01-01")
        approve_invoice(invoice_id,"tester","606000")
        con=connect();row=con.execute("SELECT status,payment_status,amount_paid,amount_remaining FROM invoices WHERE id=?",(invoice_id,)).fetchone();con.close()
        self.assertEqual(row["status"],"APPROVED");self.assertEqual(row["payment_status"],"UNPAID")
        overdue=refresh_payment_status(invoice_id,today=date(2026,8,27))
        self.assertEqual(overdue["payment_status"],"OVERDUE")
        con=connect();self.assertEqual(con.execute("SELECT status FROM invoices WHERE id=?",(invoice_id,)).fetchone()[0],"APPROVED");con.close()

    def test_csv_deduplication_strong_proposal_unknown_and_no_auto_match(self):
        invoice_id=self.invoice("IBAN-2026-001",due_date="2026-08-28")
        approve_invoice(invoice_id,"tester","606000")
        path=self.bank_csv("bank.csv",[
          {"date":"2026-08-28","libelle":"SECUREHOST SAS","montant":"-120,00","devise":"EUR","reference":"IBAN-2026-001"},
          {"date":"2026-08-28","libelle":"Inconnu","montant":"-999,00","devise":"EUR","reference":"UNKNOWN-001"}])
        first=import_bank_csv(path,"bank.csv","tester");second=import_bank_csv(path,"renamed.csv","tester")
        self.assertEqual(len(first),2);self.assertEqual(len(second),0)
        proposals=propose_matches();strong=next(item for item in proposals if item["invoice_id"]==invoice_id)
        self.assertGreaterEqual(strong["score"],95);self.assertFalse(strong["auto_matched"])
        overview=bank_overview();self.assertTrue(any(item["reference"]=="UNKNOWN-001" for item in overview["unmatched"]))
        con=connect();self.assertEqual(con.execute("SELECT COUNT(*) FROM bank_transactions").fetchone()[0],2);self.assertEqual(con.execute("SELECT COUNT(*) FROM payment_matches").fetchone()[0],0);con.close()

    def test_partial_then_second_payment_becomes_paid(self):
        invoice_id=self.invoice("PARTIAL-240",gross=240,due_date="2026-09-01")
        approve_invoice(invoice_id,"tester","606000")
        first=import_bank_csv(self.bank_csv("p1.csv",[{"date":"2026-08-20","libelle":"SECUREHOST SAS","montant":"-100","devise":"EUR","reference":"PARTIAL-240"}]))[0]
        result=validate_payment_match(first["id"],invoice_id,"tester")
        self.assertEqual(result["payment_status"],"PARTIALLY_PAID");self.assertEqual(result["amount_remaining"],140.0)
        second=import_bank_csv(self.bank_csv("p2.csv",[{"date":"2026-08-21","libelle":"SECUREHOST SAS","montant":"-140","devise":"EUR","reference":"PARTIAL-240"}]))[0]
        result=validate_payment_match(second["id"],invoice_id,"tester")
        self.assertEqual(result["payment_status"],"PAID");self.assertEqual(result["amount_paid"],240.0);self.assertEqual(result["amount_remaining"],0.0)
        con=connect();events=[r[0] for r in con.execute("SELECT event FROM audit_events WHERE invoice_id=?",(invoice_id,))];con.close()
        self.assertGreaterEqual(events.count("payment_match_validated"),2);self.assertIn("payment_status_changed",events)

    def test_purchase_and_sale_forms_propose_positive_allocation(self):
        purchase=self.invoice("SIGN-PURCHASE",gross=120);approve_invoice(purchase,"tester","606000")
        sale=self.invoice("SIGN-SALE",gross=120,direction="sale",supplier="INNOVATECH SOFTWARE E IA SL")
        approve_invoice(sale,"tester","706000")
        purchase_tx=import_bank_csv(self.bank_csv("sign-purchase.csv",[{"date":"2026-08-21","libelle":"SECUREHOST SAS","montant":"-120","devise":"EUR","reference":"SIGN-PURCHASE"}]))[0]
        sale_tx=import_bank_csv(self.bank_csv("sign-sale.csv",[{"date":"2026-08-21","libelle":"CLIENT TEST","montant":"120","devise":"EUR","reference":"SIGN-SALE"}]))[0]
        overview=bank_overview();by_id={row["id"]:row for row in overview["pending"]}
        self.assertEqual(by_id[purchase_tx["id"]]["amount"],-120.0)
        self.assertEqual(by_id[purchase_tx["id"]]["suggested_allocation"],"120.00")
        self.assertEqual(by_id[sale_tx["id"]]["amount"],120.0)
        self.assertEqual(by_id[sale_tx["id"]]["suggested_allocation"],"120.00")

    def test_negative_and_zero_allocations_are_refused_clearly(self):
        invoice_id=self.invoice("INVALID-ALLOC",gross=120);approve_invoice(invoice_id,"tester","606000")
        transaction=import_bank_csv(self.bank_csv("invalid-allocation.csv",[{"date":"2026-08-21","libelle":"SECUREHOST SAS","montant":"-120","devise":"EUR","reference":"INVALID-ALLOC"}]))[0]
        for value in ("-120", "0"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError,"doit être positif"):
                validate_payment_match(transaction["id"],invoice_id,"tester",value)
        con=connect();self.assertEqual(con.execute("SELECT COUNT(*) FROM payment_matches").fetchone()[0],0);con.close()

    def test_securehost_manual_workflow_reaches_paid_without_sign_edit(self):
        invoice_id=self.invoice("IBAN-2026-001",gross=120,due_date="2026-09-10")
        approve_invoice(invoice_id,"tester","626TEST")
        transaction=import_bank_csv(self.bank_csv("securehost-manual.csv",[{"date":"2026-09-10","libelle":"VIREMENT SECUREHOST SAS","montant":"-120","devise":"EUR","reference":"IBAN-2026-001"}]))[0]
        suggested=next(row for row in bank_overview()["pending"] if row["id"]==transaction["id"])["suggested_allocation"]
        result=validate_payment_match(transaction["id"],invoice_id,"tester",suggested)
        self.assertEqual(result["payment_status"],"PAID")
        self.assertEqual(result["amount_paid"],120.0);self.assertEqual(result["amount_remaining"],0.0)

    def test_accounting_configuration_is_scoped_to_active_company(self):
        save_accounting_config({"journal_purchase":"COMPRAS","account_supplier":"400000",
                                "account_vat_deductible":"472000"})
        self.assertEqual(accounting_config()["journal_purchase"],"COMPRAS")
        con=connect();con.execute("UPDATE companies SET active=0")
        con.execute("INSERT INTO companies(legal_name,active) VALUES('AUTRE SOCIETE TEST',1)")
        con.commit();con.close()
        second=accounting_config()
        self.assertEqual(second["company_name"],"AUTRE SOCIETE TEST")
        self.assertEqual(second["journal_purchase"],"")
        self.assertEqual(second["account_supplier"],"")

    def test_overpayment_creates_mismatch_and_sale_accepts_positive_receipt(self):
        purchase=self.invoice("OVER-240",gross=240);approve_invoice(purchase,"tester","606000")
        tx=import_bank_csv(self.bank_csv("over.csv",[{"date":"2026-08-21","libelle":"SECUREHOST SAS","montant":"-250","devise":"EUR","reference":"OVER-240"}]))[0]
        result=validate_payment_match(tx["id"],purchase,"tester")
        self.assertEqual(result["payment_status"],"PAYMENT_MISMATCH");self.assertEqual(result["amount_remaining"],-10.0)
        sale=self.invoice("SALE-120",gross=120,direction="sale",supplier="INNOVATECH SOFTWARE E IA SL")
        approve_invoice(sale,"tester","706000")
        receipt=import_bank_csv(self.bank_csv("sale.csv",[{"date":"2026-08-21","libelle":"CLIENT TEST","montant":"120","devise":"EUR","reference":"SALE-120"}]))[0]
        self.assertEqual(validate_payment_match(receipt["id"],sale,"tester")["payment_status"],"PAID")

    def test_export_blocks_incomplete_statuses_and_records_reexport(self):
        save_accounting_config({"journal_purchase":"ACH","journal_sale":"VEN","account_supplier":"401TEST",
          "account_customer":"411TEST","account_vat_deductible":"4456TEST","account_vat_collected":"4457TEST"})
        valid=self.invoice("EXP-OK",account="606TEST");approve_invoice(valid,"tester","606TEST")
        missing=self.invoice("EXP-NOACCOUNT");approve_invoice(missing,"tester",None)
        review=self.invoice("EXP-REVIEW",account="606TEST")
        rejected=self.invoice("EXP-REJECT",account="606TEST");reject_invoice(rejected,"tester","incorrect_data")
        preview=export_preview({"exported":"not_exported"})
        self.assertEqual([row["id"] for row in preview["eligible"]],[valid])
        self.assertTrue(any(row["id"]==missing and "Compte comptable final à compléter" in row["blocking_reasons"] for row in preview["blocked"]))
        self.assertFalse(any(row["id"] in {review,rejected} for row in preview["eligible"]+preview["blocked"]))
        first=create_accounting_export(self.root/"ebp.csv",{"exported":"not_exported"},"tester")
        self.assertTrue(Path(first["path"]).is_file());self.assertEqual(first["invoice_count"],1)
        with Path(first["path"]).open(encoding="utf-8-sig",newline="") as handle:
            entries=list(csv.DictReader(handle,delimiter=";"))
        debit=round(sum(float(row["Debit"] or 0) for row in entries),2)
        credit=round(sum(float(row["Credit"] or 0) for row in entries),2)
        self.assertEqual(debit,credit);self.assertEqual(debit,120.0)
        with self.assertRaises(ValueError):create_accounting_export(self.root/"ebp.csv",{"exported":"not_exported"},"tester")
        second=create_accounting_export(self.root/"ebp.csv",{"exported":"exported"},"tester")
        self.assertNotEqual(first["batch_id"],second["batch_id"])
        con=connect();self.assertEqual(con.execute("SELECT COUNT(*) FROM accounting_exports").fetchone()[0],2);self.assertEqual(con.execute("SELECT is_reexport FROM accounting_export_items WHERE export_id=?",(second["export_id"],)).fetchone()[0],1);events=con.execute("SELECT COUNT(*) FROM audit_events WHERE invoice_id=? AND event='accounting_exported'",(valid,)).fetchone()[0];con.close();self.assertEqual(events,2)


if __name__ == "__main__":unittest.main()
