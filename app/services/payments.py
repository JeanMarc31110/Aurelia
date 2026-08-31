import json
from datetime import date
from decimal import Decimal, InvalidOperation

from app.db import connect


TOLERANCE = Decimal("0.02")


def _money(value):
    try:return Decimal(str(value or 0)).quantize(Decimal("0.01"))
    except InvalidOperation as exc:raise ValueError("Montant de paiement invalide") from exc


def _audit(con, invoice_id, username, event, field_name=None, old_value=None, new_value=None,
           reason=None, details=None, agent="PAYMENT"):
    con.execute(
        """INSERT INTO audit_events(
             invoice_id,username,agent,event,details,field_name,old_value,new_value,reason)
           VALUES(?,?,?,?,?,?,?,?,?)""",
        (invoice_id, username, agent, event, json.dumps(details or {}, ensure_ascii=False), field_name,
         None if old_value is None else str(old_value), None if new_value is None else str(new_value), reason),
    )


def refresh_payment_status(invoice_id, today=None, username="system"):
    today = today or date.today()
    con = connect()
    row = con.execute("SELECT * FROM invoices WHERE id=?", (invoice_id,)).fetchone()
    if not row:
        con.close();raise ValueError("Facture introuvable")
    paid = _money(con.execute(
        "SELECT COALESCE(SUM(allocated_amount),0) FROM payment_matches WHERE invoice_id=? AND cancelled_at IS NULL",
        (invoice_id,),
    ).fetchone()[0])
    gross = _money(row["gross_amount"])
    remaining = gross - paid
    if paid > gross + TOLERANCE:
        status = "PAYMENT_MISMATCH"
    elif abs(paid - gross) <= TOLERANCE and gross > 0:
        status = "PAID"
        remaining = Decimal("0.00")
    elif paid > TOLERANCE:
        status = "PARTIALLY_PAID"
    else:
        try:overdue = bool(row["due_date"] and date.fromisoformat(row["due_date"]) < today)
        except ValueError:overdue = False
        status = "OVERDUE" if overdue else "UNPAID"
    old_status = row["payment_status"] or "UNPAID"
    con.execute(
        "UPDATE invoices SET payment_status=?,amount_paid=?,amount_remaining=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (status, float(paid), float(remaining), invoice_id),
    )
    if old_status != status:
        _audit(con, invoice_id, username, "payment_status_changed", "payment_status", old_status, status,
               details={"amount_paid": float(paid), "amount_remaining": float(remaining)})
    con.commit();con.close()
    return {"payment_status": status, "amount_paid": float(paid), "amount_remaining": float(remaining)}


def validate_payment_match(transaction_id, invoice_id, username, allocated_amount=None, comment=None):
    con = connect()
    transaction = con.execute("SELECT * FROM bank_transactions WHERE id=?", (transaction_id,)).fetchone()
    invoice = con.execute("SELECT * FROM invoices WHERE id=?", (invoice_id,)).fetchone()
    if not transaction or transaction["status"] != "PENDING":
        con.close();raise ValueError("Transaction bancaire indisponible")
    if not invoice or invoice["status"] != "APPROVED":
        con.close();raise ValueError("Seule une facture approuvée peut être rapprochée")
    if invoice["direction"] == "purchase" and transaction["amount"] >= 0:
        con.close();raise ValueError("Un paiement fournisseur doit être une sortie bancaire")
    if invoice["direction"] == "sale" and transaction["amount"] <= 0:
        con.close();raise ValueError("Un encaissement client doit être une entrée bancaire")
    amount = _money(allocated_amount if allocated_amount not in {None, ""} else abs(transaction["amount"]))
    if amount <= 0:
        con.close();raise ValueError("Le montant affecté doit être positif.")
    bank_amount = _money(abs(transaction["amount"]))
    if amount > bank_amount + TOLERANCE:
        con.close();raise ValueError("Le montant affecté dépasse le montant disponible sur la transaction bancaire.")
    try:
        cursor = con.execute(
            """INSERT INTO payment_matches(transaction_id,invoice_id,allocated_amount,username,comment)
               VALUES(?,?,?,?,?)""", (transaction_id, invoice_id, float(amount), username, (comment or "").strip() or None),
        )
    except Exception as exc:
        con.close();raise ValueError("Cette transaction possède déjà un rapprochement actif") from exc
    con.execute("UPDATE bank_transactions SET status='MATCHED',matched_invoice_id=? WHERE id=?", (invoice_id, transaction_id))
    con.execute("UPDATE payment_match_proposals SET status=CASE WHEN invoice_id=? THEN 'ACCEPTED' ELSE 'REJECTED' END,updated_at=CURRENT_TIMESTAMP WHERE transaction_id=?", (invoice_id, transaction_id))
    _audit(con, invoice_id, username, "payment_match_validated", "amount_paid", None, float(amount),
           reason=(comment or "").strip() or None,
           details={"transaction_id": transaction_id, "match_id": cursor.lastrowid, "bank_amount": transaction["amount"]})
    con.commit();con.close()
    result = refresh_payment_status(invoice_id, username=username)
    return dict(result, match_id=cursor.lastrowid)


def cancel_payment_match(match_id, username, reason):
    reason = (reason or "").strip()
    if not reason:raise ValueError("Une raison d'annulation est obligatoire")
    con = connect()
    match = con.execute("SELECT * FROM payment_matches WHERE id=? AND cancelled_at IS NULL", (match_id,)).fetchone()
    if not match:
        con.close();raise ValueError("Rapprochement actif introuvable")
    con.execute("UPDATE payment_matches SET cancelled_at=CURRENT_TIMESTAMP,cancelled_by=?,cancellation_reason=? WHERE id=?", (username, reason, match_id))
    con.execute("UPDATE bank_transactions SET status='PENDING',matched_invoice_id=NULL WHERE id=?", (match["transaction_id"],))
    _audit(con, match["invoice_id"], username, "payment_match_cancelled", "amount_paid",
           match["allocated_amount"], None, reason=reason, details={"transaction_id": match["transaction_id"], "match_id": match_id})
    con.commit();con.close()
    return refresh_payment_status(match["invoice_id"], username=username)


def ignore_transaction(transaction_id, username, comment=None):
    con = connect()
    row = con.execute("SELECT * FROM bank_transactions WHERE id=? AND status='PENDING'", (transaction_id,)).fetchone()
    if not row:
        con.close();raise ValueError("Transaction en attente introuvable")
    con.execute("UPDATE bank_transactions SET status='IGNORED',ignored_by=?,ignored_at=CURRENT_TIMESTAMP WHERE id=?", (username, transaction_id))
    con.execute("UPDATE payment_match_proposals SET status='IGNORED',updated_at=CURRENT_TIMESTAMP WHERE transaction_id=? AND status='PENDING'", (transaction_id,))
    con.commit();con.close()


def set_account_final(invoice_id, account, username, reason=None):
    account = (account or "").strip()
    if not account:raise ValueError("Le compte comptable est obligatoire")
    con = connect();row = con.execute("SELECT * FROM invoices WHERE id=?", (invoice_id,)).fetchone()
    if not row:
        con.close();raise ValueError("Facture introuvable")
    old = row["account_final"]
    con.execute("UPDATE invoices SET account_final=?,account_validated_by=?,account_validated_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=?", (account, username, invoice_id))
    _audit(con, invoice_id, username, "account_final_validated", "account_final", old, account, reason=reason)
    con.commit();con.close()
    return account


def payment_overview():
    con = connect();ids = [row[0] for row in con.execute("SELECT id FROM invoices WHERE status='APPROVED'")];con.close()
    for invoice_id in ids:refresh_payment_status(invoice_id)
    con = connect();rows = [dict(row) for row in con.execute(
        """SELECT * FROM invoices WHERE status='APPROVED'
           ORDER BY CASE WHEN due_date IS NULL THEN 1 ELSE 0 END,due_date,id""",
    )];con.close()
    today = date.today()
    for row in rows:
        try:row["days_overdue"] = max(0, (today - date.fromisoformat(row["due_date"])).days) if row["due_date"] else None
        except ValueError:row["days_overdue"] = None
        try:row["due_soon"] = 0 <= (date.fromisoformat(row["due_date"]) - today).days < 7
        except (TypeError, ValueError):row["due_soon"] = False
    return {
        "rows": rows,
        "to_pay": [row for row in rows if row["direction"] == "purchase" and row["payment_status"] in {"UNPAID","OVERDUE","PARTIALLY_PAID","PAYMENT_MISMATCH"}],
        "to_collect": [row for row in rows if row["direction"] == "sale" and row["payment_status"] in {"UNPAID","OVERDUE","PARTIALLY_PAID","PAYMENT_MISMATCH"}],
        "due_soon": [row for row in rows if row["due_soon"] and row["payment_status"] == "UNPAID"],
        "overdue": [row for row in rows if row["payment_status"] == "OVERDUE"],
        "partial": [row for row in rows if row["payment_status"] == "PARTIALLY_PAID"],
        "paid": [row for row in rows if row["payment_status"] == "PAID"],
        "mismatch": [row for row in rows if row["payment_status"] == "PAYMENT_MISMATCH"],
    }
