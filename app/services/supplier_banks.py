import re

from app.db import connect


def _normalise_identifier(value):
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


def normalise_iban(value):
    iban = _normalise_identifier(value)
    return iban if re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", iban) else None


def mask_iban(value):
    iban = normalise_iban(value) or _normalise_identifier(value)
    if len(iban) < 8:
        return "****"
    return f"{iban[:4]}{'*' * max(4, len(iban) - 8)}{iban[-4:]}"


def supplier_identity(supplier):
    for identity_type in ("siret", "siren", "vat_id", "vat_number", "nif", "legal_id"):
        value = _normalise_identifier((supplier or {}).get(identity_type))
        if value:
            canonical_type = "vat_id" if identity_type == "vat_number" else identity_type
            return canonical_type, value
    return None, None


def observe_supplier_bank_account(invoice):
    supplier = invoice.get("supplier") or invoice.get("issuer") or {}
    identity_type, identity_value = supplier_identity(supplier)
    iban = normalise_iban(supplier.get("iban") or invoice.get("supplier_iban"))
    if not identity_value or not iban:
        return {"status": "IGNORED", "reason": "strong_supplier_identity_and_valid_iban_required"}

    con = connect()
    known = con.execute(
        """SELECT * FROM supplier_bank_accounts
           WHERE supplier_identity_type=? AND supplier_identity_value=? AND active=1 AND status='KNOWN'
           ORDER BY id DESC LIMIT 1""",
        (identity_type, identity_value),
    ).fetchone()
    existing = con.execute(
        """SELECT * FROM supplier_bank_accounts
           WHERE supplier_identity_type=? AND supplier_identity_value=? AND iban=?""",
        (identity_type, identity_value, iban),
    ).fetchone()
    source = (
        invoice.get("source_file"), invoice.get("invoice_number"), invoice.get("issue_date")
    )
    if known is None:
        if existing:
            con.execute(
                "UPDATE supplier_bank_accounts SET status='KNOWN',active=1,last_seen_at=CURRENT_TIMESTAMP WHERE id=?",
                (existing["id"],),
            )
            account_id = existing["id"]
        else:
            cursor = con.execute(
                """INSERT INTO supplier_bank_accounts(
                     supplier_identity_type,supplier_identity_value,supplier_name,iban,bic,status,active,
                     source_file,source_invoice_number,source_issue_date)
                   VALUES(?,?,?,?,?,'KNOWN',1,?,?,?)""",
                (identity_type, identity_value, supplier.get("name"), iban, supplier.get("bic"), *source),
            )
            account_id = cursor.lastrowid
        con.commit()
        con.close()
        return {"status": "KNOWN", "account_id": account_id, "first_seen": True}

    if known["iban"] == iban:
        con.execute("UPDATE supplier_bank_accounts SET last_seen_at=CURRENT_TIMESTAMP WHERE id=?", (known["id"],))
        con.commit()
        con.close()
        return {"status": "KNOWN", "account_id": known["id"], "first_seen": False}

    if existing:
        pending_id = existing["id"]
        con.execute("UPDATE supplier_bank_accounts SET last_seen_at=CURRENT_TIMESTAMP WHERE id=?", (pending_id,))
    else:
        cursor = con.execute(
            """INSERT INTO supplier_bank_accounts(
                 supplier_identity_type,supplier_identity_value,supplier_name,iban,bic,status,active,
                 source_file,source_invoice_number,source_issue_date)
               VALUES(?,?,?,?,?,'PENDING',0,?,?,?)""",
            (identity_type, identity_value, supplier.get("name"), iban, supplier.get("bic"), *source),
        )
        pending_id = cursor.lastrowid
    con.commit()
    result = {
        "status": "RIB_CHANGED",
        "pending_account_id": pending_id,
        "supplier": supplier.get("name"),
        "old_iban_masked": mask_iban(known["iban"]),
        "new_iban_masked": mask_iban(iban),
        "previous_source_file": known["source_file"],
        "previous_invoice_number": known["source_invoice_number"],
        "previous_issue_date": known["source_issue_date"],
        "new_source_file": invoice.get("source_file"),
        "new_invoice_number": invoice.get("invoice_number"),
        "new_issue_date": invoice.get("issue_date"),
    }
    con.close()
    return result


def accept_supplier_bank_account(account_id, username):
    con = connect()
    pending = con.execute("SELECT * FROM supplier_bank_accounts WHERE id=? AND status='PENDING'", (account_id,)).fetchone()
    if not pending:
        con.close()
        raise ValueError("Coordonnée bancaire en attente introuvable")
    con.execute(
        """UPDATE supplier_bank_accounts SET active=0
           WHERE supplier_identity_type=? AND supplier_identity_value=? AND active=1""",
        (pending["supplier_identity_type"], pending["supplier_identity_value"]),
    )
    con.execute(
        """UPDATE supplier_bank_accounts
           SET status='KNOWN',active=1,accepted_at=CURRENT_TIMESTAMP,accepted_by=? WHERE id=?""",
        (username, account_id),
    )
    con.commit()
    con.close()


def reject_supplier_bank_account(account_id, username):
    con = connect()
    pending = con.execute(
        "SELECT * FROM supplier_bank_accounts WHERE id=? AND status='PENDING'", (account_id,),
    ).fetchone()
    if not pending:
        con.close()
        raise ValueError("Coordonnée bancaire en attente introuvable")
    con.execute(
        """UPDATE supplier_bank_accounts SET status='REJECTED',active=0,
           accepted_at=CURRENT_TIMESTAMP,accepted_by=? WHERE id=?""",
        (username, account_id),
    )
    con.commit()
    con.close()


def list_supplier_bank_accounts():
    con = connect()
    rows = [dict(row) for row in con.execute("SELECT * FROM supplier_bank_accounts ORDER BY id DESC")]
    con.close()
    for row in rows:
        row["iban_masked"] = mask_iban(row["iban"])
        row.pop("iban", None)
    return rows
