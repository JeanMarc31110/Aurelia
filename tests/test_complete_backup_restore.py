import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from app import db
from app.local_config import ensure_local_directories, load_local_config
from app.services.backup_restore import (
    BACKUP_FORMAT_VERSION,
    MaintenanceError,
    create_complete_backup,
    read_maintenance_status,
    restore_backup,
    validate_backup,
)
from app.services.diagnostics import create_diagnostic_bundle
from app.services.redaction import REDACTED, redact, redact_text


class CompleteBackupRestoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.environment = patch.dict(os.environ, {
            "AURELIA_RUNTIME_ROOT": str(self.root / "runtime"),
            "AURELIA_WATCHER_ENABLED": "0",
            "AURELIA_BACKUPS_ENABLED": "0",
            "AURELIA_COMPLETE_BACKUP_RETENTION": "2",
        }, clear=True)
        self.environment.start()
        self.config = ensure_local_directories(load_local_config(self.root / "program"))
        self.database_patch = patch.object(db, "DB_PATH", self.config.sqlite_path)
        self.database_patch.start()
        db.init_db()
        connection = db.connect()
        connection.execute(
            "INSERT INTO users(username,password_hash,role,active) VALUES('synthetic','hash','admin',1)"
        )
        connection.execute(
            "INSERT INTO companies(legal_name,currency,active) VALUES('Synthetic Company','EUR',1)"
        )
        connection.execute(
            "INSERT INTO invoices(invoice_number,status,raw_json) VALUES('SYN-001','REVIEW_REQUIRED','{}')"
        )
        connection.commit()
        connection.close()
        self._write_dataset()

    def tearDown(self):
        self.database_patch.stop()
        self.environment.stop()
        self.temporary.cleanup()

    def _write_dataset(self):
        files = {
            self.config.uploads_dir / "upload.txt": b"upload-content",
            self.config.email_attachments_dir / "mail.xml": b"email-content",
            self.config.generated_documents_dir / "invoice.pdf": b"generated-content",
            self.config.inbox_dir / "incoming.ubl": b"document-content",
            self.config.config_dir / "preferences.json": b'{"language":"fr"}',
            self.config.config_dir / "google_client_secret.json": b'{"client_secret":"excluded"}',
            self.config.config_dir / "integration.json": b'{"refresh_token":"excluded-by-content"}',
            self.config.secrets_dir / "session.secret": b"excluded-session-secret",
        }
        for path, content in files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        program = self.root / "program"
        program.mkdir(parents=True, exist_ok=True)
        (program / "Aurelia.exe").write_bytes(b"application-binary")

    def _backup(self):
        return create_complete_backup(self.config)

    def _rewrite_archive(self, path, transform):
        path = Path(path)
        with zipfile.ZipFile(path, "r") as source:
            entries = {name: source.read(name) for name in source.namelist()}
        transform(entries)
        temporary = path.with_suffix(path.suffix + ".rewrite")
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as target:
            for name, content in entries.items():
                target.writestr(name, content)
        os.replace(temporary, path)

    def _replace_database(self, path, schema_version=None, corrupt=False):
        def transform(entries):
            if corrupt:
                database = b"not a sqlite database"
            else:
                database_path = self.root / "archive-edit.db"
                database_path.write_bytes(entries["database/aurelia_v5.db"])
                connection = sqlite3.connect(database_path)
                connection.execute(f"PRAGMA user_version={schema_version}")
                connection.commit()
                connection.close()
                database = database_path.read_bytes()
            entries["database/aurelia_v5.db"] = database
            manifest = json.loads(entries["manifest.json"])
            row = next(item for item in manifest["files"] if item["path"] == "database/aurelia_v5.db")
            old_size = row["size"]
            row["size"] = len(database)
            row["sha256"] = hashlib.sha256(database).hexdigest()
            manifest["total_logical_size"] += len(database) - old_size
            if schema_version is not None:
                manifest["database_schema_version"] = schema_version
            entries["manifest.json"] = json.dumps(manifest).encode("utf-8")
        self._rewrite_archive(path, transform)

    def test_complete_backup_manifest_hashes_and_database_integrity(self):
        result = self._backup()
        validation = validate_backup(result["path"])
        manifest = validation["manifest"]
        self.assertEqual(manifest["backup_format_version"], BACKUP_FORMAT_VERSION)
        self.assertEqual(manifest["database_integrity"], "ok")
        self.assertEqual(manifest["database_schema_version"], db.CURRENT_SCHEMA_VERSION)
        self.assertEqual(manifest["total_file_count"], len(manifest["files"]))
        self.assertTrue(all(len(row["sha256"]) == 64 for row in manifest["files"]))

    def test_expected_dataset_included_and_binaries_secrets_excluded(self):
        manifest = self._backup()["manifest"]
        paths = {row["path"] for row in manifest["files"]}
        self.assertIn("database/aurelia_v5.db", paths)
        self.assertIn("data/uploads/upload.txt", paths)
        self.assertIn("data/email_attachments/mail.xml", paths)
        self.assertIn("documents/Generated/invoice.pdf", paths)
        self.assertIn("documents/Inbox/incoming.ubl", paths)
        self.assertIn("config/preferences.json", paths)
        self.assertFalse(any("Aurelia.exe" in path for path in paths))
        self.assertFalse(any("secret" in path.lower() or "token" in path.lower() for path in paths))
        self.assertNotIn("config/integration.json", paths)
        self.assertEqual(manifest["secret_handling"]["mode"], "excluded_reauthentication_required")
        config_entry = next(row for row in manifest["files"] if row["path"] == "config/preferences.json")
        self.assertEqual(config_entry["classification"], "sensitive_configuration")

    def test_atomic_publication_and_separate_retention(self):
        first = self._backup()["path"]
        second = self._backup()["path"]
        third = self._backup()["path"]
        self.assertFalse(first.exists())
        self.assertTrue(second.exists())
        self.assertTrue(third.exists())
        self.assertEqual(len(list(self.config.backups_dir.glob("Aurelia-Backup-*.aurelia-backup"))), 2)
        self.assertFalse(list(self.config.backups_dir.glob(".*.tmp")))

    def test_staging_failure_leaves_no_valid_partial_backup(self):
        with patch("app.services.backup_restore._capture_database", side_effect=OSError("synthetic")):
            with self.assertRaises(MaintenanceError):
                self._backup()
        self.assertFalse(list(self.config.backups_dir.glob("Aurelia-Backup-*.aurelia-backup")))

    def test_corrupt_source_database_blocks_backup(self):
        self.config.sqlite_path.write_bytes(b"corrupt")
        with self.assertRaisesRegex(MaintenanceError, "SQLite"):
            self._backup()

    def test_tampered_file_and_missing_file_block_restore(self):
        tampered = self._backup()["path"]
        self._rewrite_archive(tampered, lambda entries: entries.__setitem__(
            "data/uploads/upload.txt", b"tampered"
        ))
        with self.assertRaisesRegex(MaintenanceError, "Intégrité"):
            validate_backup(tampered)

        missing = self._backup()["path"]
        self._rewrite_archive(missing, lambda entries: entries.pop("documents/Inbox/incoming.ubl"))
        with self.assertRaises(MaintenanceError) as caught:
            validate_backup(missing)
        self.assertEqual(caught.exception.code, "ARCHIVE_INVENTORY_MISMATCH")

    def test_missing_or_corrupt_manifest_blocks_restore(self):
        missing = self._backup()["path"]
        self._rewrite_archive(missing, lambda entries: entries.pop("manifest.json"))
        with self.assertRaises(MaintenanceError) as caught:
            validate_backup(missing)
        self.assertEqual(caught.exception.code, "MANIFEST_MISSING")

        corrupt = self._backup()["path"]
        self._rewrite_archive(corrupt, lambda entries: entries.__setitem__("manifest.json", b"{"))
        with self.assertRaises(MaintenanceError) as caught:
            validate_backup(corrupt)
        self.assertEqual(caught.exception.code, "MANIFEST_INVALID")

    def test_corrupt_database_inside_archive_blocks_restore(self):
        backup = self._backup()["path"]
        self._replace_database(backup, corrupt=True)
        with self.assertRaises(MaintenanceError) as caught:
            validate_backup(backup)
        self.assertEqual(caught.exception.code, "DATABASE_CORRUPT")

    def test_future_schema_blocks_restore(self):
        backup = self._backup()["path"]
        self._replace_database(backup, schema_version=db.CURRENT_SCHEMA_VERSION + 1)
        with self.assertRaises(MaintenanceError) as caught:
            validate_backup(backup)
        self.assertEqual(caught.exception.code, "SCHEMA_UNSUPPORTED")

    def test_older_supported_schema_restores_and_migrates(self):
        backup = self._backup()["path"]
        self._replace_database(backup, schema_version=0)
        self.config.generated_documents_dir.joinpath("after.txt").write_text("remove", encoding="utf-8")
        restore_backup(backup, self.config)
        connection = sqlite3.connect(self.config.sqlite_path)
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        connection.close()
        self.assertEqual(version, db.CURRENT_SCHEMA_VERSION)
        self.assertFalse(self.config.generated_documents_dir.joinpath("after.txt").exists())

    def test_successful_restore_reproduces_dataset_and_preserves_safety_backup(self):
        backup = self._backup()["path"]
        original = self.config.generated_documents_dir.joinpath("invoice.pdf").read_bytes()
        self.config.generated_documents_dir.joinpath("invoice.pdf").write_bytes(b"modified")
        self.config.generated_documents_dir.joinpath("after-backup.txt").write_bytes(b"new")
        connection = db.connect()
        connection.execute("INSERT INTO invoices(invoice_number,status,raw_json) VALUES('AFTER','NEW','{}')")
        connection.commit()
        connection.close()
        result = restore_backup(backup, self.config)
        self.assertEqual(self.config.generated_documents_dir.joinpath("invoice.pdf").read_bytes(), original)
        self.assertFalse(self.config.generated_documents_dir.joinpath("after-backup.txt").exists())
        connection = db.connect()
        numbers = {row[0] for row in connection.execute("SELECT invoice_number FROM invoices")}
        connection.close()
        self.assertEqual(numbers, {"SYN-001"})
        self.assertTrue(result["safety_backup"].is_file())
        self.assertEqual(validate_backup(result["safety_backup"])["database"]["integrity"], "ok")
        self.assertEqual(read_maintenance_status(self.config)["last_restore_result"], "success")

    def test_failed_post_restore_validation_rolls_back_live_dataset(self):
        backup = self._backup()["path"]
        marker = self.config.generated_documents_dir / "after-backup.txt"
        marker.write_text("must survive rollback", encoding="utf-8")

        def fail_validation(_):
            raise MaintenanceError("SYNTHETIC_POSTCHECK", "synthetic")

        with self.assertRaises(MaintenanceError) as caught:
            restore_backup(backup, self.config, post_restore_validator=fail_validation)
        self.assertEqual(caught.exception.code, "SYNTHETIC_POSTCHECK")
        self.assertEqual(marker.read_text(encoding="utf-8"), "must survive rollback")
        self.assertTrue(list((self.config.data_dir / "RestoreSafety").glob("*.aurelia-backup")))
        self.assertEqual(read_maintenance_status(self.config)["last_restore_result"], "failed")

    def test_insufficient_space_blocks_backup_and_restore_before_mutation(self):
        with patch("app.services.backup_restore._disk_free", return_value=0):
            with self.assertRaises(MaintenanceError) as caught:
                self._backup()
        self.assertEqual(caught.exception.code, "BACKUP_SPACE_INSUFFICIENT")

        backup = self._backup()["path"]
        marker = self.config.generated_documents_dir / "marker.txt"
        marker.write_text("unchanged", encoding="utf-8")
        with patch("app.services.backup_restore._disk_free", return_value=0):
            with self.assertRaises(MaintenanceError) as caught:
                restore_backup(backup, self.config)
        self.assertEqual(caught.exception.code, "RESTORE_SPACE_INSUFFICIENT")
        self.assertEqual(marker.read_text(encoding="utf-8"), "unchanged")

    def test_restore_refuses_active_instance_lock(self):
        backup = self._backup()["path"]
        with patch("app.services.backup_restore.InstanceLock.acquire", return_value=False):
            with self.assertRaises(MaintenanceError) as caught:
                restore_backup(backup, self.config)
        self.assertEqual(caught.exception.code, "RESTORE_APP_RUNNING")

    def test_restore_then_restart_database_services(self):
        backup = self._backup()["path"]
        restore_backup(backup, self.config)
        db.init_db()
        connection = db.connect()
        self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM users").fetchone()[0], 1)
        connection.close()

    def test_diagnostic_bundle_is_allowlisted_and_redacted(self):
        self.config.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.config.log_path.write_text(
            "Authorization: Bearer live-token password=unsafe client_secret=unsafe2",
            encoding="utf-8",
        )
        result = create_diagnostic_bundle(self.config)
        with zipfile.ZipFile(result["path"], "r") as archive:
            self.assertEqual(set(archive.namelist()), {"diagnostics.json", "recent.log"})
            metadata = json.loads(archive.read("diagnostics.json"))
            log = archive.read("recent.log").decode("utf-8")
        self.assertIn("aurelia_version", metadata)
        self.assertIn("database_schema_version", metadata)
        self.assertIn("operating_system", metadata)
        self.assertIn("ocr", metadata)
        self.assertIn("integrations", metadata)
        self.assertNotIn(str(self.root), json.dumps(metadata))
        self.assertNotIn("live-token", log)
        self.assertNotIn("unsafe", log)
        self.assertNotIn("document-content", log)

    def test_redaction_handles_nested_fields_and_headers(self):
        value = redact({"safe": "ok", "access_token": "abc", "nested": {"password": "def"}})
        self.assertEqual(value["safe"], "ok")
        self.assertEqual(value["access_token"], REDACTED)
        self.assertEqual(value["nested"]["password"], REDACTED)
        redacted = redact_text("Authorization: Bearer abc123 api_key=xyz")
        self.assertNotIn("abc123", redacted)
        self.assertNotIn("xyz", redacted)


if __name__ == "__main__":
    unittest.main()
