import os
import sqlite3
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from app import db
from app.db import connect, init_db
from app.local_config import ensure_local_directories, load_local_config
from app.services.folder_watcher import FolderWatcher
from app.services.local_ingestion import LocalIngestionService, sha256_file, wait_for_stable_file
from app.services.company import save_active_company
from app.services.sqlite_backups import BackupScheduler, create_sqlite_backup


class FakeImporter:
    def __init__(self):
        self.calls = []
        self.lock = threading.Lock()

    def __call__(self, path, username="system", stored_path=None):
        with self.lock:self.calls.append(Path(path).name)
        if Path(path).name.startswith("bad"):
            raise ValueError("document synthétique invalide")
        return {"document": {"source_path": str(stored_path)},
                "decision": {"status": "REVIEW_REQUIRED"}}


class Phase4LocalRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.environment = patch.dict(os.environ, {
            "AURELIA_DATA_DIR": str(self.root / "local-data"),
            "AURELIA_DOCUMENTS_DIR": str(self.root / "documents"),
            "AURELIA_DB_PATH": str(self.root / "local-data" / "aurelia.db"),
            "AURELIA_WATCHER_POLL_SECONDS": "0.05",
            "AURELIA_WATCHER_STABLE_SECONDS": "0.05",
            "AURELIA_WATCHER_STABLE_CHECKS": "2",
            "AURELIA_BACKUP_RETENTION": "2",
        }, clear=False)
        self.environment.start()
        self.config = replace(load_local_config(self.root / "program"),
                              watcher_poll_seconds=.02, watcher_stable_seconds=.01,
                              backup_interval_seconds=.05)
        ensure_local_directories(self.config)
        self.db_patch = patch.object(db, "DB_PATH", self.config.sqlite_path)
        self.db_patch.start();init_db()

    def tearDown(self):
        self.db_patch.stop();self.environment.stop();self.temporary.cleanup()

    def write_inbox(self, name, content=b"synthetic invoice"):
        path = self.config.inbox_dir / name
        path.write_bytes(content)
        return path

    def test_configured_paths_and_directories_are_idempotent(self):
        ensure_local_directories(self.config);ensure_local_directories(self.config)
        self.assertEqual(self.config.data_dir, (self.root / "local-data").resolve())
        self.assertEqual(self.config.documents_dir, (self.root / "documents").resolve())
        for directory in (self.config.inbox_dir, self.config.processed_dir, self.config.errors_dir,
                          self.config.archive_dir, self.config.exports_dir, self.config.backups_dir,
                          self.config.logs_dir):
            self.assertTrue(directory.is_dir())

    def test_success_preserves_content_moves_file_and_persists_hash(self):
        importer = FakeImporter();service = LocalIngestionService(self.config, importer)
        source = self.write_inbox("invoice.pdf", b"exact original bytes")
        digest = sha256_file(source);result = service.process_file(source)
        destination = Path(result["path"])
        self.assertEqual(result["status"], "imported");self.assertFalse(source.exists())
        self.assertEqual(destination.parent, self.config.processed_dir)
        self.assertEqual(destination.read_bytes(), b"exact original bytes")
        connection=connect();row=connection.execute("SELECT * FROM ingested_files WHERE sha256=?",(digest,)).fetchone();connection.close()
        self.assertEqual(row["status"],"IMPORTED");self.assertEqual(row["stored_path"],str(destination))

    def test_watcher_service_reuses_real_ubl_pipeline(self):
        save_active_company({"legal_name": "INNOVATECH SOFTWARE E IA SL",
                             "nif": "B22714539", "country": "ES"})
        ubl = b'''<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
 xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
 xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
 <cbc:ID>PH4-UBL-001</cbc:ID><cbc:IssueDate>2026-08-27</cbc:IssueDate>
 <cbc:DocumentCurrencyCode>EUR</cbc:DocumentCurrencyCode>
 <cac:AccountingSupplierParty><cac:Party><cac:PartyName><cbc:Name>PHASE 4 TEST SUPPLIER</cbc:Name></cac:PartyName>
 <cac:PartyTaxScheme><cbc:CompanyID>FR11111111111</cbc:CompanyID></cac:PartyTaxScheme></cac:Party></cac:AccountingSupplierParty>
 <cac:AccountingCustomerParty><cac:Party><cac:PartyName><cbc:Name>INNOVATECH SOFTWARE E IA SL</cbc:Name></cac:PartyName>
 <cac:PartyTaxScheme><cbc:CompanyID>B22714539</cbc:CompanyID></cac:PartyTaxScheme></cac:Party></cac:AccountingCustomerParty>
 <cac:InvoiceLine><cbc:ID>1</cbc:ID><cbc:InvoicedQuantity>1</cbc:InvoicedQuantity><cbc:LineExtensionAmount>100.00</cbc:LineExtensionAmount>
 <cac:Item><cbc:Description>Service synthetique</cbc:Description></cac:Item><cac:Price><cbc:PriceAmount>100.00</cbc:PriceAmount></cac:Price></cac:InvoiceLine>
 <cac:TaxTotal><cbc:TaxAmount>20.00</cbc:TaxAmount></cac:TaxTotal>
 <cac:LegalMonetaryTotal><cbc:TaxExclusiveAmount>100.00</cbc:TaxExclusiveAmount><cbc:PayableAmount>120.00</cbc:PayableAmount></cac:LegalMonetaryTotal>
</Invoice>'''
        result = LocalIngestionService(self.config).process_file(
            self.write_inbox("phase4_invoice.ubl", ubl))
        self.assertEqual(result["status"], "imported")
        self.assertEqual(Path(result["path"]).read_bytes(), ubl)
        connection = connect()
        try:
            invoice = connection.execute(
                "SELECT invoice_number,supplier_name,gross_amount FROM invoices ORDER BY id DESC LIMIT 1"
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(invoice["invoice_number"], "PH4-UBL-001")
        self.assertEqual(invoice["supplier_name"], "PHASE 4 TEST SUPPLIER")
        self.assertEqual(invoice["gross_amount"], 120.0)

    def test_same_content_two_names_and_after_restart_is_not_reimported(self):
        importer = FakeImporter();first_service = LocalIngestionService(self.config, importer)
        first_service.process_file(self.write_inbox("invoice.pdf", b"same-content"))
        second_service = LocalIngestionService(self.config, importer)
        result = second_service.process_file(self.write_inbox("copy_invoice.pdf", b"same-content"))
        self.assertEqual(result["status"],"duplicate");self.assertEqual(len(importer.calls),1)
        self.assertTrue(Path(result["path"]).is_file());self.assertIn("duplicate-",Path(result["path"]).name)
        connection=connect();self.assertEqual(connection.execute("SELECT COUNT(*) FROM ingested_files").fetchone()[0],1)
        self.assertEqual(connection.execute("SELECT duplicate_count FROM ingested_files").fetchone()[0],1);connection.close()

    def test_concurrent_same_content_is_serialized(self):
        importer = FakeImporter();service = LocalIngestionService(self.config, importer)
        first=self.write_inbox("one.pdf",b"concurrent");second=self.write_inbox("two.pdf",b"concurrent")
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(service.process_file,(first,second)))
        self.assertEqual(sorted(row["status"] for row in results),["duplicate","imported"])
        self.assertEqual(len(importer.calls),1)

    def test_stability_waits_until_writer_has_finished(self):
        source=self.write_inbox("copying.pdf",b"part-1")
        def finish_copy():
            time.sleep(.035);source.write_bytes(b"part-1-and-final-part")
        writer=threading.Thread(target=finish_copy);writer.start();started=time.monotonic()
        wait_for_stable_file(source,checks=2,interval=.025,max_wait=2);writer.join()
        self.assertGreaterEqual(time.monotonic()-started,.07)
        self.assertEqual(source.read_bytes(),b"part-1-and-final-part")

    def test_unsupported_and_failed_files_move_to_errors_without_loss(self):
        importer=FakeImporter();service=LocalIngestionService(self.config,importer)
        unsupported=self.write_inbox("notes.txt",b"unsupported bytes")
        bad=self.write_inbox("bad.pdf",b"invalid pdf bytes")
        unsupported_result=service.process_file(unsupported);bad_result=service.process_file(bad)
        self.assertEqual(unsupported_result["status"],"unsupported")
        self.assertEqual(bad_result["status"],"error")
        self.assertEqual(Path(unsupported_result["path"]).read_bytes(),b"unsupported bytes")
        self.assertEqual(Path(bad_result["path"]).read_bytes(),b"invalid pdf bytes")

    def test_one_error_does_not_stop_following_file(self):
        importer=FakeImporter();watcher=FolderWatcher(self.config,LocalIngestionService(self.config,importer))
        self.write_inbox("bad-first.pdf",b"bad");self.write_inbox("good-second.pdf",b"good")
        results=watcher.scan_once()
        self.assertEqual([row["status"] for row in results],["error","imported"])
        self.assertEqual(len(list(self.config.errors_dir.iterdir())),1)
        self.assertEqual(len(list(self.config.processed_dir.iterdir())),1)

    def test_existing_file_is_processed_when_watcher_starts_and_stops_cleanly(self):
        importer=FakeImporter();self.write_inbox("already-there.pdf",b"before startup")
        watcher=FolderWatcher(self.config,LocalIngestionService(self.config,importer))
        self.assertTrue(watcher.start())
        deadline=time.monotonic()+3
        while time.monotonic()<deadline and not list(self.config.processed_dir.iterdir()):time.sleep(.02)
        self.assertTrue(watcher.running);self.assertTrue(watcher.stop());self.assertFalse(watcher.running)
        self.assertEqual(importer.calls,["already-there.pdf"])

    def test_sqlite_backup_is_integral_and_retention_keeps_latest_two(self):
        connection=connect();connection.execute("INSERT INTO settings(key,value) VALUES('phase4','ok')");connection.commit();connection.close()
        backups=[]
        for index in range(3):
            backups.append(create_sqlite_backup(self.config.backups_dir,self.config.sqlite_path,f"test-{index}",keep_last=2))
        existing=sorted(self.config.backups_dir.glob("aurelia_*.db"))
        self.assertEqual(len(existing),2);self.assertFalse(backups[0].exists())
        check=sqlite3.connect(existing[-1]);self.assertEqual(check.execute("PRAGMA integrity_check").fetchone()[0],"ok")
        self.assertEqual(check.execute("SELECT value FROM settings WHERE key='phase4'").fetchone()[0],"ok");check.close()

    def test_backup_scheduler_lifecycle_has_no_orphan_thread(self):
        scheduler=BackupScheduler(self.config,self.config.sqlite_path)
        self.assertTrue(scheduler.start())
        deadline=time.monotonic()+2
        while time.monotonic()<deadline and not list(self.config.backups_dir.glob("aurelia_*.db")):time.sleep(.02)
        self.assertTrue(scheduler.running);self.assertTrue(scheduler.stop());self.assertFalse(scheduler.running)


if __name__ == "__main__":unittest.main()
