from pypdf import PdfReader
import tempfile,re
from .xml_invoice import parse_xml_invoice
from app.services.ocr import ocr_pdf

def _parse_text(text):
    def first(p):
        m=re.search(p,text,re.I|re.M)
        return m.group(1).strip() if m else None
    def num(v):
        if not v:return 0.0
        s=re.sub(r"[^0-9,.\-]","",v.replace(" ",""))
        if s.count(",")==1 and s.count(".")==0:s=s.replace(",",".")
        elif s.count(",")==1 and s.count(".")>=1:s=s.replace(".","").replace(",",".")
        try:return float(s)
        except:return 0.0
    return {
      "invoice_number":first(r"(?:facture|invoice)\s*(?:n[°oº]?|no|#)?\s*[:\-]?\s*([A-Z0-9_./-]+)"),
      "gross_amount":num(first(r"(?:total\s*ttc|net\s*à\s*payer|amount\s*due)\s*:?\s*([0-9][0-9 .,'’]*)")),
      "raw_text":text[:30000]
    }

def parse_pdf(path):
    reader=PdfReader(str(path))
    attachments=getattr(reader,"attachments",{}) or {}
    for name,blobs in attachments.items():
        if name.lower().endswith(".xml"):
            blob=blobs[0] if isinstance(blobs,list) else blobs
            with tempfile.NamedTemporaryFile(suffix=".xml",delete=False) as f:
                f.write(blob); tmp=f.name
            data=parse_xml_invoice(tmp)
            data["format"]="FACTUR-X"; data["embedded_xml"]=name
            return data

    text="\n".join((p.extract_text() or "") for p in reader.pages)
    ocr_used=False
    ocr_error=None
    if not text.strip():
        r=ocr_pdf(path)
        if r["ok"]:
            text=r["text"]; ocr_used=True
        else:
            ocr_error=r.get("error")

    parsed=_parse_text(text)
    return {
      "format":"PDF_OCR" if ocr_used else ("PDF_TEXT" if text.strip() else "PDF_IMAGE"),
      "direction":"purchase",
      "supplier":{"name":""},"customer":{"name":""},"lines":[],
      "net_amount":0.0,"vat_amount":0.0,
      "gross_amount":parsed["gross_amount"],"invoice_number":parsed["invoice_number"],
      "currency":"EUR","raw_text":parsed["raw_text"],
      "needs_manual_extraction":not bool(text.strip()),
      "ocr_used":ocr_used,"ocr_error":ocr_error
    }
