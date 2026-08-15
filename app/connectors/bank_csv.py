import csv
from app.db import connect

def import_bank_csv(path):
    rows=[]
    with open(path,encoding="utf-8-sig",newline="") as f:
        for r in csv.DictReader(f):
            amt=float(str(r.get("amount") or r.get("montant") or "0").replace(",","."))
            row={"booking_date":r.get("booking_date") or r.get("date"),"label":r.get("label") or r.get("libelle"),
                 "amount":amt,"currency":r.get("currency") or r.get("devise") or "EUR","reference":r.get("reference") or ""}
            rows.append(row)
    con=connect()
    for r in rows:con.execute("INSERT INTO bank_transactions(booking_date,label,amount,currency,reference) VALUES(?,?,?,?,?)",(r["booking_date"],r["label"],r["amount"],r["currency"],r["reference"]))
    con.commit();con.close();return rows

def propose_matches(tolerance=.02):
    con=connect();tx=[dict(r) for r in con.execute("SELECT * FROM bank_transactions WHERE matched_invoice_id IS NULL")]
    inv=[dict(r) for r in con.execute("SELECT * FROM invoices WHERE status IN ('APPROVED','VALIDATED')")]
    con.close();out=[]
    for t in tx:
        for i in inv:
            if abs(abs(float(t["amount"]))-float(i["gross_amount"] or 0))<=tolerance:
                conf=.82
                if i["invoice_number"] and i["invoice_number"] in (t["reference"] or ""):conf=.97
                out.append({"transaction_id":t["id"],"invoice_id":i["id"],"invoice_number":i["invoice_number"],"amount":t["amount"],"confidence":conf})
    return out
