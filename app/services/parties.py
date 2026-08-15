from app.db import connect

def ensure_supplier(name,siren=None,vat_number=None,email=None,iban=None):
    if not name:return None
    con=connect()
    row=con.execute("SELECT id FROM suppliers WHERE name=?",(name,)).fetchone()
    if row:
        con.execute("""UPDATE suppliers SET siren=COALESCE(?,siren),vat_number=COALESCE(?,vat_number),
                       email=COALESCE(?,email),updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                    (siren,vat_number,email,row["id"]))
        sid=row["id"]
    else:
        cur=con.execute("INSERT INTO suppliers(name,siren,vat_number,email,iban) VALUES(?,?,?,?,?)",
                        (name,siren,vat_number,email,iban)); sid=cur.lastrowid
    con.commit();con.close();return sid

def ensure_customer(name,siren=None,vat_number=None,email=None,address=None):
    if not name:return None
    con=connect()
    row=con.execute("SELECT id FROM customers WHERE name=?",(name,)).fetchone()
    if row:
        con.execute("""UPDATE customers SET siren=COALESCE(?,siren),vat_number=COALESCE(?,vat_number),
                       email=COALESCE(?,email),address=COALESCE(?,address),updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                    (siren,vat_number,email,address,row["id"])); cid=row["id"]
    else:
        cur=con.execute("INSERT INTO customers(name,siren,vat_number,email,address) VALUES(?,?,?,?,?)",
                        (name,siren,vat_number,email,address)); cid=cur.lastrowid
    con.commit();con.close();return cid
