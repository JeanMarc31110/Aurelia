import os, re
from pathlib import Path

def ocr_pdf(path):
    try:
        import pytesseract, pypdfium2 as pdfium
        from PIL import Image
    except Exception as e:
        return {"ok":False,"text":"","error":f"Dépendances OCR indisponibles: {e}"}

    cmd=os.getenv("TESSERACT_CMD","").strip()
    if cmd:
        pytesseract.pytesseract.tesseract_cmd=cmd

    lang=os.getenv("OCR_LANG","fra+eng")
    try:
        doc=pdfium.PdfDocument(str(path))
        texts=[]
        for idx in range(len(doc)):
            page=doc[idx]
            bitmap=page.render(scale=2.2)
            pil=bitmap.to_pil()
            texts.append(pytesseract.image_to_string(pil,lang=lang))
        return {"ok":True,"text":"\n".join(texts)}
    except Exception as e:
        return {"ok":False,"text":"","error":str(e)}
