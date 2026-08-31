import re
import unicodedata


DATE_LABEL = re.compile(
    r"^\s*(?:date(?:\s+de\s+facture)?|invoice\s+date|fecha(?:\s+de\s+factura)?|"
    r"(?:date\s+d['’]?échéance|échéance|due\s+date|fecha\s+de\s+vencimiento))\b",
    re.I,
)


def normalize_ocr_text(raw_text):
    """Apply conservative OCR-only repairs while retaining the untouched source text."""
    text = unicodedata.normalize("NFKC", raw_text or "").replace("\r\n", "\n")
    transformations = []
    date_corrections = []

    def replace(pattern, replacement, name, flags=re.I | re.M):
        nonlocal text
        updated, count = re.subn(pattern, replacement, text, flags=flags)
        if count:
            transformations.append({"name": name, "count": count})
            text = updated

    # Remove scan artefacts only when they prefix a known label at the start of a line.
    replace(r"^[\s(\[{]*[�|¦]+\s*(?=(?:TVA|VAT|IVA|CLIENT|FOURNISSEUR)\b)", "", "label_prefix_noise")
    replace(r"^[\s(\[{]+(?=(?:CLIENT|FOURNISSEUR|VENDEUR|ACHETEUR|SELLER|BUYER)\b)", "", "label_opening_noise")

    # Normalise explicit invoice-number markers; a marker remains mandatory.
    replace(
        r"\bFACTURE\s*N\s*(?:[°º0O*]|[Nn][Oo]\.?)?\s*[:#-]*\s*",
        "FACTURE N° : ",
        "invoice_number_label",
    )
    replace(r"\bN\s*[0O°º*]\s*FACTURE\s*[:#-]*\s*", "N° FACTURE : ", "invoice_number_prefix")

    # Stabilise spacing and punctuation around labels without altering their values.
    replace(r"\b(FOURNISSEUR|CLIENT|VENDEUR|ACHETEUR|SELLER|BUYER)\s*[:;=\-]\s*", r"\1 : ", "party_label")
    replace(r"\b(TVA|VAT|IVA)\s*(\d{1,2}(?:[.,]\d+)?)\s*%\s*[:;=\-]?\s*", r"\1 \2 % : ", "vat_rate_label")
    replace(r"\b(TOTAL)\s*(HT|TTC)\s*[:;=\-]?\s*", r"\1 \2 : ", "total_label")

    repaired_lines = []
    for line in text.splitlines():
        repaired = line
        if DATE_LABEL.search(line):
            for pattern, replacement, label in (
                (r"\bao0t\b", "aout", "ao0t_to_aout"),
                (r"\ba0ut\b", "aout", "a0ut_to_aout"),
                (r"\bf[ée]vrler\b", "fevrier", "fevrler_to_fevrier"),
            ):
                updated, count = re.subn(pattern, replacement, repaired, flags=re.I)
                if count:
                    date_corrections.append(label)
                    repaired = updated
        repaired_lines.append(re.sub(r"[ \t]+", " ", repaired).strip())

    normalized = "\n".join(repaired_lines).strip()
    if date_corrections:
        transformations.append({"name": "date_month_correction", "count": len(date_corrections)})
    return {
        "raw_text": raw_text or "",
        "normalized_text": normalized,
        "transformations": transformations,
        "date_corrections": date_corrections,
        "year_corrected": False,
    }
