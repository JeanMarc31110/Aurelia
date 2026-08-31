import csv
import hashlib
import json
import uuid
from pathlib import Path

from app.db import connect
from app.services.company import get_active_company
from app.services.settings import get_setting, set_setting


CONFIG_KEYS = (
    "journal_purchase", "journal_sale", "account_supplier", "account_customer",
    "account_vat_deductible", "account_vat_collected",
)


def _company_config_key(company_id, key):
    return f"accounting.company.{company_id}.{key}"


def accounting_config():
    company = get_active_company()
    if not company:
        return {key: get_setting(key, "") or "" for key in CONFIG_KEYS}
    scoped = {key: get_setting(_company_config_key(company["id"], key), "") or "" for key in CONFIG_KEYS}
    if not any(scoped.values()):
        legacy = {key: get_setting(key, "") or "" for key in CONFIG_KEYS}
        if any(legacy.values()):
            for key, value in legacy.items():set_setting(_company_config_key(company["id"], key), value)
            scoped = legacy
    scoped["company_name"] = company.get("legal_name") or f"Société {company['id']}"
    return scoped


def save_accounting_config(values):
    company = get_active_company()
    for key in CONFIG_KEYS:
        setting_key = _company_config_key(company["id"], key) if company else key
        set_setting(setting_key, (values.get(key) or "").strip())
    return accounting_config()


def _filters_sql(filters):
    clauses = ["i.status='APPROVED'"]
    params = []
    if filters.get("date_from"):clauses.append("i.issue_date>=?");params.append(filters["date_from"])
    if filters.get("date_to"):clauses.append("i.issue_date<=?");params.append(filters["date_to"])
    if filters.get("direction") in {"purchase", "sale"}:clauses.append("i.direction=?");params.append(filters["direction"])
    payment = filters.get("payment")
    if payment == "paid":clauses.append("i.payment_status='PAID'")
    elif payment == "unpaid":clauses.append("i.payment_status<>'PAID'")
    exported = filters.get("exported") or "not_exported"
    if exported == "not_exported":clauses.append("NOT EXISTS(SELECT 1 FROM accounting_export_items x WHERE x.invoice_id=i.id)")
    elif exported == "exported":clauses.append("EXISTS(SELECT 1 FROM accounting_export_items x WHERE x.invoice_id=i.id)")
    return " AND ".join(clauses), params


def _blocking_reasons(invoice, config):
    reasons = []
    for field, label in (("invoice_number", "Numéro manquant"), ("issue_date", "Date manquante"),
                         ("gross_amount", "TTC manquant"), ("net_amount", "HT manquant"),
                         ("account_final", "Compte comptable final à compléter")):
        if invoice.get(field) in {None, ""}:reasons.append(label)
    direction = invoice.get("direction")
    if direction not in {"purchase", "sale"}:reasons.append("Direction achat/vente à confirmer")
    if direction == "purchase":
        if not invoice.get("supplier_name"):reasons.append("Fournisseur manquant")
        for key, label in (("journal_purchase", "Journal achats non configuré"),
                           ("account_supplier", "Compte fournisseur non configuré")):
            if not config.get(key):reasons.append(label)
        if invoice.get("vat_amount") and not config.get("account_vat_deductible"):reasons.append("Compte TVA déductible non configuré")
    elif direction == "sale":
        if not invoice.get("customer_name"):reasons.append("Client manquant")
        for key, label in (("journal_sale", "Journal ventes non configuré"),
                           ("account_customer", "Compte client non configuré")):
            if not config.get(key):reasons.append(label)
        if invoice.get("vat_amount") and not config.get("account_vat_collected"):reasons.append("Compte TVA collectée non configuré")
    return reasons


def export_preview(filters=None):
    filters = filters or {}
    config = accounting_config();where,params = _filters_sql(filters)
    con = connect();rows = [dict(row) for row in con.execute(
        f"""SELECT i.*,EXISTS(SELECT 1 FROM accounting_export_items x WHERE x.invoice_id=i.id) already_exported
            FROM invoices i WHERE {where} ORDER BY i.issue_date,i.id""", params,
    )];history = [dict(row) for row in con.execute("SELECT * FROM accounting_exports ORDER BY id DESC LIMIT 30")];con.close()
    eligible,blocked = [],[]
    for row in rows:
        reasons = _blocking_reasons(row, config)
        if reasons:row["blocking_reasons"] = reasons;blocked.append(row)
        else:eligible.append(row)
    return {"eligible": eligible, "blocked": blocked, "history": history, "config": config, "filters": filters}


def _entries(invoice, config):
    piece = invoice["invoice_number"];label = invoice["supplier_name"] if invoice["direction"] == "purchase" else invoice["customer_name"]
    if invoice["direction"] == "purchase":
        journal = config["journal_purchase"]
        rows = [[invoice["issue_date"],journal,invoice["account_final"],label,invoice["net_amount"],0,piece]]
        if invoice["vat_amount"]:rows.append([invoice["issue_date"],journal,config["account_vat_deductible"],"TVA déductible",invoice["vat_amount"],0,piece])
        rows.append([invoice["issue_date"],journal,config["account_supplier"],label,0,invoice["gross_amount"],piece])
    else:
        journal = config["journal_sale"]
        rows = [[invoice["issue_date"],journal,config["account_customer"],label,invoice["gross_amount"],0,piece],
                [invoice["issue_date"],journal,invoice["account_final"],label,0,invoice["net_amount"],piece]]
        if invoice["vat_amount"]:rows.append([invoice["issue_date"],journal,config["account_vat_collected"],"TVA collectée",0,invoice["vat_amount"],piece])
    debit = round(sum(float(row[4] or 0) for row in rows),2);credit = round(sum(float(row[5] or 0) for row in rows),2)
    if abs(debit-credit) > .02:raise ValueError(f"Écriture déséquilibrée pour {piece}")
    return rows


def create_accounting_export(path, filters, username):
    preview = export_preview(filters)
    invoices = preview["eligible"]
    if not invoices:raise ValueError("Aucune facture complète et autorisée à exporter")
    config = preview["config"];path = Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    batch_id = "EXP-" + uuid.uuid4().hex[:12].upper()
    final_path = path.with_name(f"{path.stem}_{batch_id}{path.suffix}")
    with final_path.open("w",encoding="utf-8-sig",newline="") as handle:
        writer=csv.writer(handle,delimiter=";");writer.writerow(["Date","Journal","Compte","Libelle","Debit","Credit","Piece"])
        for invoice in invoices:writer.writerows(_entries(invoice,config))
    file_hash=hashlib.sha256(final_path.read_bytes()).hexdigest()
    con=connect();cursor=con.execute(
        """INSERT INTO accounting_exports(batch_id,file_name,file_hash,filters_json,invoice_count,username)
           VALUES(?,?,?,?,?,?)""",(batch_id,final_path.name,file_hash,json.dumps(filters,ensure_ascii=False),len(invoices),username))
    export_id=cursor.lastrowid
    for invoice in invoices:
        is_reexport=int(bool(invoice["already_exported"]))
        con.execute("INSERT INTO accounting_export_items(export_id,invoice_id,is_reexport) VALUES(?,?,?)",(export_id,invoice["id"],is_reexport))
        con.execute("UPDATE invoices SET last_exported_at=CURRENT_TIMESTAMP WHERE id=?",(invoice["id"],))
        con.execute("""INSERT INTO audit_events(invoice_id,username,agent,event,details,field_name,old_value,new_value)
          VALUES(?,?, 'ACCOUNTING','accounting_exported',?,'last_exported_at',?,CURRENT_TIMESTAMP)""",
          (invoice["id"],username,json.dumps({"batch_id":batch_id,"file_hash":file_hash,"reexport":bool(is_reexport)}),invoice.get("last_exported_at")))
    con.commit();con.close()
    return {"batch_id":batch_id,"path":str(final_path),"file_hash":file_hash,"invoice_count":len(invoices),"export_id":export_id}
