from datetime import date,timedelta
from pathlib import Path
from xml.etree.ElementTree import Element,SubElement,ElementTree,register_namespace
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from app.db import connect

BASE=Path(__file__).resolve().parents[2]
GEN=BASE/"data"/"generated"; GEN.mkdir(parents=True,exist_ok=True)

def next_number():
    year=date.today().year;name=f"SALES-{year}"
    con=connect();row=con.execute("SELECT current_value FROM sequences WHERE name=?",(name,)).fetchone()
    n=(row["current_value"]+1) if row else 1
    if row:con.execute("UPDATE sequences SET current_value=? WHERE name=?",(n,name))
    else:con.execute("INSERT INTO sequences(name,current_value) VALUES(?,?)",(name,n))
    con.commit();con.close()
    return f"F{year}-{n:05d}"

def generate_pdf(number,customer,issue,due,description,net,vat,gross):
    path=GEN/f"{number}.pdf"
    c=canvas.Canvas(str(path),pagesize=A4); w,h=A4
    c.setFont("Helvetica-Bold",18);c.drawString(50,h-60,"FACTURE")
    c.setFont("Helvetica",10)
    y=h-95
    for label,val in [("Numéro",number),("Date",issue),("Échéance",due),("Client",customer["name"])]:
        c.drawString(50,y,f"{label} : {val}");y-=18
    y-=20;c.drawString(50,y,description);y-=35
    c.drawRightString(w-60,y,f"HT : {net:.2f} EUR");y-=18
    c.drawRightString(w-60,y,f"TVA : {vat:.2f} EUR");y-=18
    c.setFont("Helvetica-Bold",11);c.drawRightString(w-60,y,f"TTC : {gross:.2f} EUR")
    c.save();return str(path)

def generate_ubl(number,customer,issue,due,description,net,vat,gross):
    ns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
    root=Element("Invoice"); SubElement(root,"ID").text=number;SubElement(root,"IssueDate").text=issue
    SubElement(root,"DueDate").text=due;SubElement(root,"DocumentCurrencyCode").text="EUR"
    SubElement(root,"BuyerName").text=customer["name"];SubElement(root,"Description").text=description
    SubElement(root,"TaxExclusiveAmount").text=f"{net:.2f}";SubElement(root,"TaxAmount").text=f"{vat:.2f}"
    SubElement(root,"PayableAmount").text=f"{gross:.2f}"
    path=GEN/f"{number}.ubl.xml";ElementTree(root).write(path,encoding="utf-8",xml_declaration=True)
    return str(path)

def create_outbound(customer_id,description,net_amount,vat_rate=20.0,due_days=30):
    con=connect();customer=con.execute("SELECT * FROM customers WHERE id=?",(customer_id,)).fetchone()
    if not customer:con.close();raise ValueError("Client introuvable")
    customer=dict(customer);number=next_number();issue=date.today().isoformat();due=(date.today()+timedelta(days=due_days)).isoformat()
    net=float(net_amount);vat=round(net*float(vat_rate)/100,2);gross=round(net+vat,2)
    pdf=generate_pdf(number,customer,issue,due,description,net,vat,gross)
    xml=generate_ubl(number,customer,issue,due,description,net,vat,gross)
    cur=con.execute("""INSERT INTO outbound_invoices(invoice_number,customer_id,issue_date,due_date,net_amount,vat_amount,gross_amount,
    description,pdf_path,xml_path) VALUES(?,?,?,?,?,?,?,?,?,?)""",(number,customer_id,issue,due,net,vat,gross,description,pdf,xml))
    oid=cur.lastrowid;con.commit();con.close()
    return {"id":oid,"invoice_number":number,"pdf_path":pdf,"xml_path":xml,"gross_amount":gross}
