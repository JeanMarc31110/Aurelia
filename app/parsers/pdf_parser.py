import json
import re
import tempfile
import unicodedata
from datetime import date
from pathlib import Path

from pypdf import PdfReader

from .xml_invoice import parse_xml_invoice
from app.services.ocr import ocr_pdf
from app.services.ocr_normalization import normalize_ocr_text
from app.services.company import company_matches_party, determine_invoice_direction, get_active_company


ISSUER_HEADINGS = {"EMETTEUR", "FOURNISSEUR", "VENDEUR", "SELLER", "ISSUER", "SUPPLIER"}
CUSTOMER_HEADINGS = {"CLIENT", "ACHETEUR", "CUSTOMER", "BUYER"}
TABLE_HEADINGS = {
    "DESIGNATION", "DESCRIPTION", "PRODUIT", "SERVICE", "ITEM", "ITEMS", "DETAIL", "DETAILS", "LIGNES"
}
INVALID_INVOICE_NUMBERS = {"FOURNISSEUR", "CLIENT", "FACTURE", "INVOICE", "DOCUMENT", "TEST"}

MONTHS = {
    "janvier": 1, "janv": 1, "january": 1, "enero": 1,
    "fevrier": 2, "fevr": 2, "february": 2, "febrero": 2,
    "mars": 3, "march": 3, "marzo": 3,
    "avril": 4, "april": 4, "abril": 4,
    "mai": 5, "may": 5, "mayo": 5,
    "juin": 6, "june": 6, "junio": 6,
    "juillet": 7, "july": 7, "julio": 7,
    "aout": 8, "august": 8, "agosto": 8,
    "septembre": 9, "sept": 9, "september": 9, "septiembre": 9,
    "octobre": 10, "oct": 10, "october": 10, "octubre": 10,
    "novembre": 11, "nov": 11, "november": 11, "noviembre": 11,
    "decembre": 12, "dec": 12, "december": 12, "diciembre": 12,
}


def _fold(value):
    value = unicodedata.normalize("NFKD", value or "")
    return "".join(c for c in value if not unicodedata.combining(c)).strip().upper()


def _clean_lines(text):
    return [re.sub(r"\s+", " ", line).strip() for line in (text or "").splitlines() if line.strip()]


def _parse_amount(value):
    if value is None:
        return None
    cleaned = re.sub(r"[^0-9,.\-]", "", str(value).replace("\u00a0", "").replace("\u202f", ""))
    if not cleaned or cleaned in {"-", ".", ","}:
        return None
    comma = cleaned.rfind(",")
    dot = cleaned.rfind(".")
    if comma >= 0 and dot >= 0:
        decimal = "," if comma > dot else "."
        thousands = "." if decimal == "," else ","
        cleaned = cleaned.replace(thousands, "").replace(decimal, ".")
    elif comma >= 0 or dot >= 0:
        separator = "," if comma >= 0 else "."
        fractional_length = len(cleaned) - cleaned.rfind(separator) - 1
        if cleaned.count(separator) > 1 or fractional_length == 3:
            cleaned = cleaned.replace(separator, "")
        else:
            cleaned = cleaned.replace(separator, ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


AMOUNT_TOKEN = re.compile(r"-?\d(?:[\d\s\u00a0\u202f.,']*\d)?(?:\s*(?:EUR|USD|GBP|CHF|€|\$|£))?", re.I)


def _last_amount(line):
    values = []
    for token in AMOUNT_TOKEN.findall(line or ""):
        value = _parse_amount(token)
        if value is not None:
            values.append(value)
    return values[-1] if values else None


def _extract_labeled_amount(lines, patterns, excluded=()):
    for line in reversed(lines):
        folded = _fold(line)
        if any(re.search(pattern, folded) for pattern in excluded):
            continue
        for pattern in patterns:
            label = re.search(pattern, folded)
            if not label:
                continue
            remainder = folded[label.end():]
            amount = re.fullmatch(
                r"\s*(?:\d{1,2}(?:[.,]\d+)?\s*%\s*)?(?:[:=\-]\s*)?"
                r"(?P<amount>-?\d(?:[\d\s.,']*\d)?(?:\s*(?:EUR|USD|GBP|CHF|€|\$|£))?)\s*",
                remainder,
                re.I,
            )
            if amount:
                value = _parse_amount(amount.group("amount"))
                if value is not None:
                    return value
    return None


def _parse_date(value):
    if not value:
        return None
    folded = _fold(value).lower()
    match = re.search(r"\b(\d{1,2})\s+([a-z]+)\s+(\d{4})\b", folded)
    if match and match.group(2) in MONTHS:
        try:
            return date(int(match.group(3)), MONTHS[match.group(2)], int(match.group(1))).isoformat()
        except ValueError:
            return None
    match = re.search(r"\b(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b", folded)
    if match:
        parts = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    else:
        match = re.search(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})\b", folded)
        if not match:
            return None
        parts = (int(match.group(3)), int(match.group(2)), int(match.group(1)))
    try:
        return date(*parts).isoformat()
    except ValueError:
        return None


def _extract_invoice_number(lines):
    patterns = (
        r"\bFACTURE[ \t]*(?:N[°º0O*]|NO\.?|NUMERO)[ \t]*[:#-]?[ \t]*(?P<number>[A-Z0-9][A-Z0-9*/._-]*)",
        r"\bN[°º0O*][ \t]*FACTURE[ \t]*[:#-]?[ \t]*(?P<number>[A-Z0-9][A-Z0-9*/._-]*)",
        r"\bNUMERO[ \t]+DE[ \t]+FACTURE[ \t]*[:#-]?[ \t]*(?P<number>[A-Z0-9][A-Z0-9*/._-]*)",
        r"\bINVOICE[ \t]+(?:NUMBER|NO\.?|#)[ \t]*[:#-]?[ \t]*(?P<number>[A-Z0-9][A-Z0-9*/._-]*)",
        r"\bN[°ºO][ \t]+FACTURA[ \t]*[:#-]?[ \t]*(?P<number>[A-Z0-9][A-Z0-9*/._-]*)",
        r"\bNUMERO[ \t]+DE[ \t]+FACTURA[ \t]*[:#-]?[ \t]*(?P<number>[A-Z0-9][A-Z0-9*/._-]*)",
    )
    for line in lines:
        folded = _fold(line)
        for pattern in patterns:
            match = re.search(pattern, folded, re.I)
            if not match:
                continue
            number = match.group("number").strip("-._/")
            if number in INVALID_INVOICE_NUMBERS or not re.search(r"\d", number):
                continue
            return number
    return None


def _heading(line):
    return _fold(re.sub(r"[:\-]+$", "", line)).strip()


def _extract_identifier(block, labels):
    label_pattern = "|".join(re.escape(label) for label in labels)
    for line in block:
        match = re.search(rf"(?:{label_pattern})\b\s*[:#-]?\s*([A-Z0-9 .-]{{5,24}})", _fold(line), re.I)
        if match:
            return re.sub(r"[^A-Z0-9]", "", match.group(1).upper())
    return None


def _extract_party(lines, headings):
    label_pattern = "|".join(re.escape(label) for label in sorted(headings, key=len, reverse=True))
    start = None
    inline_name = ""
    for index, line in enumerate(lines):
        if _heading(line) in headings:
            start = index
            break
        inline = re.match(rf"^\s*(?:{label_pattern})\s*[:=\-]\s*(.+?)\s*$", line, re.I)
        if inline:
            start = index
            inline_name = inline.group(1).strip()
            break
    if start is None:
        return {"name": ""}
    block = [inline_name] if inline_name else []
    all_stops = ISSUER_HEADINGS | CUSTOMER_HEADINGS | TABLE_HEADINGS
    stop_label_pattern = "|".join(re.escape(label) for label in sorted(all_stops, key=len, reverse=True))
    for line in lines[start + 1:]:
        heading = _heading(line)
        folded = _fold(line)
        if (heading in all_stops or
                re.match(rf"^\s*(?:{stop_label_pattern})\s*[:=\-]", line, re.I) or
                re.match(r"^(FACTURE|INVOICE|FACTURA|DATE|ECHEANCE|DUE DATE|FECHA|TOTAL|SOUS-TOTAL|SUBTOTAL|BASE IMPONIBLE)\b", folded)):
            break
        block.append(line)

    identifier_labels = r"^(NIF|CIF|SIREN|SIRET|TVA|VAT|VAT ID|TVA INTRACOMMUNAUTAIRE)\b"
    ignored = r"^(TEL|TELEPHONE|PHONE|EMAIL|E-MAIL|IBAN|BIC|SWIFT)\b"
    name = next((line for line in block if not re.match(identifier_labels, _fold(line)) and
                 not re.match(ignored, _fold(line))), "")
    address = "\n".join(line for line in block if line != name and
                         not re.match(identifier_labels, _fold(line)) and
                         not re.match(ignored, _fold(line)))
    nif = _extract_identifier(block, ["NIF", "CIF"])
    siren = _extract_identifier(block, ["SIREN"])
    siret = _extract_identifier(block, ["SIRET"])
    vat_id = _extract_identifier(block, ["VAT ID", "TVA INTRACOMMUNAUTAIRE", "VAT", "TVA"])
    result = {"name": name, "address": address}
    for key, value in (("nif", nif), ("siren", siren), ("siret", siret), ("vat_id", vat_id)):
        if value:
            result[key] = value
    if vat_id:
        result["vat_number"] = vat_id
    return result


def _extract_bank_details(lines):
    iban = None
    bic = None
    for line in lines:
        folded = _fold(line)
        if iban is None:
            match = re.search(r"\bIBAN\s*[:#-]?\s*([A-Z]{2}\s*\d{2}(?:\s*[A-Z0-9]){11,30})", folded)
            if match:
                candidate = re.sub(r"[^A-Z0-9]", "", match.group(1))
                if re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", candidate):
                    iban = candidate
        if bic is None:
            match = re.search(r"\b(?:BIC|SWIFT)\s*[:#-]?\s*([A-Z0-9]{8}(?:[A-Z0-9]{3})?)\b", folded)
            if match:
                bic = match.group(1)
    return iban, bic


def _document_type(text):
    folded = _fold(text)
    if re.search(r"\b(?:AVOIR|CREDIT NOTE|NOTA DE CREDITO)\b", folded):
        return "credit_note"
    if re.search(r"\b(?:DEVIS|QUOTATION|QUOTE)\b", folded):
        return "quote"
    if re.search(r"\b(?:BON DE COMMANDE|PURCHASE ORDER|ORDEN DE COMPRA)\b", folded):
        return "purchase_order"
    if re.search(r"\b(?:RELEVE BANCAIRE|BANK STATEMENT|EXTRACTO BANCARIO)\b", folded):
        return "bank_statement"
    if re.search(r"\b(?:FACTURE|INVOICE|FACTURA)\b", folded):
        return "invoice"
    return "unknown"


def _looks_like_quantity(line):
    return bool(re.fullmatch(r"\d+(?:[.,]\d+)?", line.strip()))


def _looks_like_amount(line):
    if _last_amount(line) is None:
        return False
    folded = _fold(line)
    return bool(re.search(r"(?:EUR|USD|GBP|CHF|€|\$|£)", line, re.I) or
                re.fullmatch(r"-?[\d\s.,']+", folded))


def _extract_lines(lines, net_amount=None):
    start = next((i for i, line in enumerate(lines) if _heading(line) in TABLE_HEADINGS), None)
    if start is None:
        return [], "none"
    candidates = []
    for line in lines[start + 1:]:
        folded = _fold(line)
        if (re.match(r"^(TOTAL HT|SOUS-TOTAL|SUBTOTAL|BASE IMPONIBLE|TOTAL TTC|TOTAL A PAYER|MONTANT TOTAL|IMPORTE TOTAL)\b", folded)
                and _last_amount(line) is not None):
            break
        if re.match(r"^(QTE|QUANTITE|QTY|QUANTITY|PRIX UNITAIRE|UNIT PRICE|PRECIO UNITARIO|TOTAL HT)$", folded):
            continue
        candidates.append(line)

    extracted = []
    index = 0
    while index + 3 < len(candidates):
        description = candidates[index]
        if (_looks_like_quantity(candidates[index + 1]) and _looks_like_amount(candidates[index + 2]) and
                _looks_like_amount(candidates[index + 3])):
            quantity = _parse_amount(candidates[index + 1])
            unit_price = _last_amount(candidates[index + 2])
            line_total = _last_amount(candidates[index + 3])
            if quantity is not None and unit_price is not None and line_total is not None:
                extracted.append({
                    "description": description,
                    "quantity": quantity,
                    "unit_price_net": unit_price,
                    "line_total_net": line_total,
                })
                index += 4
                continue
        index += 1

    if not extracted:
        return [], "none"
    total = round(sum(line["line_total_net"] for line in extracted), 2)
    if net_amount is None or abs(total - round(net_amount, 2)) > 0.02:
        return [], "none"
    return extracted, "high"


def _extract_ocr_inline_lines(lines, net_amount=None):
    """Accept an OCR line only when quantity arithmetic and invoice net total both agree."""
    if net_amount is None:
        return [], "none"
    amount = r"-?\d(?:[\d\s.,']*\d)?"
    expression = re.compile(
        rf"^(?P<description>.+?)\s+(?P<quantity>\d+(?:[.,]\d+)?)\s*[x×X]\s*"
        rf"(?P<unit>{amount})\s*(?:EUR|USD|GBP|CHF|€|\$|£)?\s+"
        rf"(?P<total>{amount})\s*(?:EUR|USD|GBP|CHF|€|\$|£)?\s*$",
        re.I,
    )
    for line in lines:
        match = expression.match(line)
        if not match:
            continue
        description = match.group("description").strip()
        if re.match(r"^(TOTAL|TVA|VAT|IVA|DATE|ECHEANCE|FACTURE)\b", _fold(description)):
            continue
        quantity = _parse_amount(match.group("quantity"))
        unit_price = _parse_amount(match.group("unit"))
        line_total = _parse_amount(match.group("total"))
        if None in (quantity, unit_price, line_total):
            continue
        if abs(round(quantity * unit_price, 2) - round(line_total, 2)) > 0.02:
            continue
        if abs(round(line_total, 2) - round(net_amount, 2)) > 0.02:
            continue
        return [{
            "description": description,
            "quantity": quantity,
            "unit_price_net": unit_price,
            "line_total_net": line_total,
        }], "high"
    return [], "none"


def _positioned_page(page, page_number):
    tokens = []

    def visitor(text, _cm, tm, _font, _font_size):
        try:
            x, y = float(tm[4]), float(tm[5])
        except (TypeError, ValueError, IndexError):
            return
        for fragment in (text or "").splitlines():
            cleaned = re.sub(r"\s+", " ", fragment).strip()
            if cleaned:
                tokens.append({"text": cleaned, "x": x, "y": y, "page": page_number})

    text = page.extract_text(visitor_text=visitor) or ""
    return text, tokens


def _group_positioned_rows(tokens, tolerance=4.0):
    rows = []
    for token in sorted(tokens, key=lambda item: (-item["y"], item["x"])):
        row = next((candidate for candidate in rows if abs(candidate["y"] - token["y"]) <= tolerance), None)
        if row is None:
            row = {"y": token["y"], "tokens": []}
            rows.append(row)
        row["tokens"].append(token)
        row["y"] = sum(item["y"] for item in row["tokens"]) / len(row["tokens"])
    return sorted(rows, key=lambda row: -row["y"])


def _column_kind(text):
    folded = _fold(text)
    if re.fullmatch(r"(?:DESIGNATION|DESCRIPTION|PRODUIT|SERVICE|ITEMS?)", folded):
        return "description"
    if re.fullmatch(r"(?:QTE|QUANTITE|QTY|QUANTITY)", folded):
        return "quantity"
    if re.fullmatch(r"(?:PRIX UNITAIRE(?: HT)?|PU(?: HT)?|UNIT PRICE|PRECIO UNITARIO)", folded):
        return "unit_price"
    if re.fullmatch(r"(?:TVA|VAT|IVA)(?:\s*%)?", folded):
        return "vat"
    if re.fullmatch(r"(?:TOTAL(?: HT)?|AMOUNT|IMPORTE)", folded):
        return "line_total"
    return None


def _extract_positioned_lines(positioned_pages, net_amount=None):
    if net_amount is None:
        return [], "none"
    all_reconstructed = []
    carried_columns = None
    for page_tokens in positioned_pages or []:
        rows = _group_positioned_rows(page_tokens)
        header_index = None
        columns = None
        for index, row in enumerate(rows):
            detected = {}
            for token in row["tokens"]:
                kind = _column_kind(token["text"])
                if kind and kind not in detected:
                    detected[kind] = token["x"]
            if {"description", "quantity", "unit_price", "line_total"}.issubset(detected):
                header_index, columns = index, detected
                break
        if header_index is None:
            if carried_columns is None:
                continue
            columns = carried_columns
            header_index = -1

        ordered_columns = sorted(columns.items(), key=lambda item: item[1])
        reconstructed = []
        reached_total = False
        for row in rows[header_index + 1:]:
            cells = {kind: [] for kind in columns}
            for token in sorted(row["tokens"], key=lambda item: item["x"]):
                kind = min(ordered_columns, key=lambda item: abs(item[1] - token["x"]))[0]
                cells[kind].append(token["text"])
            cells = {kind: " ".join(parts).strip() for kind, parts in cells.items()}
            description = cells.get("description", "")
            if re.match(r"^(TOTAL|SOUS-TOTAL|SUBTOTAL|BASE IMPONIBLE|NET A PAYER)", _fold(description)):
                reached_total = True
                break
            quantity = _parse_amount(cells.get("quantity"))
            unit_price = _last_amount(cells.get("unit_price"))
            line_total = _last_amount(cells.get("line_total"))
            if description and quantity is not None and unit_price is not None and line_total is not None:
                reconstructed.append({
                    "description": description,
                    "quantity": quantity,
                    "unit_price_net": unit_price,
                    "line_total_net": line_total,
                })
        all_reconstructed.extend(reconstructed)
        carried_columns = None if reached_total else columns
    if all_reconstructed:
        reconstructed_total = round(sum(line["line_total_net"] for line in all_reconstructed), 2)
        if abs(reconstructed_total - round(net_amount, 2)) <= 0.02:
            return all_reconstructed, "high"
    return [], "none"


_ACTIVE_COMPANY = object()


def _ocr_name_key(value):
    # Canonicalise the two most common OCR-only glyph confusions after removing spacing/punctuation.
    return re.sub(r"[^A-Z0-9]", "", _fold(value)).translate(str.maketrans({"1": "I", "0": "O"}))


def _ocr_company_name_match(company, party_name):
    party_key = _ocr_name_key(party_name)
    if len(party_key) < 10:
        return False
    try:
        aliases = json.loads(company.get("aliases_json") or "[]")
    except (TypeError, json.JSONDecodeError):
        aliases = []
    names = (company.get("legal_name"), company.get("trade_name"), *aliases)
    return any(party_key == _ocr_name_key(name) for name in names if name)


def _parse_text(text, company=_ACTIVE_COMPANY, positioned_pages=None, ocr_mode=False,
                normalization_metadata=None):
    lines = _clean_lines(text)
    issuer = _extract_party(lines, ISSUER_HEADINGS)
    customer = _extract_party(lines, CUSTOMER_HEADINGS)
    supplier_iban, supplier_bic = _extract_bank_details(lines)
    if supplier_iban:
        issuer["iban"] = supplier_iban
    if supplier_bic:
        issuer["bic"] = supplier_bic

    net_amount = _extract_labeled_amount(
        lines, [r"^TOTAL HT\b", r"^SOUS[- ]?TOTAL HT\b", r"^SUBTOTAL\b", r"^BASE IMPONIBLE\b"]
    )
    vat_amount = _extract_labeled_amount(lines, [r"^(TVA|IVA|VAT)(?=\s|\d|[:=\-]|$)"])
    gross_amount = _extract_labeled_amount(
        lines,
        [r"^TOTAL TTC\b", r"^TOTAL A PAYER\b", r"^NET A PAYER\b", r"^MONTANT TOTAL\b",
         r"^IMPORTE TOTAL\b", r"^AMOUNT DUE\b", r"^TOTAL\b"],
        excluded=[r"^TOTAL HT\b", r"^TOTAL TVA\b", r"^TOTAL IVA\b", r"^TOTAL VAT\b"],
    )

    invoice_number = _extract_invoice_number(lines)

    issue_date = None
    due_date = None
    due_terms = None
    for line in lines:
        folded = _fold(line)
        if re.match(r"^(DATE D'?ECHEANCE|ECHEANCE|DUE DATE|FECHA DE VENCIMIENTO)\b", folded):
            due_date = _parse_date(line)
            if not due_date:
                due_terms = line.split(":", 1)[-1].strip()
        elif re.match(r"^(DATE(?: DE FACTURE)?|INVOICE DATE|FECHA(?: DE FACTURA)?)\b", folded):
            issue_date = issue_date or _parse_date(line)
        if re.search(r"\bTOUS LES \d{1,2} DU MOIS\b", folded) or re.search(r"\bEVERY MONTH\b", folded):
            due_terms = line.strip()

    issue_date_candidate = issue_date
    issue_date_confidence = "high" if issue_date else "none"
    normalization_metadata = normalization_metadata or {}
    if ocr_mode and normalization_metadata.get("date_corrections") and issue_date:
        issue_date_confidence = "medium"
    if ocr_mode and issue_date and due_date and issue_date > due_date:
        # The year is never guessed or rewritten: retain the candidate for review and suppress it as a reliable value.
        issue_date_confidence = "low"
        issue_date = None

    folded_text = _fold(text)
    reverse_charge = any(phrase in folded_text for phrase in (
        "TVA NON APPLICABLE", "AUTOLIQUIDATION", "REVERSE CHARGE", "ARTICLE 196", "ART. 196",
        "ART 196", "INVERSION DEL SUJETO PASIVO",
    ))
    extracted_lines, lines_confidence = _extract_lines(lines, net_amount)
    lines_method = "sequential" if lines_confidence == "high" else None
    if lines_confidence != "high" and ocr_mode:
        extracted_lines, lines_confidence = _extract_ocr_inline_lines(lines, net_amount)
        if lines_confidence == "high":
            lines_method = "ocr_inline_arithmetic"
    if lines_confidence != "high" and positioned_pages:
        extracted_lines, lines_confidence = _extract_positioned_lines(positioned_pages, net_amount)
        if lines_confidence == "high":
            lines_method = "positional"
    if company is _ACTIVE_COMPANY:
        company = get_active_company()
    direction, direction_evidence, company_id = determine_invoice_direction(issuer, customer, company)
    if ocr_mode and direction == "unknown" and company:
        # OCR commonly damages identifiers. An exact normalised company-name match is still strong evidence;
        # no fuzzy or substring match is allowed here.
        issuer_name_match = (
            company_matches_party(company, {"name": issuer.get("name")}) or
            _ocr_company_name_match(company, issuer.get("name"))
        )
        customer_name_match = (
            company_matches_party(company, {"name": customer.get("name")}) or
            _ocr_company_name_match(company, customer.get("name"))
        )
        if issuer_name_match and not customer_name_match:
            direction, direction_evidence = "sale", "active_company_exact_name_ocr"
        elif customer_name_match and not issuer_name_match:
            direction, direction_evidence = "purchase", "active_company_exact_name_ocr"

    currency = "EUR"
    if re.search(r"\bUSD\b|\$", text, re.I):
        currency = "USD"
    elif re.search(r"\bGBP\b|£", text, re.I):
        currency = "GBP"
    elif re.search(r"\bCHF\b", text, re.I):
        currency = "CHF"

    totals_coherent = (
        net_amount is not None and vat_amount is not None and gross_amount is not None and
        abs(round(net_amount + vat_amount, 2) - round(gross_amount, 2)) <= 0.02
    )
    field_confidence = {
        "invoice_number": 0.95 if invoice_number else 0.0,
        "supplier": 0.90 if issuer.get("name") else 0.0,
        "customer": 0.90 if customer.get("name") else 0.0,
        "issue_date": {"high": 0.90, "medium": 0.65, "low": 0.30, "none": 0.0}[issue_date_confidence],
        "net_amount": 0.95 if net_amount is not None else 0.0,
        "vat_amount": 0.95 if vat_amount is not None and totals_coherent else (0.60 if vat_amount is not None else 0.0),
        "gross_amount": 0.95 if gross_amount is not None else 0.0,
        "lines": 0.95 if lines_confidence == "high" else 0.0,
        "direction": 0.95 if direction != "unknown" else 0.0,
    }
    structured_confidence = round(100 * sum(field_confidence.values()) / len(field_confidence), 1)
    needs_manual_validation = (
        direction == "unknown" or lines_confidence != "high" or not totals_coherent or
        issue_date_confidence in {"low", "none"}
    )

    return {
        "document_type": _document_type(text),
        "invoice_number": invoice_number,
        "issuer": issuer,
        "supplier": dict(issuer),
        "customer": customer,
        "issue_date": issue_date,
        "issue_date_candidate": issue_date_candidate if issue_date_confidence == "low" else None,
        "issue_date_confidence": issue_date_confidence,
        "due_date": due_date,
        "due_terms": due_terms,
        "net_amount": 0.0 if net_amount is None else net_amount,
        "vat_amount": 0.0 if vat_amount is None else vat_amount,
        "gross_amount": 0.0 if gross_amount is None else gross_amount,
        "currency": currency,
        "lines": extracted_lines,
        "lines_extraction_confidence": lines_confidence,
        "lines_extraction_method": lines_method,
        "vat_mechanism": "reverse_charge" if reverse_charge else None,
        "vat_treatment": "reverse_charge" if reverse_charge else None,
        "direction": direction,
        "direction_evidence": direction_evidence,
        "company_id": company_id,
        "amounts_arithmetic_coherent": totals_coherent,
        "field_confidence": field_confidence,
        "structured_extraction_confidence": structured_confidence,
        "needs_manual_validation": needs_manual_validation,
        "raw_text": (text or "")[:30000],
    }


def parse_pdf(path):
    reader = PdfReader(str(path))
    attachments = getattr(reader, "attachments", {}) or {}
    standard_names = {"factur-x.xml": 0, "zugferd-invoice.xml": 1, "xrechnung.xml": 2}
    attachment_items = sorted(attachments.items(), key=lambda item: standard_names.get(item[0].lower(), 10))
    for name, blobs in attachment_items:
        if name.lower().endswith(".xml"):
            blob = blobs[0] if isinstance(blobs, list) else blobs
            with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as handle:
                handle.write(blob)
                temporary_xml = handle.name
            try:
                data = parse_xml_invoice(temporary_xml)
                if data.get("format") not in {"CII", "UBL"}:
                    continue
                data["format"] = "FACTUR_X"
                data["structured_source"] = "embedded_xml"
                data["embedded_xml_name"] = name
                return data
            except (ValueError, OSError):
                pass
            finally:
                Path(temporary_xml).unlink(missing_ok=True)

    page_results = [_positioned_page(page, index) for index, page in enumerate(reader.pages)]
    text = "\n".join(result[0] for result in page_results)
    positioned_pages = [result[1] for result in page_results]
    ocr_used = False
    ocr_error = None
    ocr_confidence = None
    ocr_result = {}
    normalization = None
    if not text.strip():
        result = ocr_pdf(path)
        if result["ok"]:
            ocr_result = result
            normalization = normalize_ocr_text(result["text"])
            text = normalization["normalized_text"]
            ocr_used = True
            ocr_confidence = result.get("confidence")
        else:
            ocr_error = result.get("error")

    parsed = _parse_text(
        text, positioned_pages=positioned_pages, ocr_mode=ocr_used,
        normalization_metadata=normalization,
    )
    parsed.update({
        "format": "PDF_OCR" if ocr_used else ("PDF_TEXT" if text.strip() else "PDF_IMAGE"),
        "needs_manual_extraction": not bool(text.strip()),
        "ocr_used": ocr_used,
        "ocr_error": ocr_error,
        "ocr_confidence": ocr_confidence,
        "raw_ocr_text": normalization["raw_text"][:30000] if normalization else None,
        "normalized_ocr_text": normalization["normalized_text"][:30000] if normalization else None,
        "ocr_normalization": {
            "transformations": normalization["transformations"],
            "date_corrections": normalization["date_corrections"],
            "year_corrected": normalization["year_corrected"],
        } if normalization else None,
        "ocr_languages": ocr_result.get("languages"),
        "ocr_tesseract_version": ocr_result.get("tesseract_version"),
        "ocr_render_dpi": ocr_result.get("render_dpi"),
        "ocr_preprocessing": ocr_result.get("preprocessing"),
        "ocr_candidate_confidences": ocr_result.get("candidate_confidences"),
        "ocr_binarization": ocr_result.get("binarization"),
        "ocr_deskew": ocr_result.get("deskew"),
    })
    if ocr_used and (ocr_confidence is None or ocr_confidence < 70):
        parsed["needs_manual_validation"] = True
    return parsed
