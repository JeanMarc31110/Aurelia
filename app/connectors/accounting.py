import os,requests,csv
from pathlib import Path
from app.db import connect

class GenericRESTConnector:
    def __init__(self,base_url,token=None,headers=None):
        self.base_url=(base_url or "").rstrip("/")
        self.token=token
        self.headers=headers or {}
        if token:self.headers.setdefault("Authorization",f"Bearer {token}")
    def configured(self):return bool(self.base_url)
    def post(self,path,payload):
        if not self.configured():return {"ok":False,"error":"Connecteur non configuré"}
        r=requests.post(self.base_url+"/"+path.lstrip("/"),json=payload,headers=self.headers,timeout=30)
        return {"ok":r.ok,"status_code":r.status_code,"body":r.text[:2000]}

class PennylaneConnector(GenericRESTConnector):
    @classmethod
    def from_env(cls):return cls(os.getenv("PENNYLANE_BASE_URL"),os.getenv("PENNYLANE_API_TOKEN"))

class SageConnector(GenericRESTConnector):
    @classmethod
    def from_env(cls):return cls(os.getenv("SAGE_BASE_URL"),os.getenv("SAGE_ACCESS_TOKEN"))

class CegidConnector(GenericRESTConnector):
    @classmethod
    def from_env(cls):
        headers={}
        if os.getenv("CEGID_API_KEY"):headers["x-apikey"]=os.getenv("CEGID_API_KEY")
        if os.getenv("CEGID_SUBSCRIPTION_KEY"):headers["Ocp-Apim-Subscription-Key"]=os.getenv("CEGID_SUBSCRIPTION_KEY")
        return cls(os.getenv("CEGID_BASE_URL"),headers=headers)

def export_ebp_csv(path):
    con=connect();rows=[dict(r) for r in con.execute("SELECT * FROM invoices WHERE status='APPROVED' ORDER BY issue_date,id")];con.close()
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.writer(f,delimiter=";");w.writerow(["Date","Journal","Compte","Libelle","Debit","Credit","Piece"])
        for i in rows:
            acc=i["approved_account"] or i["proposed_account"] or "A_VERIFIER"
            w.writerow([i["issue_date"],"ACH",acc,i["supplier_name"],i["net_amount"],0,i["invoice_number"]])
            w.writerow([i["issue_date"],"ACH","44566","TVA déductible",i["vat_amount"],0,i["invoice_number"]])
            w.writerow([i["issue_date"],"ACH","401",i["supplier_name"],0,i["gross_amount"],i["invoice_number"]])
    return str(path)
