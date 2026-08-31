from pathlib import Path

from app.services.ingestion import ingest_file
from app.services.orchestrator import process_invoice


SUPPORTED_DOCUMENT_EXTENSIONS = {".pdf", ".xml", ".ubl", ".cii"}


def import_document(path, username="system", stored_path=None):
    path = Path(path)
    data = ingest_file(path)
    if stored_path is not None:
        stored_path = Path(stored_path)
        data["source_file"] = stored_path.name
        data["source_path"] = str(stored_path)
    decision = process_invoice(data, username)
    return {"document": data, "decision": decision}
