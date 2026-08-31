import csv
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas

from app import db
from app.connectors.bank_csv import bank_overview, import_bank_csv
from app.db import connect, init_db
from app.parsers.pdf_parser import _parse_text, parse_pdf
from app.parsers.xml_invoice import parse_xml_invoice
from app.services.company import save_active_company
from app.services.email_import import import_eml
from app.services.ocr import ocr_status
from app.services.orchestrator import process_invoice
from app.services.supplier_banks import accept_supplier_bank_account


COMPANY = {"legal_name": "INNOVATECH SOFTWARE E IA SL", "nif": "B22714539", "country": "ES"}


def invoice_text(number="TEST-2026-001", supplier="SYNTHETIC SUPPLIER SAS", siret="11111111111111",
                 net=100, vat=20, gross=120, iban=None):
    bank = f"\nIBAN : {iban}\nBIC : TESTFRPP" if iban else ""
    return f"""FACTURE N° : {number}
Date : 27/08/2026
Échéance : 26/09/2026
FOURNISSEUR
{supplier}
SIRET : {siret}
CLIENT
INNOVATECH SOFTWARE E IA SL
NIF : B22714539
Designation
Qte
Prix unitaire HT
Total HT
Service synthétique
1
{net},00 EUR
{net},00 EUR
Total HT : {net},00 EUR
TVA : {vat},00 EUR
Total TTC : {gross},00 EUR
{bank}
"""


UBL_XML = '''<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
 xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
 xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
 <cbc:ID>UBL-2026-001</cbc:ID><cbc:IssueDate>2026-08-27</cbc:IssueDate><cbc:DueDate>2026-09-26</cbc:DueDate>
 <cbc:DocumentCurrencyCode>EUR</cbc:DocumentCurrencyCode>
 <cac:AccountingSupplierParty><cac:Party><cac:PartyName><cbc:Name>UBL TEST SUPPLIER SAS</cbc:Name></cac:PartyName>
  <cac:PartyTaxScheme><cbc:CompanyID>FR11111111111</cbc:CompanyID></cac:PartyTaxScheme>
  <cac:PostalAddress><cbc:StreetName>1 rue UBL</cbc:StreetName><cbc:CityName>Paris</cbc:CityName></cac:PostalAddress>
 </cac:Party></cac:AccountingSupplierParty>
 <cac:AccountingCustomerParty><cac:Party><cac:PartyName><cbc:Name>INNOVATECH SOFTWARE E IA SL</cbc:Name></cac:PartyName>
  <cac:PartyTaxScheme><cbc:CompanyID>B22714539</cbc:CompanyID></cac:PartyTaxScheme>
 </cac:Party></cac:AccountingCustomerParty>
 <cac:InvoiceLine><cbc:ID>1</cbc:ID><cbc:InvoicedQuantity>1</cbc:InvoicedQuantity><cbc:LineExtensionAmount>150.00</cbc:LineExtensionAmount>
  <cac:Item><cbc:Description>Service UBL synthétique</cbc:Description></cac:Item><cac:Price><cbc:PriceAmount>150.00</cbc:PriceAmount></cac:Price>
 </cac:InvoiceLine>
 <cac:TaxTotal><cbc:TaxAmount>30.00</cbc:TaxAmount></cac:TaxTotal>
 <cac:LegalMonetaryTotal><cbc:TaxExclusiveAmount>150.00</cbc:TaxExclusiveAmount><cbc:PayableAmount>180.00</cbc:PayableAmount></cac:LegalMonetaryTotal>
</Invoice>'''.encode("utf-8")


CII_XML = '''<?xml version="1.0" encoding="UTF-8"?>
<rsm:CrossIndustryInvoice xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
 xmlns:ram="urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100">
 <rsm:ExchangedDocument><ram:ID>CII-2026-001</ram:ID><ram:IssueDateTime><ram:DateTimeString format="102">20260827</ram:DateTimeString></ram:IssueDateTime></rsm:ExchangedDocument>
 <rsm:SupplyChainTradeTransaction>
  <ram:IncludedSupplyChainTradeLineItem><ram:SpecifiedTradeProduct><ram:Name>Service CII synthétique</ram:Name></ram:SpecifiedTradeProduct>
   <ram:SpecifiedLineTradeAgreement><ram:NetPriceProductTradePrice><ram:ChargeAmount>200.00</ram:ChargeAmount></ram:NetPriceProductTradePrice></ram:SpecifiedLineTradeAgreement>
   <ram:SpecifiedLineTradeDelivery><ram:BilledQuantity>1</ram:BilledQuantity></ram:SpecifiedLineTradeDelivery>
   <ram:SpecifiedLineTradeSettlement><ram:SpecifiedTradeSettlementLineMonetarySummation><ram:LineTotalAmount>200.00</ram:LineTotalAmount></ram:SpecifiedTradeSettlementLineMonetarySummation></ram:SpecifiedLineTradeSettlement>
  </ram:IncludedSupplyChainTradeLineItem>
  <ram:ApplicableHeaderTradeAgreement><ram:SellerTradeParty><ram:Name>CII TEST SUPPLIER SAS</ram:Name><ram:SpecifiedTaxRegistration><ram:ID schemeID="VA">FR22222222222</ram:ID></ram:SpecifiedTaxRegistration></ram:SellerTradeParty>
   <ram:BuyerTradeParty><ram:Name>INNOVATECH SOFTWARE E IA SL</ram:Name><ram:SpecifiedTaxRegistration><ram:ID schemeID="VA">B22714539</ram:ID></ram:SpecifiedTaxRegistration></ram:BuyerTradeParty>
  </ram:ApplicableHeaderTradeAgreement>
  <ram:ApplicableHeaderTradeSettlement><ram:InvoiceCurrencyCode>EUR</ram:InvoiceCurrencyCode><ram:SpecifiedTradePaymentTerms><ram:DueDateDateTime><ram:DateTimeString format="102">20260926</ram:DateTimeString></ram:DueDateDateTime></ram:SpecifiedTradePaymentTerms>
   <ram:SpecifiedTradeSettlementHeaderMonetarySummation><ram:TaxBasisTotalAmount>200.00</ram:TaxBasisTotalAmount><ram:TaxTotalAmount>40.00</ram:TaxTotalAmount><ram:GrandTotalAmount>240.00</ram:GrandTotalAmount></ram:SpecifiedTradeSettlementHeaderMonetarySummation>
  </ram:ApplicableHeaderTradeSettlement>
 </rsm:SupplyChainTradeTransaction>
</rsm:CrossIndustryInvoice>'''.encode("utf-8")


class ResistanceCampaignTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.db_patch = patch.object(db, "DB_PATH", self.root / "campaign.db")
        self.db_patch.start()
        init_db()
        self.company = save_active_company(COMPANY)

    def tearDown(self):
        self.db_patch.stop()
        self.temporary_directory.cleanup()

    def _pdf_from_lines(self, name, pages):
        path = self.root / name
        document = canvas.Canvas(str(path))
        document.setFont("Helvetica", 9)
        for page_lines in pages:
            y = 810
            for line in page_lines:
                if isinstance(line, tuple):
                    x, y_value, text = line
                    document.drawString(x, y_value, text)
                else:
                    document.drawString(50, y, line)
                    y -= 18
            document.showPage()
        document.save()
        return path

    def test_01_inconsistent_totals_are_detected(self):
        parsed = _parse_text(invoice_text(gross=130))
        decision = process_invoice(parsed, "test")
        totals = next(item for item in decision["findings"] if item["code"] == "totals")
        self.assertFalse(totals["ok"])
        self.assertEqual(decision["fraud_risk_score"], 0)

    def test_02_quote_is_classified_and_not_inserted_as_invoice(self):
        parsed = _parse_text("DEVIS\nCE DOCUMENT EST UN DEVIS\nIL NE CONSTITUE PAS UNE FACTURE")
        self.assertEqual(parsed["document_type"], "quote")
        decision = process_invoice(parsed, "test")
        self.assertEqual(decision["status"], "NON_ACCOUNTING_DOCUMENT")
        self.assertEqual(decision["findings"][0]["agent"], "DOCUMENT_TYPE")
        con = connect()
        self.assertEqual(con.execute("SELECT COUNT(*) FROM invoices").fetchone()[0], 0)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM documents WHERE document_type='quote'").fetchone()[0], 1)
        con.close()

    def test_03_exact_duplicate_is_document_risk_not_certain_fraud(self):
        parsed = _parse_text(invoice_text())
        first = process_invoice(parsed, "test")
        second = process_invoice(parsed, "test")
        self.assertNotEqual(first["status"], "DUPLICATE")
        self.assertEqual(second["status"], "DUPLICATE")
        duplicate = next(item for item in second["findings"] if item["code"] == "DUPLICATE")
        self.assertEqual(duplicate["agent"], "WORKFLOW")
        self.assertEqual(second["fraud_risk_score"], 0)
        self.assertGreater(second["document_risk_score"], 0)

    def test_04_same_number_different_strong_supplier_is_not_duplicate(self):
        first = process_invoice(_parse_text(invoice_text(number="SAME-001", supplier="SUPPLIER ONE", siret="11111111111111")), "test")
        second = process_invoice(_parse_text(invoice_text(number="SAME-001", supplier="SUPPLIER TWO", siret="22222222222222")), "test")
        self.assertNotEqual(first["fingerprint"], second["fingerprint"])
        self.assertNotEqual(second["status"], "DUPLICATE")

    def test_05_supplier_iban_change_is_pending_and_requires_acceptance(self):
        first = _parse_text(invoice_text(number="RIB-001", supplier="SECUREHOST SAS", siret="44444444444444",
                                         iban="FR7644444444444444444444444"))
        first["source_file"] = "05A_iban_reference.pdf"
        process_invoice(first, "test")
        second = _parse_text(invoice_text(number="RIB-002", supplier="SECUREHOST SAS", siret="44444444444444",
                                          iban="FR7699999999999999999999999"))
        second["source_file"] = "05B_iban_change_suspect.pdf"
        decision = process_invoice(second, "test")
        change = next(item for item in decision["findings"] if item["code"] == "RIB_CHANGED")
        self.assertIn("****", change["message"])
        self.assertTrue(decision["human_review_required"])
        self.assertFalse(decision["payment_authorized"])
        self.assertGreater(decision["fraud_risk_score"], 0)
        pending_id = change["details"]["pending_account_id"]
        accept_supplier_bank_account(pending_id, "admin")
        con = connect()
        active = con.execute("SELECT iban FROM supplier_bank_accounts WHERE active=1").fetchone()[0]
        history_count = con.execute("SELECT COUNT(*) FROM supplier_bank_accounts").fetchone()[0]
        con.close()
        self.assertEqual(active, "FR7699999999999999999999999")
        self.assertEqual(history_count, 2)

    def test_06_ocr_pipeline_and_missing_configuration_message(self):
        image_pdf = self._pdf_from_lines("scan.pdf", [[]])
        with patch("app.services.ocr._candidate_paths", return_value=[]):
            status = ocr_status()
        self.assertFalse(status["available"])
        self.assertIn("Paramètres > OCR", status["instruction"])
        ocr_text = invoice_text(number="SCAN-2026-055", supplier="SCANPRINT SAS", siret="55555555555555")
        with patch("app.parsers.pdf_parser.ocr_pdf", return_value={
            "ok": True, "text": ocr_text, "confidence": 86.0, "error": None
        }):
            parsed = parse_pdf(image_pdf)
        self.assertTrue(parsed["ocr_used"])
        self.assertEqual(parsed["invoice_number"], "SCAN-2026-055")
        self.assertEqual(parsed["supplier"]["name"], "SCANPRINT SAS")
        self.assertEqual((parsed["net_amount"], parsed["vat_amount"], parsed["gross_amount"]), (100.0, 20.0, 120.0))

    def test_07_multipage_positioned_table_is_reconstructed(self):
        columns = {"description": 50, "quantity": 315, "unit": 360, "vat": 465, "total": 520}
        def header(y):
            return [(columns["description"], y, "Designation"), (columns["quantity"], y, "Qte"),
                    (columns["unit"], y, "Prix unitaire HT"), (columns["vat"], y, "TVA"),
                    (columns["total"], y, "Total HT")]
        page1 = [(50,810,"FACTURE N° : MULTI-2026-007"),(50,790,"Date : 27/08/2026"),
                 (50,760,"FOURNISSEUR"),(50,742,"MULTIPAGE SUPPLIER SAS"),(50,724,"SIRET : 77777777777777"),
                 (50,695,"CLIENT"),(50,677,"INNOVATECH SOFTWARE E IA SL"),(50,659,"NIF : B22714539")] + header(610)
        page2 = header(780)
        values = [(580,"Ligne 1",1,100),(550,"Ligne 2",1,150),(520,"Ligne 3",1,200)]
        for y,desc,qty,total in values:
            page1 += [(columns["description"],y,desc),(columns["quantity"],y,str(qty)),(columns["unit"],y,f"{total},00 EUR"),(columns["vat"],y,"20 %"),(columns["total"],y,f"{total},00 EUR")]
        values2 = [(740,"Ligne 4",1,120),(710,"Ligne 5",1,220)]
        for y,desc,qty,total in values2:
            page2 += [(columns["description"],y,desc),(columns["quantity"],y,str(qty)),(columns["unit"],y,f"{total},00 EUR"),(columns["vat"],y,"20 %"),(columns["total"],y,f"{total},00 EUR")]
        page2 += [(50,650,"Total HT : 790,00 EUR"),(50,630,"TVA : 158,00 EUR"),(50,610,"Total TTC : 948,00 EUR")]
        parsed = parse_pdf(self._pdf_from_lines("multipage.pdf", [page1,page2]))
        self.assertEqual(parsed["invoice_number"], "MULTI-2026-007")
        self.assertEqual(len(parsed["lines"]), 5)
        self.assertEqual(parsed["lines_extraction_method"], "positional")
        self.assertAlmostEqual(sum(line["line_total_net"] for line in parsed["lines"]), 790.0)

    def test_08_ubl_parties_direction_and_lines(self):
        path = self.root / "invoice.ubl"
        path.write_bytes(UBL_XML)
        parsed = parse_xml_invoice(path)
        self.assertEqual(parsed["format"], "UBL")
        self.assertEqual(parsed["supplier"]["name"], "UBL TEST SUPPLIER SAS")
        self.assertEqual(parsed["customer"]["name"], COMPANY["legal_name"])
        self.assertEqual(parsed["direction"], "purchase")
        self.assertEqual(parsed["company_id"], self.company["id"])
        self.assertEqual(len(parsed["lines"]), 1)

    def test_09_cii_date_is_normalised_without_regression(self):
        path = self.root / "invoice.cii"
        path.write_bytes(CII_XML)
        parsed = parse_xml_invoice(path)
        self.assertEqual(parsed["issue_date"], "2026-08-27")
        self.assertEqual(parsed["due_date"], "2026-09-26")
        self.assertEqual(parsed["direction"], "purchase")
        self.assertEqual((parsed["net_amount"], parsed["vat_amount"], parsed["gross_amount"]), (200.0, 40.0, 240.0))

    def test_10_embedded_cii_is_prioritised_without_claiming_conformity(self):
        visual = self._pdf_from_lines("visual.pdf", [["FACTURE N° : VISUAL-ONLY"]])
        output = self.root / "facturx_like.pdf"
        writer = PdfWriter()
        writer.append_pages_from_reader(PdfReader(str(visual)))
        writer.add_attachment("factur-x.xml", CII_XML)
        with output.open("wb") as handle:
            writer.write(handle)
        parsed = parse_pdf(output)
        self.assertEqual(parsed["format"], "FACTUR_X")
        self.assertEqual(parsed["structured_source"], "embedded_xml")
        self.assertEqual(parsed["invoice_number"], "CII-2026-001")

    def test_11_bank_csv_has_separate_path_and_unknown_reference_stays_unmatched(self):
        parsed = _parse_text(invoice_text(number="KNOWN-001", gross=120))
        first = process_invoice(parsed, "test")
        con = connect()
        con.execute("UPDATE invoices SET status='VALIDATED' WHERE id=?", (first["invoice_id"],))
        con.commit(); con.close()
        csv_path = self.root / "bank.csv"
        with csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["date","libelle","montant","devise","reference"])
            writer.writeheader()
            writer.writerow({"date":"2026-08-28","libelle":"Virement connu","montant":"-120,00","devise":"EUR","reference":"KNOWN-001"})
            writer.writerow({"date":"2026-08-28","libelle":"Inconnu","montant":"-999,00","devise":"EUR","reference":"UNKNOWN-001"})
        imported = import_bank_csv(csv_path)
        overview = bank_overview()
        self.assertEqual(len(imported), 2)
        self.assertTrue(any(item["invoice_number"] == "KNOWN-001" for item in overview["proposed"]))
        self.assertTrue(any(item["reference"] == "UNKNOWN-001" for item in overview["unmatched"]))
        self.assertTrue(all(not item["auto_matched"] for item in overview["proposed"] + overview["uncertain"]))

    def test_12_eml_metadata_attachment_pipeline_and_attachment_duplicate(self):
        message = EmailMessage()
        message["From"] = "sender@example.test"
        message["To"] = "recipient@example.test"
        message["Subject"] = "Facture synthétique UBL"
        message["Message-ID"] = "<synthetic-12@example.test>"
        message.set_content("Veuillez trouver la facture synthétique en pièce jointe.")
        message.add_attachment(UBL_XML, maintype="application", subtype="xml", filename="invoice.ubl")
        eml = self.root / "message.eml"
        eml.write_bytes(message.as_bytes())
        first = import_eml(eml, "test", self.root / "attachments")
        second = import_eml(eml, "test", self.root / "attachments")
        self.assertEqual(first["message"]["source_message_id"], "<synthetic-12@example.test>")
        self.assertEqual(first["attachments"][0]["invoice"]["source_type"], "email")
        self.assertEqual(first["attachments"][0]["invoice"]["supplier"]["name"], "UBL TEST SUPPLIER SAS")
        self.assertEqual(second["attachments"][0]["status"], "DUPLICATE_ATTACHMENT")


if __name__ == "__main__":
    unittest.main()
