from datetime import date
from app.db import connect
from app.connectors.gmail_oauth import create_draft

def overdue_outbound():
    con=connect()
    rows=[dict(r) for r in con.execute("""SELECT o.*,c.name customer_name,c.email customer_email
      FROM outbound_invoices o JOIN customers c ON c.id=o.customer_id
      WHERE o.status NOT IN ('PAID','CANCELLED') AND o.due_date < date('now')
      ORDER BY o.due_date""")]
    con.close();return rows

def make_reminder_draft(outbound_id):
    con=connect()
    row=con.execute("""SELECT o.*,c.name customer_name,c.email customer_email FROM outbound_invoices o
      JOIN customers c ON c.id=o.customer_id WHERE o.id=?""",(outbound_id,)).fetchone()
    con.close()
    if not row:raise ValueError("Facture client introuvable")
    r=dict(row)
    if not r["customer_email"]:raise ValueError("Adresse e-mail client manquante")
    subject=f"Rappel — facture {r['invoice_number']} arrivée à échéance"
    body=f"""Bonjour,

Sauf erreur de notre part, la facture {r['invoice_number']} d'un montant de {r['gross_amount']:.2f} EUR,
échue le {r['due_date']}, reste à ce jour en attente de règlement.

Merci de nous indiquer si le règlement a déjà été effectué ou, dans le cas contraire, sa date prévue.

Cordialement"""
    draft=create_draft(r["customer_email"],subject,body)
    con=connect();con.execute("""INSERT INTO reminders(outbound_invoice_id,customer_email,reminder_level,subject,body,gmail_draft_id)
      VALUES(?,?,?,?,?,?)""",(outbound_id,r["customer_email"],1,subject,body,draft.get("id")));con.commit();con.close()
    return {"draft_id":draft.get("id"),"subject":subject}
