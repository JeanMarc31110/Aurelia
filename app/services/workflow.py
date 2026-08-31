import json
from datetime import date
from decimal import Decimal, InvalidOperation

from app.db import connect
from app.services.learning import learn


REJECTION_REASONS = {
    "duplicate": "Doublon",
    "not_invoice": "Document non facture",
    "incorrect_data": "Données incorrectes",
    "unknown_supplier": "Fournisseur inconnu",
    "other": "Autre",
}
EDITABLE_FIELDS = (
    "supplier_name", "customer_name", "invoice_number", "issue_date", "due_date",
    "net_amount", "vat_amount", "gross_amount", "direction",
)


def _audit(con, invoice_id, username, event, field_name=None, old_value=None, new_value=None,
           reason=None, details=None, agent="HUMAN"):
    con.execute(
        """INSERT INTO audit_events(
             invoice_id,username,agent,event,details,field_name,old_value,new_value,reason)
           VALUES(?,?,?,?,?,?,?,?,?)""",
        (invoice_id, username, agent, event,
         json.dumps(details or {}, ensure_ascii=False), field_name,
         None if old_value is None else str(old_value),
         None if new_value is None else str(new_value), reason),
    )


def record_audit(invoice_id, username, event, reason=None, details=None):
    con = connect()
    _audit(con, invoice_id, username, event, reason=reason, details=details)
    con.commit()
    con.close()


def approve_invoice(invoice_id, username, account=None, comment=None):
    con = connect()
    row = con.execute("SELECT * FROM invoices WHERE id=?", (invoice_id,)).fetchone()
    if not row:
        con.close()
        raise ValueError("Facture introuvable")
    if row["document_type"] not in {"invoice", "credit_note"}:
        con.close()
        raise ValueError("Ce document n'est pas dans le workflow comptable")
    if row["status"] in {"APPROVED", "REJECTED", "DUPLICATE"}:
        con.close()
        raise ValueError(f"Validation non autorisée depuis le statut {row['status']}")
    invoice = json.loads(row["raw_json"])
    supplier = invoice.get("supplier") or invoice.get("issuer") or {}
    supplier["name"] = row["supplier_name"] or supplier.get("name")
    invoice["supplier"] = supplier
    invoice["customer"] = dict(invoice.get("customer") or {}, name=row["customer_name"])
    for field in ("invoice_number", "issue_date", "due_date", "net_amount", "vat_amount",
                  "gross_amount", "direction"):
        invoice[field] = row[field]
    current_lines = json.loads(row["lines_json"] or "[]")
    if current_lines:
        invoice["lines"] = current_lines
    approved_account = account or row["proposed_account"]
    con.execute(
        """UPDATE invoices SET status='APPROVED',approved_account=?,approved_by=?,
           approved_at=CURRENT_TIMESTAMP,validation_comment=?,payment_status='UNPAID',amount_paid=0,
           amount_remaining=COALESCE(gross_amount,0),account_final=COALESCE(?,account_final),
           account_validated_by=CASE WHEN ? IS NOT NULL THEN ? ELSE account_validated_by END,
           account_validated_at=CASE WHEN ? IS NOT NULL THEN CURRENT_TIMESTAMP ELSE account_validated_at END,
           updated_at=CURRENT_TIMESTAMP WHERE id=?""",
        (approved_account, username, (comment or "").strip() or None, approved_account,
         approved_account, username, approved_account, invoice_id),
    )
    con.execute(
        """UPDATE reviews SET resolved=1,resolution='APPROVED',resolved_by=?,
           resolved_at=CURRENT_TIMESTAMP WHERE invoice_id=? AND resolved=0""",
        (username, invoice_id),
    )
    _audit(con, invoice_id, username, "approved", "status", row["status"], "APPROVED",
           reason=(comment or "").strip() or None, details={"account": approved_account})
    con.commit()
    con.close()
    if approved_account:
        learn(invoice, approved_account)
    return {"status": "APPROVED", "account": approved_account}


def reject_invoice(invoice_id, username, reason, comment=None):
    if reason not in REJECTION_REASONS:
        raise ValueError("Motif de rejet invalide")
    label = REJECTION_REASONS[reason]
    detail = (comment or "").strip()
    full_reason = f"{label} — {detail}" if detail else label
    con = connect()
    row = con.execute("SELECT * FROM invoices WHERE id=?", (invoice_id,)).fetchone()
    if not row:
        con.close()
        raise ValueError("Facture introuvable")
    if row["status"] == "APPROVED":
        con.close()
        raise ValueError("Une facture approuvée ne peut pas être rejetée depuis cet écran")
    con.execute(
        """UPDATE invoices SET status='REJECTED',rejected_by=?,rejected_at=CURRENT_TIMESTAMP,
           rejection_reason=?,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
        (username, full_reason, invoice_id),
    )
    con.execute(
        """UPDATE reviews SET resolved=1,resolution='REJECTED',resolved_by=?,
           resolved_at=CURRENT_TIMESTAMP WHERE invoice_id=? AND resolved=0""",
        (username, invoice_id),
    )
    _audit(con, invoice_id, username, "rejected", "status", row["status"], "REJECTED",
           reason=full_reason, details={"reason_code": reason})
    con.commit()
    con.close()
    return {"status": "REJECTED"}


def _date_value(value, field):
    value = (value or "").strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ValueError(f"Date invalide pour {field}") from exc


def _amount_value(value, field):
    try:
        amount = Decimal(str(value).strip().replace(",", "."))
    except (InvalidOperation, AttributeError) as exc:
        raise ValueError(f"Montant invalide pour {field}") from exc
    if not amount.is_finite():
        raise ValueError(f"Montant invalide pour {field}")
    return amount.quantize(Decimal("0.01"))


def parse_lines_text(lines_text, expected_net):
    lines = []
    for number, text in enumerate((lines_text or "").splitlines(), start=1):
        if not text.strip():
            continue
        parts = [part.strip() for part in text.split("|")]
        if len(parts) != 4 or not parts[0]:
            raise ValueError(f"Ligne {number} : format attendu Description | Quantité | Prix unitaire | Total")
        quantity = _amount_value(parts[1], f"quantité ligne {number}")
        unit_price = _amount_value(parts[2], f"prix unitaire ligne {number}")
        line_total = _amount_value(parts[3], f"total ligne {number}")
        if abs(quantity * unit_price - line_total) > Decimal("0.02"):
            raise ValueError(f"Ligne {number} : quantité × prix unitaire ne correspond pas au total")
        lines.append({
            "description": parts[0], "quantity": float(quantity),
            "unit_price_net": float(unit_price), "line_total_net": float(line_total),
        })
    if lines:
        total = sum(Decimal(str(line["line_total_net"])) for line in lines)
        if abs(total - expected_net) > Decimal("0.02"):
            raise ValueError("La somme des lignes ne correspond pas au montant HT")
    return lines


def correct_invoice(invoice_id, username, values, justification):
    justification = (justification or "").strip()
    if not justification:
        raise ValueError("Une justification est obligatoire")
    cleaned = {
        "supplier_name": (values.get("supplier_name") or "").strip() or None,
        "customer_name": (values.get("customer_name") or "").strip() or None,
        "invoice_number": (values.get("invoice_number") or "").strip() or None,
        "issue_date": _date_value(values.get("issue_date"), "date facture"),
        "due_date": _date_value(values.get("due_date"), "échéance"),
        "net_amount": _amount_value(values.get("net_amount"), "HT"),
        "vat_amount": _amount_value(values.get("vat_amount"), "TVA"),
        "gross_amount": _amount_value(values.get("gross_amount"), "TTC"),
        "direction": (values.get("direction") or "unknown").strip(),
    }
    if cleaned["direction"] not in {"purchase", "sale", "unknown"}:
        raise ValueError("Direction invalide")
    if abs(cleaned["net_amount"] + cleaned["vat_amount"] - cleaned["gross_amount"]) > Decimal("0.02"):
        raise ValueError("HT + TVA ne correspond pas au TTC")
    lines = parse_lines_text(values.get("lines_text"), cleaned["net_amount"])

    con = connect()
    row = con.execute("SELECT * FROM invoices WHERE id=?", (invoice_id,)).fetchone()
    if not row:
        con.close()
        raise ValueError("Facture introuvable")
    if row["status"] in {"APPROVED", "REJECTED"}:
        con.close()
        raise ValueError("Cette facture n'est plus modifiable")
    changed = []
    for field in EDITABLE_FIELDS:
        new_value = float(cleaned[field]) if isinstance(cleaned[field], Decimal) else cleaned[field]
        old_value = row[field]
        if old_value != new_value:
            changed.append((field, old_value, new_value))
    old_lines = row["lines_json"] or "[]"
    new_lines = json.dumps(lines, ensure_ascii=False)
    if json.loads(old_lines or "[]") != lines:
        changed.append(("lines", old_lines, new_lines))
    if not changed:
        con.close()
        raise ValueError("Aucune modification détectée")

    assignments = ",".join(f"{field}=?" for field in EDITABLE_FIELDS)
    sql_values = [float(cleaned[field]) if isinstance(cleaned[field], Decimal) else cleaned[field]
                  for field in EDITABLE_FIELDS]
    con.execute(
        f"""UPDATE invoices SET {assignments},lines_json=?,status='REVIEW_REQUIRED',
            updated_at=CURRENT_TIMESTAMP WHERE id=?""",
        (*sql_values, new_lines, invoice_id),
    )
    for field, old_value, new_value in changed:
        _audit(con, invoice_id, username, "corrected", field, old_value, new_value,
               reason=justification)
    con.execute(
        "INSERT INTO reviews(invoice_id,reason,severity) VALUES(?,?,?)",
        (invoice_id, f"Correction humaine à valider : {justification}", "warning"),
    )
    con.commit()
    con.close()
    return {"status": "REVIEW_REQUIRED", "changed_fields": [item[0] for item in changed]}
