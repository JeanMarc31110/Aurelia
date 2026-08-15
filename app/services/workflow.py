import json
from app.db import connect
from app.services.learning import learn

def approve_invoice(invoice_id,username,account=None):
    con=connect();row=con.execute("SELECT * FROM invoices WHERE id=?",(invoice_id,)).fetchone()
    if not row:con.close();raise ValueError("Facture introuvable")
    inv=json.loads(row["raw_json"]);acct=account or row["proposed_account"]
    con.execute("UPDATE invoices SET status='APPROVED',approved_account=?,approved_by=?,approved_at=CURRENT_TIMESTAMP WHERE id=?",(acct,username,invoice_id))
    con.execute("UPDATE reviews SET resolved=1,resolution='APPROVED',resolved_by=?,resolved_at=CURRENT_TIMESTAMP WHERE invoice_id=? AND resolved=0",(username,invoice_id))
    con.execute("INSERT INTO audit_events(invoice_id,username,agent,event,details) VALUES(?,?,?,?,?)",(invoice_id,username,"WORKFLOW","approved",json.dumps({"account":acct})))
    con.commit();con.close();learn(inv,acct)
    return {"status":"APPROVED","account":acct}

def reject_invoice(invoice_id,username,reason):
    con=connect()
    con.execute("UPDATE invoices SET status='REJECTED',rejected_by=?,rejected_at=CURRENT_TIMESTAMP,rejection_reason=? WHERE id=?",(username,reason,invoice_id))
    con.execute("UPDATE reviews SET resolved=1,resolution='REJECTED',resolved_by=?,resolved_at=CURRENT_TIMESTAMP WHERE invoice_id=? AND resolved=0",(username,invoice_id))
    con.commit();con.close();return {"status":"REJECTED"}
