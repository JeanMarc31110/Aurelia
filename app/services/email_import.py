import hashlib
import json
from email import policy
from email.parser import BytesParser
from pathlib import Path

from app.db import connect
from app.local_config import load_local_config
from app.services.ingestion import ingest_file
from app.services.orchestrator import process_invoice


SUPPORTED_ATTACHMENTS = {".pdf", ".xml", ".ubl", ".cii"}


def import_eml(path, username="system", destination=None):
    message = BytesParser(policy=policy.default).parsebytes(Path(path).read_bytes())
    metadata = {
        "source_type": "email",
        "source_message_id": str(message.get("Message-ID") or ""),
        "source_sender": str(message.get("From") or ""),
        "source_recipient": str(message.get("To") or ""),
        "source_subject": str(message.get("Subject") or ""),
        "source_date": str(message.get("Date") or ""),
    }
    destination = Path(destination or load_local_config().email_attachments_dir)
    destination.mkdir(parents=True, exist_ok=True)
    results = []
    for part in message.iter_attachments():
        filename = Path(part.get_filename() or "attachment").name
        extension = Path(filename).suffix.lower()
        if extension not in SUPPORTED_ATTACHMENTS:
            continue
        payload = part.get_payload(decode=True) or b""
        content_hash = hashlib.sha256(payload).hexdigest()
        con = connect()
        existing = con.execute(
            "SELECT id,status,invoice_id FROM email_attachments WHERE content_sha256=?", (content_hash,)
        ).fetchone()
        con.close()
        if existing:
            results.append({"filename": filename, "status": "DUPLICATE_ATTACHMENT",
                            "attachment_id": existing["id"], "invoice_id": existing["invoice_id"]})
            continue

        stored_path = destination / f"{content_hash}{extension}"
        stored_path.write_bytes(payload)
        try:
            invoice = ingest_file(stored_path)
            invoice.update(metadata)
            invoice["source_attachment_name"] = filename
            decision = process_invoice(invoice, username)
            status = decision["status"]
            invoice_id = decision.get("invoice_id")
            details = {"invoice": invoice, "decision": decision}
        except Exception as exc:
            status = "ERROR"
            invoice_id = None
            details = {"error": str(exc)}
        con = connect()
        cursor = con.execute(
            """INSERT INTO email_attachments(
               content_sha256,filename,source_message_id,source_sender,source_subject,stored_path,status,invoice_id,details_json)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (content_hash, filename, metadata["source_message_id"], metadata["source_sender"],
             metadata["source_subject"], str(stored_path), status, invoice_id,
             json.dumps(details, ensure_ascii=False)),
        )
        con.commit()
        attachment_id = cursor.lastrowid
        con.close()
        results.append({"filename": filename, "status": status, "attachment_id": attachment_id,
                        "invoice_id": invoice_id, "invoice": details.get("invoice"),
                        "decision": details.get("decision")})
    return {"message": metadata, "attachments": results, "attachment_count": len(results)}
