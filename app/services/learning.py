from app.db import connect

def suggest(invoice):
    supplier=(invoice.get("supplier") or {}).get("name") or ""
    con=connect()
    rows=[dict(r) for r in con.execute("SELECT * FROM learned_mappings WHERE supplier_name=? ORDER BY validations DESC",(supplier,))]
    con.close()
    if rows:
        r=rows[0];return {"account":r["account"],"confidence":min(.99,.80+.03*int(r["validations"])),"source":"learning"}
    return None

def learn(invoice,account):
    supplier=(invoice.get("supplier") or {}).get("name") or ""
    if not supplier or not account:return
    con=connect()
    row=con.execute("SELECT * FROM learned_mappings WHERE supplier_name=? AND account=?",(supplier,account)).fetchone()
    if row:con.execute("UPDATE learned_mappings SET validations=validations+1,updated_at=CURRENT_TIMESTAMP WHERE id=?",(row["id"],))
    else:con.execute("INSERT INTO learned_mappings(supplier_name,keyword,account) VALUES(?,?,?)",(supplier,"",account))
    con.commit();con.close()
