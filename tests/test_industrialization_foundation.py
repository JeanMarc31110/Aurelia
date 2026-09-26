import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import db
from app.local_config import ensure_local_directories, load_local_config
from app.services.runtime_migration import migrate_legacy_runtime_data


ROOT = Path(__file__).resolve().parents[1]


class RuntimePathTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_default_windows_layout_separates_runtime_categories(self):
        environment = {
            "LOCALAPPDATA": str(self.root / "LocalAppData"),
            "USERPROFILE": str(self.root / "Profile"),
        }
        with patch.dict(os.environ, environment, clear=True):
            config = load_local_config(self.root / "Programs" / "Aurelia")
        state = (self.root / "LocalAppData" / "Aurelia").resolve()
        documents = (self.root / "Profile" / "Documents" / "Aurelia").resolve()
        self.assertEqual(config.data_dir, state)
        self.assertEqual(config.internal_data_dir, state / "Data")
        self.assertEqual(config.config_dir, state / "Config")
        self.assertEqual(config.secrets_dir, state / "Secrets")
        self.assertEqual(config.logs_dir, state / "Logs")
        self.assertEqual(config.sqlite_path, state / "aurelia_v5.db")
        self.assertEqual(config.documents_dir, documents)
        self.assertEqual(config.generated_documents_dir, documents / "Generated")

    def test_runtime_root_provides_a_complete_disposable_layout(self):
        runtime = self.root / "runtime"
        with patch.dict(os.environ, {"AURELIA_RUNTIME_ROOT": str(runtime)}, clear=True):
            config = ensure_local_directories(load_local_config(self.root / "program"))
        self.assertEqual(config.data_dir, (runtime / "state").resolve())
        self.assertEqual(config.documents_dir, (runtime / "documents").resolve())
        for directory in config.required_directories():
            self.assertTrue(directory.is_dir())

    def test_runtime_root_does_not_discover_a_repository_legacy_database(self):
        program = self.root / "program"
        repository_legacy = program / "data" / "aurelia_v5.db"
        repository_legacy.parent.mkdir(parents=True)
        repository_legacy.write_bytes(b"must not be discovered")
        runtime = self.root / "runtime"
        with patch.dict(os.environ, {"AURELIA_RUNTIME_ROOT": str(runtime)}, clear=True):
            config = load_local_config(program)
            self.assertEqual(config.legacy_sqlite_path, (runtime / "legacy" / "aurelia_v5.db").resolve())
            self.assertFalse(config.legacy_data_layout)

    def test_specific_legacy_overrides_remain_authoritative(self):
        custom = self.root / "custom"
        environment = {
            "AURELIA_DATA_DIR": str(custom / "state"),
            "AURELIA_DB_PATH": str(custom / "database.db"),
            "AURELIA_UPLOADS_PATH": str(custom / "uploads"),
            "AURELIA_DOCUMENTS_DIR": str(custom / "documents"),
            "USERPROFILE": str(self.root / "profile"),
        }
        with patch.dict(os.environ, environment, clear=True):
            config = load_local_config(self.root / "program")
        self.assertEqual(config.data_dir, (custom / "state").resolve())
        self.assertEqual(config.sqlite_path, (custom / "database.db").resolve())
        self.assertEqual(config.uploads_dir, (custom / "uploads").resolve())

    def test_generated_documents_never_default_to_install_root(self):
        runtime = self.root / "runtime"
        program = self.root / "installed-program"
        with patch.dict(os.environ, {"AURELIA_RUNTIME_ROOT": str(runtime)}, clear=True):
            config = load_local_config(program)
        self.assertFalse(config.generated_documents_dir.is_relative_to(program.resolve()))

    def test_legacy_generated_files_are_copied_and_source_is_preserved(self):
        program = self.root / "program"
        source = program / "data" / "generated" / "nested" / "invoice.pdf"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"synthetic invoice")
        with patch.dict(os.environ, {"AURELIA_RUNTIME_ROOT": str(self.root / "runtime")}, clear=True):
            config = ensure_local_directories(load_local_config(program))
            outcomes = migrate_legacy_runtime_data(config)
        target = config.generated_documents_dir / "nested" / "invoice.pdf"
        self.assertEqual(target.read_bytes(), b"synthetic invoice")
        self.assertEqual(source.read_bytes(), b"synthetic invoice")
        self.assertEqual(outcomes[0].status, "copied")

    def test_legacy_copy_never_overwrites_a_conflicting_target(self):
        program = self.root / "program"
        source = program / "data" / "generated" / "invoice.pdf"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"legacy")
        with patch.dict(os.environ, {"AURELIA_RUNTIME_ROOT": str(self.root / "runtime")}, clear=True):
            config = ensure_local_directories(load_local_config(program))
            target = config.generated_documents_dir / "invoice.pdf"
            target.write_bytes(b"current")
            outcomes = migrate_legacy_runtime_data(config)
        self.assertEqual(target.read_bytes(), b"current")
        self.assertEqual(source.read_bytes(), b"legacy")
        self.assertEqual(outcomes[0].status, "conflict")


class SchemaFoundationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "aurelia.db"
        self.database_patch = patch.object(db, "DB_PATH", self.database)
        self.database_patch.start()

    def tearDown(self):
        self.database_patch.stop()
        self.temporary.cleanup()

    def user_version(self):
        connection = sqlite3.connect(self.database)
        try:
            return connection.execute("PRAGMA user_version").fetchone()[0]
        finally:
            connection.close()

    def test_fresh_database_is_created_at_current_schema(self):
        db.init_db()
        self.assertEqual(self.user_version(), db.CURRENT_SCHEMA_VERSION)

    def test_legacy_zero_database_preserves_business_data(self):
        connection = sqlite3.connect(self.database)
        connection.execute("CREATE TABLE users(id INTEGER PRIMARY KEY, username TEXT UNIQUE, password_hash TEXT, role TEXT, active INTEGER, created_at TEXT)")
        connection.execute("INSERT INTO users VALUES(1,'legacy','hash','admin',1,CURRENT_TIMESTAMP)")
        connection.commit()
        connection.close()
        db.init_db()
        connection = sqlite3.connect(self.database)
        try:
            row = connection.execute("SELECT username,must_change_password FROM users WHERE id=1").fetchone()
        finally:
            connection.close()
        self.assertEqual(row, ("legacy", 1))
        self.assertEqual(self.user_version(), db.CURRENT_SCHEMA_VERSION)

    def test_failed_migration_rolls_back_schema_and_version(self):
        connection = sqlite3.connect(self.database)
        connection.execute("CREATE TABLE legacy_marker(value TEXT)")
        connection.execute("INSERT INTO legacy_marker VALUES('preserved')")
        connection.commit()
        connection.close()

        def failing_migration(connection):
            connection.execute("CREATE TABLE should_rollback(value TEXT)")
            raise RuntimeError("synthetic migration failure")

        with patch.dict(db.MIGRATIONS, {0: failing_migration}, clear=True):
            with self.assertRaises(db.SchemaMigrationError):
                db.init_db()
        connection = sqlite3.connect(self.database)
        try:
            marker = connection.execute("SELECT value FROM legacy_marker").fetchone()[0]
            rolled_back = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='should_rollback'"
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(marker, "preserved")
        self.assertIsNone(rolled_back)
        self.assertEqual(self.user_version(), 0)

    def test_future_schema_is_rejected_without_mutation(self):
        connection = sqlite3.connect(self.database)
        connection.execute("CREATE TABLE future_marker(value TEXT)")
        connection.execute("INSERT INTO future_marker VALUES('unchanged')")
        connection.execute(f"PRAGMA user_version={db.CURRENT_SCHEMA_VERSION + 1}")
        connection.commit()
        connection.close()
        with self.assertRaises(db.SchemaVersionError):
            db.init_db()
        connection = sqlite3.connect(self.database)
        try:
            self.assertEqual(connection.execute("SELECT value FROM future_marker").fetchone()[0], "unchanged")
        finally:
            connection.close()
        self.assertEqual(self.user_version(), db.CURRENT_SCHEMA_VERSION + 1)

    def test_current_schema_restart_is_idempotent(self):
        db.init_db()
        connection = sqlite3.connect(self.database)
        connection.execute("INSERT INTO settings(key,value) VALUES('restart-marker','keep')")
        connection.commit()
        connection.close()
        db.init_db()
        connection = sqlite3.connect(self.database)
        try:
            value = connection.execute("SELECT value FROM settings WHERE key='restart-marker'").fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(value, "keep")


class IndustrializationContractTests(unittest.TestCase):
    def test_backup_dataset_contract_is_documented(self):
        contract = (ROOT / "docs" / "BACKUP_DATASET.md").read_text(encoding="utf-8")
        for required in ("aurelia_v5.db", "SHA-256", "VERSION.txt", "Secrets", "Generated"):
            self.assertIn(required, contract)


if __name__ == "__main__":
    unittest.main()
