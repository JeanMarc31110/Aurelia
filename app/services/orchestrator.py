import hashlib,json
from pathlib import Path
from app.db import connect
from app.services.learning import suggest
from app.services.parties import ensure_supplier,ensure_customer

ROOT=Path(__file__).resolve().parents[2]
POLICY=json.loads((ROOT/"config"/"policy.json").read_text(encoding="utf-8"))
MAPPING=json.loads((ROOT/"config"/"account_mapping.json").read_text(encoding="utf-8"))

def fingerprint(inv):
    key="|".join([
      ((inv.get("supplier") or {}).get("siren") or (inv.get("supplier") or {}).get("name") or "").upper(),
      str(inv.get("invoice_number","")).upper(),str(inv.get("issue_date","")),
      str(inv.get("gross_amount","")),str(inv.get("currency","EUR"))
    ])
    return hashlib.sha256(key.encode()).hexdigest()

def account_proposal(inv):
    learned=suggest(inv)
    if learned:return learned
    text=(" ".join((x.get("description") or "").lower() for x in inv.get("lines",[]))+" "+(inv.get("raw_text") or "").lower())
    for r in MAPPING:
        if any(k.lower() in text for k in r["keywords"]):
            return {"account":r["account"],"confidence":r["confidence"],"source":"rules"}
    return {"account":None,"confidence":.25,"source":"none"}

def process_invoice(inv,username="system"):
    ensure_supplier(**{k:(inv.get("supplier") or {}).get(k) for k in ["name","siren","vat_number","email","iban"]})
    ensure_customer(**{k:(inv.get("customer") or {}).get(k) for k in ["name","siren","vat_number","email","address"]})

    fp=fingerprint(inv); con=connect()
    dup=bool(con.execute("SELECT id FROM invoices WHERE fingerprint=?",(fp,)).fetchone());con.close()
    findings=[];risk=0
    def add(agent,code,severity,ok,msg,score=0):
        nonlocal risk
        findings.append({"agent":agent,"code":code,"severity":severity,"ok":ok,"message":msg})
        if not ok:risk+=score

    supplier=(inv.get("supplier") or {}).get("name")
    add("FOURNISSEURS","supplier","error",bool(supplier),"Fournisseur identifié" if supplier else "Fournisseur à compléter",20)
    add("FACTURATION","invoice_number","error",bool(inv.get("invoice_number")),"Numéro présent" if inv.get("invoice_number") else "Numéro à compléter",20)
    if inv.get("needs_manual_extraction"):add("OCR","manual","error",False,"Lecture automatique insuffisante",25)
    if inv.get("net_amount") or inv.get("vat_amount"):
        exp=round(float(inv.get("net_amount",0))+float(inv.get("vat_amount",0)),2);gross=round(float(inv.get("gross_amount",0)),2)
        add("TVA","totals","error",abs(exp-gross)<=POLICY["thresholds"]["rounding_tolerance"],f"HT+TVA={exp:.2f}; TTC={gross:.2f}",30)
    if dup:add("FRAUDE","duplicate","critical",False,"Doublon détecté",50)
    if inv.get("supplier_bank_details_changed"):add("TRESORERIE","bank_change","critical",False,"RIB modifié : blocage",50)
    if inv.get("vat_treatment")=="uncertain":add("TVA","vat","critical",False,"TVA incertaine",40)

    ap=account_proposal(inv)
    add("COMPTABILITE","account","warning",ap["confidence"]>=POLICY["thresholds"]["accounting_confidence"],f"Compte {ap['account']} — confiance {ap['confidence']:.0%}",10)

    status="DUPLICATE" if dup else ("REVIEW_REQUIRED" if any(not x["ok"] for x in findings) else "VALIDATED")
    result={"status":status,"risk_score":min(100,risk),"fingerprint":fp,"findings":findings,
            "accounting_proposal":ap,"payment_authorized":False,"human_review_required":status!="VALIDATED"}
    if not dup:
        con=connect();cur=con.cursor()
        cur.execute("""INSERT INTO invoices(fingerprint,source_file,source_path,format,direction,invoice_number,supplier_name,
        customer_name,issue_date,due_date,net_amount,vat_amount,gross_amount,currency,status,risk_score,proposed_account,
        accounting_confidence,raw_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (fp,inv.get("source_file"),inv.get("source_path"),inv.get("format"),inv.get("direction","purchase"),inv.get("invoice_number"),
         supplier,(inv.get("customer") or {}).get("name"),inv.get("issue_date"),inv.get("due_date"),inv.get("net_amount"),inv.get("vat_amount"),
         inv.get("gross_amount"),inv.get("currency","EUR"),status,result["risk_score"],ap.get("account"),ap.get("confidence"),json.dumps(inv,ensure_ascii=False)))
        iid=cur.lastrowid
        for x in findings:
            cur.execute("INSERT INTO audit_events(invoice_id,username,agent,event,details) VALUES(?,?,?,?,?)",(iid,username,x["agent"],x["code"],json.dumps(x,ensure_ascii=False)))
            if not x["ok"]:cur.execute("INSERT INTO reviews(invoice_id,reason,severity) VALUES(?,?,?)",(iid,x["message"],x["severity"]))
        con.commit();con.close();result["invoice_id"]=iid
    return result
