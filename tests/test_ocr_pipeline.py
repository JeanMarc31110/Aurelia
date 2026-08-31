import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfWriter

from app import db
from app.db import init_db
from app.parsers.pdf_parser import _parse_text, parse_pdf
from app.services.ocr_normalization import normalize_ocr_text


COMPANY = {
    "id": 1,
    "legal_name": "INNOVATECH SOFTWARE E IA SL",
    "trade_name": None,
    "aliases_json": "[]",
    "nif": "B22714639",
}

RAW_INCONSISTENT = """FACTURE FOURNISSEUR - SCAN FICTIF
FOURNISSEUR: SCANPRINT SAS
SIRET: 58555555555556
�TVA: FRSSTESTSS555
(CLIENT: INNOVATECH SOFTWARE EIA SL
NIF: 822714639
FACTUREN*: SCAN-2026-055
Date: 27 aout 2028
Echeance: 27 septembre 2026
Impression supports marketing 2x60,00 EUR 100,00 EUR
Total HT: 100,00 EUR
�TVA 20%: 20,00 EUR
Total TTC: 120,00 EUR
"""


class OcrNormalizationTests(unittest.TestCase):
    def setUp(self):
        self.database_directory = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(
            db, "DB_PATH", Path(self.database_directory.name) / "ocr-tests.db"
        )
        self.db_patch.start()
        init_db()

    def tearDown(self):
        self.db_patch.stop()
        self.database_directory.cleanup()

    def test_real_scan_variants_are_normalized_without_guessing_year(self):
        normalized = normalize_ocr_text(RAW_INCONSISTENT)
        self.assertIn("FACTURE N° : SCAN-2026-055", normalized["normalized_text"])
        self.assertIn("FOURNISSEUR : SCANPRINT SAS", normalized["normalized_text"])
        self.assertIn("CLIENT : INNOVATECH SOFTWARE EIA SL", normalized["normalized_text"])
        self.assertFalse(normalized["year_corrected"])

        parsed = _parse_text(
            normalized["normalized_text"], company=COMPANY, ocr_mode=True,
            normalization_metadata=normalized,
        )
        self.assertEqual(parsed["invoice_number"], "SCAN-2026-055")
        self.assertEqual(parsed["supplier"]["name"], "SCANPRINT SAS")
        self.assertEqual(parsed["customer"]["name"], "INNOVATECH SOFTWARE EIA SL")
        self.assertEqual(parsed["direction"], "purchase")
        self.assertEqual(parsed["vat_amount"], 20.0)
        self.assertTrue(parsed["amounts_arithmetic_coherent"])
        self.assertIsNone(parsed["issue_date"])
        self.assertEqual(parsed["issue_date_candidate"], "2028-08-27")
        self.assertEqual(parsed["issue_date_confidence"], "low")
        self.assertEqual(parsed["lines"], [])
        self.assertTrue(parsed["needs_manual_validation"])

    def test_reliable_inline_ocr_line_requires_both_arithmetic_checks(self):
        raw = RAW_INCONSISTENT.replace("2028", "2026").replace("2x60,00", "2 × 50,00")
        normalized = normalize_ocr_text(raw)
        parsed = _parse_text(
            normalized["normalized_text"], company=COMPANY, ocr_mode=True,
            normalization_metadata=normalized,
        )
        self.assertEqual(parsed["lines_extraction_method"], "ocr_inline_arithmetic")
        self.assertEqual(parsed["lines_extraction_confidence"], "high")
        self.assertEqual(parsed["lines"][0]["quantity"], 2.0)
        self.assertEqual(parsed["lines"][0]["unit_price_net"], 50.0)
        self.assertFalse(parsed["needs_manual_validation"])

    def test_parse_pdf_retains_raw_and_normalized_ocr_text(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scan.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=595, height=842)
            with path.open("wb") as handle:
                writer.write(handle)
            result = {
                "ok": True, "text": RAW_INCONSISTENT, "confidence": 70.1,
                "languages": ["fra", "eng", "spa"], "tesseract_version": "tesseract 5.4",
                "render_dpi": 300, "preprocessing": ["300dpi_rgb"],
                "candidate_confidences": [], "binarization": "tesseract_internal",
                "deskew": "not_applied_no_reliable_skew_signal",
            }
            with patch("app.parsers.pdf_parser.ocr_pdf", return_value=result):
                parsed = parse_pdf(path)
        self.assertEqual(parsed["raw_ocr_text"], RAW_INCONSISTENT)
        self.assertNotEqual(parsed["normalized_ocr_text"], RAW_INCONSISTENT)
        self.assertEqual(parsed["ocr_languages"], ["fra", "eng", "spa"])
        self.assertEqual(parsed["ocr_render_dpi"], 300)


if __name__ == "__main__":
    unittest.main()
