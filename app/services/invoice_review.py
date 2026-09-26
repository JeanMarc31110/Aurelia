import json
from datetime import date, timedelta

from app.db import connect
from app.services.supplier_banks import mask_iban


METHOD_LABELS = {
    "PDF_TEXT": "PDF texte",
    "PDF_OCR": "OCR Tesseract",
    "PDF_IMAGE": "PDF image",
    "UBL": "UBL",
    "CII": "CII",
    "FACTUR_X": "Factur-X",
}
HUMAN_EVENTS = {"approved", "rejected", "corrected", "rib_accepted", "rib_rejected",
                "payment_match_validated", "payment_match_cancelled", "payment_status_changed",
                "account_final_validated", "accounting_exported"}


def _json(value, fallback):
    try:
        return json.loads(value) if value else fallback
    except (TypeError, json.JSONDecodeError):
        return fallback


def confidence(value):
    if value is None:
        return {"label": "Non disponible", "class": "neutral", "percent": None}
    numeric = float(value)
    if 0 <= numeric <= 1:
        numeric *= 100
    label = "Haute" if numeric >= 85 else ("Moyenne" if numeric >= 65 else "Faible")
    css = "success" if label == "Haute" else ("warning" if label == "Moyenne" else "error")
    return {"label": label, "class": css, "percent": round(numeric, 1)}


def _format_value(value):
    if value is None or value == "":
        return "—"
    text = str(value)
    return text if len(text) <= 180 else text[:177] + "…"


def get_invoice_detail(invoice_id):
    con = connect()
    row = con.execute("SELECT * FROM invoices WHERE id=?", (invoice_id,)).fetchone()
    if not row:
        con.close()
        return None
    invoice = dict(row)
    raw = _json(invoice.get("raw_json"), {})
    stored_lines = _json(invoice.get("lines_json"), [])
    lines = stored_lines or raw.get("lines") or []
    events = [dict(item) for item in con.execute(
        "SELECT * FROM audit_events WHERE invoice_id=? ORDER BY id DESC", (invoice_id,),
    )]
    payment_matches = [dict(item) for item in con.execute(
        """SELECT pm.*,t.booking_date,t.label,t.reference,t.amount bank_amount
           FROM payment_matches pm JOIN bank_transactions t ON t.id=pm.transaction_id
           WHERE pm.invoice_id=? ORDER BY pm.id DESC""", (invoice_id,),
    )]
    findings = []
    history = []
    rib_alert = None
    for event in events:
        details = _json(event.get("details"), {})
        if isinstance(details, dict) and "ok" in details and "message" in details:
            finding = dict(details)
            finding["display_level"] = "success" if finding.get("ok") else finding.get("severity", "warning")
            findings.append(finding)
            if finding.get("code") == "RIB_CHANGED":
                rib = finding.get("details") or {}
                rib_alert = {
                    "supplier": rib.get("supplier") or invoice.get("supplier_name"),
                    "old_iban": rib.get("old_iban_masked") or "****",
                    "new_iban": rib.get("new_iban_masked") or "****",
                    "pending_account_id": rib.get("pending_account_id"),
                }
        if event.get("event") in HUMAN_EVENTS:
            event["details_data"] = details
            event["old_display"] = _format_value(event.get("old_value"))
            event["new_display"] = _format_value(event.get("new_value"))
            history.append(event)
    if rib_alert and rib_alert.get("pending_account_id"):
        bank = con.execute(
            "SELECT status FROM supplier_bank_accounts WHERE id=?", (rib_alert["pending_account_id"],),
        ).fetchone()
        rib_alert["pending"] = bool(bank and bank["status"] == "PENDING")
    con.close()

    issuer = raw.get("supplier") or raw.get("issuer") or {}
    customer = raw.get("customer") or {}
    method = METHOD_LABELS.get(invoice.get("format"), invoice.get("format") or "Non déterminée")
    structured_value = raw.get("structured_extraction_confidence")
    if structured_value is None and invoice.get("format") in {"UBL", "CII", "FACTUR_X"}:
        structured_value = 100
    original = {
        "supplier_name": issuer.get("name"), "customer_name": customer.get("name"),
        "invoice_number": raw.get("invoice_number"), "issue_date": raw.get("issue_date"),
        "due_date": raw.get("due_date"), "net_amount": raw.get("net_amount"),
        "vat_amount": raw.get("vat_amount"), "gross_amount": raw.get("gross_amount"),
        "direction": raw.get("direction"), "lines": raw.get("lines") or [],
    }
    lines_text = "\n".join(
        f"{line.get('description','')} | {line.get('quantity','')} | "
        f"{line.get('unit_price_net','')} | {line.get('line_total_net','')}" for line in lines
    )
    vat_rate = raw.get("vat_rate")
    if vat_rate is None:
        rates = raw.get("vat_rates") or []
        vat_rate = rates[0] if len(rates) == 1 else None
    invoice.update({
        "raw": raw, "issuer": issuer, "customer": customer, "lines": lines,
        "lines_text": lines_text, "findings": findings, "history": history,
        "rib_alert": rib_alert, "method_label": method,
        "ocr_confidence_display": confidence(raw.get("ocr_confidence")),
        "structured_confidence_display": confidence(structured_value),
        "original": original, "vat_rate": vat_rate,
        "vat_mechanism": raw.get("vat_mechanism") or raw.get("vat_treatment"),
        "can_approve": invoice.get("document_type") in {"invoice", "credit_note"} and
                       invoice.get("status") not in {"APPROVED", "REJECTED", "DUPLICATE"},
        "can_edit": invoice.get("status") not in {"APPROVED", "REJECTED"},
        "can_reject": invoice.get("status") != "APPROVED",
        "payment_matches": payment_matches,
    })
    return invoice


def get_document_detail(document_id):
    con = connect()
    row = con.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
    con.close()
    if not row:
        return None
    document = dict(row)
    document["raw"] = _json(document.get("raw_json"), {})
    document["raw"].setdefault("issuer", document["raw"].get("supplier") or {})
    document["raw"].setdefault("supplier", document["raw"].get("issuer") or {})
    document["raw"].setdefault("customer", {})
    document["findings"] = _json(document.get("findings_json"), [])
    document["method_label"] = METHOD_LABELS.get(document["raw"].get("format"), document["raw"].get("format") or "Non déterminée")
    return document


def dashboard_data():
    today = date.today().isoformat()
    soon = (date.today() + timedelta(days=7)).isoformat()
    con = connect()
    row = con.execute(
        """SELECT COUNT(*) total,
           SUM(status='REVIEW_REQUIRED' OR status='VALIDATED') pending,
           SUM(status='APPROVED') approved,
           SUM(status='REJECTED') rejected,
           SUM(status='DUPLICATE') duplicates,
           SUM(due_date BETWEEN ? AND ? AND status NOT IN ('APPROVED','REJECTED')) due_soon
           FROM invoices""", (today, soon),
    ).fetchone()
    result = dict(row)
    result["rib_alerts"] = con.execute(
        """SELECT COUNT(DISTINCT a.invoice_id) FROM audit_events a
           JOIN supplier_bank_accounts b
             ON b.id=CAST(json_extract(a.details,'$.details.pending_account_id') AS INTEGER)
           WHERE a.event='RIB_CHANGED' AND b.status='PENDING'""",
    ).fetchone()[0]
    con.close()
    return result


def work_queue_data():
    invoices, _ = list_invoices({})
    groups = {"to_review": [], "anomalies": [], "to_validate": [], "blocked": []}
    for invoice in invoices:
        status = invoice.get("status")
        if status not in {"REVIEW_REQUIRED", "VALIDATED", "DUPLICATE"}:
            continue
        raw = _json(invoice.get("raw_json"), {})
        invoice["confidence_display"] = confidence(raw.get("structured_extraction_confidence"))
        document_risk = invoice.get("document_risk_score") or 0
        fraud_risk = invoice.get("fraud_risk_score") or 0
        if status == "DUPLICATE":
            invoice["attention_reason"] = "Doublon potentiel à contrôler"
            groups["blocked"].append(invoice)
        elif status == "VALIDATED":
            invoice["attention_reason"] = "Contrôles terminés, décision humaine attendue"
            groups["to_validate"].append(invoice)
        elif document_risk or fraud_risk:
            invoice["attention_reason"] = (
                "Alerte fraude ou RIB à examiner" if fraud_risk else
                "Anomalie documentaire à examiner"
            )
            groups["anomalies"].append(invoice)
        else:
            invoice["attention_reason"] = "Données extraites à vérifier"
            groups["to_review"].append(invoice)
    return groups


def list_invoices(filters):
    clauses = []
    parameters = []
    status = (filters.get("status") or "").strip()
    if status:
        if status == "PENDING":
            clauses.append("i.status IN ('REVIEW_REQUIRED','VALIDATED')")
        else:
            clauses.append("i.status=?")
            parameters.append(status)
    direction = (filters.get("direction") or "").strip()
    if direction:
        clauses.append("i.direction=?")
        parameters.append(direction)
    supplier = (filters.get("supplier") or "").strip()
    if supplier:
        clauses.append("i.supplier_name LIKE ?")
        parameters.append(f"%{supplier}%")
    document_type = (filters.get("document_type") or "").strip()
    if document_type:
        clauses.append("i.document_type=?")
        parameters.append(document_type)
    date_from = (filters.get("date_from") or "").strip()
    if date_from:
        clauses.append("i.issue_date>=?")
        parameters.append(date_from)
    date_to = (filters.get("date_to") or "").strip()
    if date_to:
        clauses.append("i.issue_date<=?")
        parameters.append(date_to)
    alert = (filters.get("alert") or "").strip()
    if alert == "DUPLICATE":
        clauses.append("i.status='DUPLICATE'")
    elif alert == "RIB_CHANGED":
        clauses.append("""EXISTS(SELECT 1 FROM audit_events a
          JOIN supplier_bank_accounts b
            ON b.id=CAST(json_extract(a.details,'$.details.pending_account_id') AS INTEGER)
          WHERE a.invoice_id=i.id AND a.event='RIB_CHANGED' AND b.status='PENDING')""")
    elif alert == "due":
        clauses.append("i.due_date BETWEEN ? AND ? AND i.status NOT IN ('APPROVED','REJECTED')")
        parameters.extend([date.today().isoformat(), (date.today() + timedelta(days=7)).isoformat()])
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    con = connect()
    rows = [dict(row) for row in con.execute(
        "SELECT i.* FROM invoices i" + where + " ORDER BY i.id DESC LIMIT 250", parameters,
    )]
    suppliers = [row[0] for row in con.execute(
        "SELECT DISTINCT supplier_name FROM invoices WHERE supplier_name IS NOT NULL AND supplier_name<>'' ORDER BY supplier_name",
    )]
    con.close()
    return rows, suppliers


def masked_supplier_iban(invoice):
    raw = invoice.get("raw") or {}
    supplier = raw.get("supplier") or raw.get("issuer") or {}
    value = supplier.get("iban")
    return mask_iban(value) if value else None
