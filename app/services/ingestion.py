from pathlib import Path
from app.parsers.pdf_parser import parse_pdf
from app.parsers.xml_invoice import parse_xml_invoice

def ingest_file(path):
    p=Path(path); ext=p.suffix.lower()
    if ext==".pdf":data=parse_pdf(p)
    elif ext in {".xml",".ubl",".cii"}:data=parse_xml_invoice(p)
    else:raise ValueError(f"Format non supporté: {ext}")
    data["source_file"]=p.name
    data["source_path"]=str(p)
    return data
