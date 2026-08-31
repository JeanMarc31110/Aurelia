import hashlib
import logging
import shutil
import threading
import time
from pathlib import Path

from app.db import connect
from app.local_config import ensure_local_directories, load_local_config
from app.services.document_import import SUPPORTED_DOCUMENT_EXTENSIONS, import_document


logger = logging.getLogger("aurelia.ingestion")


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def unique_destination(directory, filename, tag=None):
    directory = Path(directory);directory.mkdir(parents=True, exist_ok=True)
    safe_name = Path(filename).name or "document"
    source = Path(safe_name)
    stem = source.stem
    if tag:stem = f"{stem}.{tag}"
    candidate = directory / f"{stem}{source.suffix}"
    counter = 1
    while candidate.exists():
        candidate = directory / f"{stem}.{counter}{source.suffix}"
        counter += 1
    return candidate


def wait_for_stable_file(path, checks=3, interval=1.0, stop_event=None, max_wait=120.0):
    path = Path(path)
    stable = 0
    previous = None
    deadline = time.monotonic() + max_wait
    while stable < checks:
        if stop_event and stop_event.is_set():raise InterruptedError("Arrêt du watcher demandé")
        if time.monotonic() >= deadline:raise TimeoutError("Le fichier n'est pas devenu stable")
        stat = path.stat()
        snapshot = (stat.st_size, stat.st_mtime_ns)
        stable = stable + 1 if snapshot == previous else 0
        previous = snapshot
        if stable < checks:
            if stop_event:stop_event.wait(interval)
            else:time.sleep(interval)
    with path.open("rb") as handle:
        handle.read(1)
    return True


def _move_and_verify(source, destination, expected_hash):
    source = Path(source);destination = Path(destination)
    shutil.move(str(source), str(destination))
    if sha256_file(destination) != expected_hash:
        raise IOError("Le contenu du fichier déplacé ne correspond pas à l'original")
    return destination


class LocalIngestionService:
    def __init__(self, config=None, importer=import_document):
        self.config = ensure_local_directories(config or load_local_config())
        self.importer = importer
        self._worker_lock = threading.Lock()

    def _claim(self, digest, filename):
        connection = connect();connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT * FROM ingested_files WHERE sha256=?", (digest,)).fetchone()
        if row and row["status"] in {"IMPORTED", "UNSUPPORTED"}:
            connection.commit();connection.close();return dict(row), False
        if row:
            connection.execute(
                """UPDATE ingested_files SET original_filename=?,status='PROCESSING',error_type=NULL,
                   error_message=NULL,updated_at=CURRENT_TIMESTAMP WHERE sha256=?""",
                (filename, digest),
            )
        else:
            connection.execute(
                "INSERT INTO ingested_files(sha256,original_filename,status) VALUES(?,?,'PROCESSING')",
                (digest, filename),
            )
        connection.commit();connection.close();return None, True

    def _mark_duplicate(self, digest, destination):
        connection = connect()
        connection.execute(
            """UPDATE ingested_files SET duplicate_count=duplicate_count+1,last_duplicate_path=?,
               updated_at=CURRENT_TIMESTAMP WHERE sha256=?""", (str(destination), digest),
        )
        connection.commit();connection.close()

    def _mark_result(self, digest, status, destination, decision=None, error=None):
        decision = decision or {}
        connection = connect()
        connection.execute(
            """UPDATE ingested_files SET status=?,stored_path=?,linked_invoice_id=?,linked_document_id=?,
               error_type=?,error_message=?,processed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP
               WHERE sha256=?""",
            (status, str(destination) if destination else None, decision.get("invoice_id"),
             decision.get("document_id"), type(error).__name__ if error else None,
             str(error)[:1000] if error else None, digest),
        )
        connection.commit();connection.close()

    def process_file(self, path, username="folder_watcher", stop_event=None):
        path = Path(path)
        with self._worker_lock:
            if not path.exists():return {"status": "missing", "path": str(path)}
            wait_for_stable_file(
                path, self.config.watcher_stable_checks, self.config.watcher_stable_seconds, stop_event,
            )
            digest = sha256_file(path)
            existing, should_process = self._claim(digest, path.name)
            if not should_process:
                target_dir = self.config.processed_dir if existing["status"] == "IMPORTED" else self.config.errors_dir
                destination = unique_destination(target_dir, path.name, f"duplicate-{digest[:8]}")
                _move_and_verify(path, destination, digest)
                self._mark_duplicate(digest, destination)
                logger.info("duplicate_file filename=%s status=%s", path.name, existing["status"])
                return {"status": "duplicate", "sha256": digest, "path": str(destination)}

            if path.suffix.lower() not in SUPPORTED_DOCUMENT_EXTENSIONS:
                destination = unique_destination(self.config.errors_dir, path.name, "unsupported")
                _move_and_verify(path, destination, digest)
                error = ValueError(f"unsupported_file_type: {path.suffix.lower() or 'none'}")
                self._mark_result(digest, "UNSUPPORTED", destination, error=error)
                logger.warning("unsupported_file filename=%s", path.name)
                return {"status": "unsupported", "sha256": digest, "path": str(destination), "error": str(error)}

            destination = unique_destination(self.config.processed_dir, path.name)
            try:
                result = self.importer(path, username=username, stored_path=destination)
                moved = _move_and_verify(path, destination, digest)
                decision = result.get("decision") or {}
                self._mark_result(digest, "IMPORTED", moved, decision=decision)
                logger.info("file_imported filename=%s", path.name)
                return {"status": "imported", "sha256": digest, "path": str(moved),
                        "decision": decision}
            except Exception as exc:
                moved = None
                try:
                    if path.exists():
                        moved = _move_and_verify(
                            path, unique_destination(self.config.errors_dir, path.name, "error"), digest,
                        )
                except Exception as move_exc:
                    logger.error("error_file_move_failed filename=%s error=%s", path.name, type(move_exc).__name__)
                self._mark_result(digest, "ERROR", moved, error=exc)
                logger.error("file_import_failed filename=%s error=%s message=%s",
                             path.name, type(exc).__name__, str(exc)[:300])
                return {"status": "error", "sha256": digest,
                        "path": str(moved or path), "error": str(exc)}
