import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, PropertyMock, patch

from pypdf import PdfReader, PdfWriter
from pypdf.errors import ParseError, PdfReadError
from reportlab.pdfgen import canvas

from app import db
from app.db import init_db
from app.parsers.pdf_parser import _extract_lines, _parse_amount, _parse_text, parse_pdf
from app.services.company import company_matches_party, save_active_company
from app.services.orchestrator import account_proposal, process_invoice


SYNTHETIC_INVOICE = """FACTURE N° INV-2026-001
Date : 15 février 2026
ÉMETTEUR
INNOVATECH SOFTWARE E IA SL
Calle de la Innovación 12, Madrid
NIF : B22714539
CLIENT
Captain Guillaume
10 rue des Tests, Toulouse
SIRET : 12345678901234
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
Tous les 15 du mois
"""


class PdfParserTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(db, "DB_PATH", Path(self.temporary_directory.name) / "test.db")
        self.db_patch.start()
        init_db()

    def tearDown(self):
        self.db_patch.stop()
        self.temporary_directory.cleanup()

    def _synthetic_pdf(self, directory):
        path = Path(directory) / "synthetic_invoice.pdf"
        document = canvas.Canvas(str(path))
        y = 810
        for line in SYNTHETIC_INVOICE.splitlines():
            document.drawString(50, y, line)
            y -= 18
            if y < 40:
                document.showPage()
                y = 810
        document.save()
        return path

    def _positioned_table_pdf(self, directory):
        path = Path(directory) / "synthetic_positioned_table.pdf"
        document = canvas.Canvas(str(path))
        document.setFont("Helvetica", 9)
        document.drawString(50, 810, "FACTURE FOURNISSEUR - DOCUMENT FICTIF DE TEST")
        document.drawString(50, 785, "FACTURE N° : CDE-2026-084")
        document.drawString(50, 765, "Date : 27/08/2026")
        document.drawString(300, 765, "Échéance : 26/09/2026")
        document.drawString(50, 725, "FOURNISSEUR")
        document.drawString(50, 708, "CLOUDDESK SERVICES SAS")
        document.drawString(50, 691, "SIREN : 987654321")
        document.drawString(50, 655, "CLIENT")
        document.drawString(50, 638, "INNOVATECH SOFTWARE E IA SL")
        document.drawString(50, 621, "NIF : B22714539")

        columns = {"description": 50, "quantity": 315, "unit": 360, "vat": 465, "total": 520}
        headers = (("Designation", "description"), ("Qte", "quantity"),
                   ("Prix unitaire HT", "unit"), ("TVA", "vat"), ("Total HT", "total"))
        for label, column in headers:
            document.drawString(columns[column], 575, label)

        # Les cellules sont volontairement écrites colonne par colonne dans le flux PDF.
        for y, value in ((545, "Abonnement logiciel CloudDesk Pro - aout 2026"),
                         (515, "Support technique premium - aout 2026")):
            document.drawString(columns["description"], y, value)
        for y, value in ((545, "1"), (515, "2")):
            document.drawString(columns["quantity"], y, value)
        for y, value in ((545, "120,00 EUR"), (515, "40,00 EUR")):
            document.drawString(columns["unit"], y, value)
        for y in (545, 515):
            document.drawString(columns["vat"], y, "20 %")
        for y, value in ((545, "120,00 EUR"), (515, "80,00 EUR")):
            document.drawString(columns["total"], y, value)

        document.drawString(50, 465, "Total HT : 200,00 EUR")
        document.drawString(50, 445, "TVA : 40,00 EUR")
        document.drawString(50, 425, "Total TTC : 240,00 EUR")
        document.drawString(50, 380, "Banque : Banque fictive")
        document.drawString(50, 362, "IBAN : FR00 0000 0000 0000")
        document.drawString(50, 344, "BIC : TESTFRPP")
        document.save()
        return path

    def test_pdf_text_extracts_parties_amounts_lines_and_reverse_charge(self):
        save_active_company({"legal_name": "Captain Guillaume", "siret": "12345678901234"})
        with tempfile.TemporaryDirectory() as directory:
            result = parse_pdf(self._synthetic_pdf(directory))

        self.assertEqual(result["format"], "PDF_TEXT")
        self.assertEqual(result["invoice_number"], "INV-2026-001")
        self.assertEqual(result["issuer"]["name"], "INNOVATECH SOFTWARE E IA SL")
        self.assertEqual(result["issuer"]["nif"], "B22714539")
        self.assertEqual(result["customer"]["name"], "Captain Guillaume")
        self.assertEqual(result["issue_date"], "2026-02-15")
        self.assertIsNone(result["due_date"])
        self.assertEqual(result["due_terms"], "Tous les 15 du mois")
        self.assertEqual(result["net_amount"], 150.0)
        self.assertEqual(result["vat_amount"], 0.0)
        self.assertEqual(result["gross_amount"], 150.0)
        self.assertEqual(result["vat_mechanism"], "reverse_charge")
        self.assertEqual(result["direction"], "purchase")
        self.assertEqual(result["lines_extraction_confidence"], "high")
        self.assertEqual(len(result["lines"]), 2)

    def test_password_required_pdf_is_rejected_before_ocr(self):
        path = Path(self.temporary_directory.name) / "encrypted.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=595, height=842)
        writer.encrypt("secret")
        with path.open("wb") as handle:
            writer.write(handle)
        with patch("app.parsers.pdf_parser.ocr_pdf") as ocr:
            with self.assertRaisesRegex(ValueError, "mot de passe"):
                parse_pdf(path)
        ocr.assert_not_called()

    def test_empty_password_pdf_preserves_text_and_path_types(self):
        original = self._synthetic_pdf(self.temporary_directory.name)
        expected = parse_pdf(original)
        path = original.with_name("encrypted.pdf")
        writer = PdfWriter()
        writer.append_pages_from_reader(PdfReader(str(original)))
        writer.encrypt("", owner_password="owner-secret")
        with path.open("wb") as handle:
            writer.write(handle)
        for argument in (path, str(path)):
            with self.subTest(argument=argument):
                self.assertEqual(parse_pdf(argument), expected)

    def test_empty_password_pdf_preserves_ocr_fallback(self):
        path = Path(self.temporary_directory.name) / "encrypted_scan.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=595, height=842)
        writer.encrypt("", owner_password="owner-secret")
        with path.open("wb") as handle:
            writer.write(handle)
        for argument in (path, str(path)):
            for ocr_result in (
                {"ok": True, "text": SYNTHETIC_INVOICE, "confidence": 85},
                {"ok": False, "error": "OCR unavailable"},
            ):
                with self.subTest(argument=argument, ok=ocr_result["ok"]):
                    with patch("app.parsers.pdf_parser.ocr_pdf", return_value=ocr_result) as ocr:
                        result = parse_pdf(argument)
                    ocr.assert_called_once_with(argument)
                    self.assertEqual(result["ocr_used"], ocr_result["ok"])
                    self.assertEqual(result["needs_manual_extraction"], not ocr_result["ok"])
                    self.assertEqual(result["format"], "PDF_OCR" if ocr_result["ok"] else "PDF_IMAGE")
                    self.assertEqual(result["ocr_error"], ocr_result.get("error"))
                    if ocr_result["ok"]:
                        self.assertEqual(result["invoice_number"], "INV-2026-001")

    def test_malformed_pdf_reports_read_error_without_ocr(self):
        path = Path(self.temporary_directory.name) / "broken.pdf"
        for content in (b"", b"not a PDF", b"%PDF-1.7\n1 0 obj\n<<"):
            with self.subTest(content=content):
                path.write_bytes(content)
                with patch("app.parsers.pdf_parser.ocr_pdf") as ocr:
                    with self.assertRaisesRegex(ValueError, "PDF invalide ou endommagé") as error:
                        parse_pdf(path)
                self.assertIsInstance(error.exception.__cause__, PdfReadError)
                ocr.assert_not_called()

    def test_pdf_read_failures_are_targeted_and_chained(self):
        for stage in ("reader", "decrypt", "attachments", "attachment_items", "pages", "text"):
            for exception_type in (PdfReadError, ParseError, RuntimeError, TypeError, OSError):
                with self.subTest(stage=stage, exception_type=exception_type):
                    failure = exception_type("failure")
                    reader = Mock(is_encrypted=False, attachments={}, pages=[Mock()])
                    if stage == "decrypt":
                        reader.is_encrypted = True
                        reader.decrypt.side_effect = failure
                    elif stage in {"attachments", "pages"}:
                        setattr(type(reader), stage, PropertyMock(side_effect=failure))
                    elif stage == "attachment_items":
                        reader.attachments = Mock()
                        reader.attachments.items.side_effect = failure
                    elif stage == "text":
                        reader.pages[0].extract_text.side_effect = failure
                    with patch("app.parsers.pdf_parser.PdfReader", return_value=reader) as constructor, \
                            patch("app.parsers.pdf_parser.ocr_pdf") as ocr:
                        if stage == "reader":
                            constructor.side_effect = failure
                        expected_type = ValueError if isinstance(failure, (PdfReadError, ParseError)) else exception_type
                        with self.assertRaises(expected_type) as error:
                            parse_pdf("invoice.pdf")
                    if expected_type is ValueError:
                        self.assertIs(error.exception.__cause__, failure)
                    else:
                        self.assertIs(error.exception, failure)
                    ocr.assert_not_called()

    def test_encrypted_embedded_xml_priority_and_cleanup(self):
        path = Path(self.temporary_directory.name) / "embedded.pdf"
        for name in ("factur-x.xml", "zugferd-invoice.xml", "xrechnung.xml"):
            with self.subTest(name=name):
                writer = PdfWriter()
                writer.add_blank_page(width=595, height=842)
                writer.add_attachment("other.xml", b"other")
                writer.add_attachment(name, b"invoice")
                writer.encrypt("", owner_password="owner-secret")
                with path.open("wb") as handle:
                    writer.write(handle)
                temporary_paths = []

                def parse_xml(temporary_path):
                    temporary_paths.append(Path(temporary_path))
                    self.assertEqual(Path(temporary_path).read_bytes(), b"invoice")
                    return {"format": "CII", "invoice_number": "XML-001"}

                with patch("app.parsers.pdf_parser.parse_xml_invoice", side_effect=parse_xml), \
                        patch("app.parsers.pdf_parser.ocr_pdf") as ocr:
                    result = parse_pdf(path)
                self.assertEqual(result["format"], "FACTUR_X")
                self.assertEqual(result["embedded_xml_name"], name)
                self.assertEqual(result["invoice_number"], "XML-001")
                self.assertEqual(len(temporary_paths), 1)
                self.assertFalse(temporary_paths[0].exists())
                ocr.assert_not_called()

    def test_xml_failure_fallback_and_cleanup_are_preserved(self):
        path = self._synthetic_pdf(self.temporary_directory.name)
        for failure in (ValueError("invalid XML"), OSError("unreadable XML"), RuntimeError("bug")):
            with self.subTest(failure=failure):
                reader = PdfReader(str(path))
                temporary_paths = []

                def parse_xml(temporary_path):
                    temporary_paths.append(Path(temporary_path))
                    raise failure

                with patch("app.parsers.pdf_parser.PdfReader", return_value=reader), \
                        patch.object(PdfReader, "attachments", new_callable=PropertyMock,
                                     return_value={"factur-x.xml": [b"invalid"]}), \
                        patch("app.parsers.pdf_parser.parse_xml_invoice", side_effect=parse_xml):
                    if isinstance(failure, RuntimeError):
                        with self.assertRaises(RuntimeError) as error:
                            parse_pdf(path)
                        self.assertIs(error.exception, failure)
                    else:
                        self.assertEqual(parse_pdf(path)["format"], "PDF_TEXT")
                self.assertEqual(len(temporary_paths), 1)
                self.assertFalse(temporary_paths[0].exists())

    def test_vat_legal_reference_is_never_an_amount(self):
        result = _parse_text(SYNTHETIC_INVOICE, company={})
        self.assertEqual(result["net_amount"], 150.0)
        self.assertEqual(result["vat_amount"], 0.0)
        self.assertEqual(result["gross_amount"], 150.0)
        self.assertEqual(result["net_amount"] + result["vat_amount"], result["gross_amount"])
        self.assertEqual(result["vat_mechanism"], "reverse_charge")

    def test_invoice_number_prioritises_explicit_labels(self):
        text = "FACTURE FOURNISSEUR - DOCUMENT FICTIF DE TEST\nFACTURE N° : CDE-2026-084"
        self.assertEqual(_parse_text(text, company={})["invoice_number"], "CDE-2026-084")
        self.assertIsNone(_parse_text("FACTURE FOURNISSEUR", company={})["invoice_number"])
        variants = {
            "Invoice No. INV-12345": "INV-12345",
            "Invoice # US/2026_42": "US/2026_42",
            "N° FACTURE : FR-9001": "FR-9001",
            "Número de factura: ES*2026*77": "ES*2026*77",
        }
        for text, expected in variants.items():
            with self.subTest(text=text):
                self.assertEqual(_parse_text(text, company={})["invoice_number"], expected)

    @patch("app.services.orchestrator.suggest", return_value=None)
    def test_positioned_table_reconstruction_and_bank_footer_exclusion(self, _suggest):
        company = save_active_company({"legal_name": "INNOVATECH SOFTWARE E IA SL", "nif": "B22714539"})
        with tempfile.TemporaryDirectory() as directory:
            result = parse_pdf(self._positioned_table_pdf(directory))

        self.assertEqual(result["invoice_number"], "CDE-2026-084")
        self.assertEqual(result["issuer"]["name"], "CLOUDDESK SERVICES SAS")
        self.assertEqual(result["customer"]["name"], "INNOVATECH SOFTWARE E IA SL")
        self.assertEqual(result["direction"], "purchase")
        self.assertEqual(result["company_id"], company["id"])
        self.assertEqual(result["issue_date"], "2026-08-27")
        self.assertEqual(result["due_date"], "2026-09-26")
        self.assertEqual((result["net_amount"], result["vat_amount"], result["gross_amount"]), (200.0, 40.0, 240.0))
        self.assertEqual(result["lines_extraction_method"], "positional")
        self.assertEqual(result["lines_extraction_confidence"], "high")
        self.assertEqual(result["lines"], [
            {"description": "Abonnement logiciel CloudDesk Pro - aout 2026", "quantity": 1.0,
             "unit_price_net": 120.0, "line_total_net": 120.0},
            {"description": "Support technique premium - aout 2026", "quantity": 2.0,
             "unit_price_net": 40.0, "line_total_net": 80.0},
        ])
        proposal = account_proposal(result)
        self.assertIsNone(proposal["account"])
        self.assertNotEqual(proposal["account"], "627")

    def test_direction_uses_sqlite_company_and_strong_identifiers(self):
        result = _parse_text(SYNTHETIC_INVOICE, company={})
        self.assertEqual(result["direction"], "unknown")
        self.assertTrue(result["needs_manual_validation"])

        company = save_active_company({"legal_name": "INNOVATECH SOFTWARE E IA SL", "nif": "B22714539"})
        result = _parse_text(SYNTHETIC_INVOICE)
        self.assertEqual(result["direction"], "sale")
        self.assertEqual(result["company_id"], company["id"])

        conflicting_party = {"name": company["legal_name"], "nif": "DIFFERENT123"}
        self.assertFalse(company_matches_party(company, conflicting_party))

    def test_european_amount_formats_and_month_languages(self):
        expected = {
            "150 €": 150.0, "150,00 €": 150.0, "150.00 EUR": 150.0,
            "1 250,50 €": 1250.5, "1.250,50 €": 1250.5,
        }
        for text, amount in expected.items():
            with self.subTest(text=text):
                self.assertEqual(_parse_amount(text), amount)
        self.assertEqual(_parse_text("Date: 3 marzo 2026", company={})["issue_date"], "2026-03-03")
        self.assertEqual(_parse_text("Invoice date: 4 July 2026", company={})["issue_date"], "2026-07-04")

    def test_line_discount_does_not_require_quantity_times_unit_price(self):
        lines, confidence = _extract_lines(
            ["Designation", "Qte", "Prix unitaire HT", "Total HT", "Service remisé",
             "2", "50,00 EUR", "80,00 EUR", "Total HT : 80,00 EUR"],
            net_amount=80.0,
        )
        self.assertEqual(confidence, "high")
        self.assertEqual(lines[0]["quantity"], 2.0)
        self.assertEqual(lines[0]["unit_price_net"], 50.0)
        self.assertEqual(lines[0]["line_total_net"], 80.0)

    @patch("app.services.orchestrator.suggest", return_value=None)
    def test_account_confidence_is_not_artificially_high(self, _suggest):
        parsed = _parse_text(SYNTHETIC_INVOICE, company={})
        proposal = account_proposal(parsed)
        self.assertNotEqual(proposal["account"], "627")
        self.assertLess(proposal["confidence"], 0.85)

        no_lines = dict(parsed, lines=[], lines_extraction_confidence="none", raw_text="Paiement par banque")
        proposal = account_proposal(no_lines)
        self.assertIsNone(proposal["account"])
        self.assertEqual(proposal["source"], "insufficient_line_evidence")
        self.assertLessEqual(proposal["confidence"], 0.15)
        decision = process_invoice(no_lines, "test")
        self.assertTrue(decision["human_review_required"])
        self.assertIsNone(decision["accounting_proposal"]["account"])


if __name__ == "__main__":
    unittest.main()
