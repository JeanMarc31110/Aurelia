import sqlite3, os
from pathlib import Path

from app.local_config import load_local_config

BASE=Path(__file__).resolve().parents[1]
DB_PATH=load_local_config(BASE).sqlite_path

def connect():
    Path(DB_PATH).parent.mkdir(parents=True,exist_ok=True)
    con=sqlite3.connect(DB_PATH,timeout=10)
    con.row_factory=sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=5000")
    return con

def init_db():
    con=connect()
    con.executescript("""
    CREATE TABLE IF NOT EXISTS users(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      username TEXT UNIQUE NOT NULL,
      password_hash TEXT NOT NULL,
      role TEXT NOT NULL DEFAULT 'validateur',
      active INTEGER NOT NULL DEFAULT 1,
      must_change_password INTEGER NOT NULL DEFAULT 0 CHECK(must_change_password IN (0,1)),
      created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS suppliers(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT UNIQUE NOT NULL,
      siren TEXT,
      vat_number TEXT,
      iban TEXT,
      email TEXT,
      risk_level TEXT DEFAULT 'normal',
      created_at TEXT DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS customers(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT UNIQUE NOT NULL,
      siren TEXT,
      vat_number TEXT,
      email TEXT,
      address TEXT,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS companies(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      legal_name TEXT NOT NULL,
      trade_name TEXT,
      country TEXT,
      address TEXT,
      postal_code TEXT,
      city TEXT,
      vat_id TEXT,
      nif TEXT,
      siren TEXT,
      siret TEXT,
      iban TEXT,
      bic TEXT,
      email TEXT,
      phone TEXT,
      currency TEXT NOT NULL DEFAULT 'EUR',
      aliases_json TEXT NOT NULL DEFAULT '[]',
      active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE UNIQUE INDEX IF NOT EXISTS ux_companies_one_active
      ON companies(active) WHERE active=1;

    CREATE TABLE IF NOT EXISTS invoices(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      company_id INTEGER REFERENCES companies(id),
      document_type TEXT NOT NULL DEFAULT 'invoice',
      fingerprint TEXT UNIQUE,
      source_file TEXT,
      source_path TEXT,
      format TEXT,
      direction TEXT,
      invoice_number TEXT,
      supplier_name TEXT,
      customer_name TEXT,
      issue_date TEXT,
      due_date TEXT,
      net_amount REAL,
      vat_amount REAL,
      gross_amount REAL,
      currency TEXT,
      status TEXT,
      payment_status TEXT DEFAULT 'UNPAID',
      amount_paid REAL NOT NULL DEFAULT 0,
      amount_remaining REAL,
      risk_score INTEGER,
      document_risk_score INTEGER NOT NULL DEFAULT 0,
      fraud_risk_score INTEGER NOT NULL DEFAULT 0,
      proposed_account TEXT,
      accounting_confidence REAL,
      approved_account TEXT,
      account_final TEXT,
      account_validated_by TEXT,
      account_validated_at TEXT,
      last_exported_at TEXT,
      approved_by TEXT,
      approved_at TEXT,
      rejected_by TEXT,
      rejected_at TEXT,
      rejection_reason TEXT,
      validation_comment TEXT,
      lines_json TEXT NOT NULL DEFAULT '[]',
      raw_json TEXT NOT NULL,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS reviews(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      invoice_id INTEGER,
      reason TEXT,
      severity TEXT,
      resolved INTEGER DEFAULT 0,
      resolution TEXT,
      resolved_by TEXT,
      resolved_at TEXT,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS audit_events(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      invoice_id INTEGER,
      username TEXT,
      agent TEXT,
      event TEXT,
      details TEXT,
      field_name TEXT,
      old_value TEXT,
      new_value TEXT,
      reason TEXT,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS learned_mappings(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      supplier_name TEXT,
      keyword TEXT,
      account TEXT NOT NULL,
      validations INTEGER DEFAULT 1,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS bank_transactions(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      booking_date TEXT,
      label TEXT,
      amount REAL,
      currency TEXT,
      reference TEXT,
      matched_invoice_id INTEGER,
      source_file TEXT,
      import_batch_id INTEGER,
      fingerprint TEXT,
      status TEXT NOT NULL DEFAULT 'PENDING',
      ignored_by TEXT,
      ignored_at TEXT,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS bank_import_batches(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      source_file TEXT,
      file_sha256 TEXT NOT NULL,
      row_count INTEGER NOT NULL DEFAULT 0,
      imported_count INTEGER NOT NULL DEFAULT 0,
      duplicate_count INTEGER NOT NULL DEFAULT 0,
      username TEXT,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS payment_match_proposals(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      transaction_id INTEGER NOT NULL REFERENCES bank_transactions(id),
      invoice_id INTEGER NOT NULL REFERENCES invoices(id),
      score REAL NOT NULL,
      reasons_json TEXT NOT NULL DEFAULT '[]',
      positive_signals_json TEXT NOT NULL DEFAULT '[]',
      negative_signals_json TEXT NOT NULL DEFAULT '[]',
      contradictions_json TEXT NOT NULL DEFAULT '[]',
      status TEXT NOT NULL DEFAULT 'PENDING',
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      UNIQUE(transaction_id,invoice_id)
    );

    CREATE TABLE IF NOT EXISTS payment_matches(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      transaction_id INTEGER NOT NULL REFERENCES bank_transactions(id),
      invoice_id INTEGER NOT NULL REFERENCES invoices(id),
      allocated_amount REAL NOT NULL,
      username TEXT NOT NULL,
      comment TEXT,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      cancelled_at TEXT,
      cancelled_by TEXT,
      cancellation_reason TEXT
    );

    CREATE TABLE IF NOT EXISTS accounting_exports(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      batch_id TEXT UNIQUE NOT NULL,
      file_name TEXT NOT NULL,
      file_hash TEXT NOT NULL,
      filters_json TEXT NOT NULL DEFAULT '{}',
      invoice_count INTEGER NOT NULL,
      username TEXT NOT NULL,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS accounting_export_items(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      export_id INTEGER NOT NULL REFERENCES accounting_exports(id),
      invoice_id INTEGER NOT NULL REFERENCES invoices(id),
      is_reexport INTEGER NOT NULL DEFAULT 0,
      UNIQUE(export_id,invoice_id)
    );

    CREATE TABLE IF NOT EXISTS outbound_invoices(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      invoice_number TEXT UNIQUE,
      customer_id INTEGER,
      issue_date TEXT,
      due_date TEXT,
      net_amount REAL,
      vat_amount REAL,
      gross_amount REAL,
      currency TEXT DEFAULT 'EUR',
      description TEXT,
      pdf_path TEXT,
      xml_path TEXT,
      status TEXT DEFAULT 'DRAFT',
      created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS reminders(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      outbound_invoice_id INTEGER,
      customer_email TEXT,
      reminder_level INTEGER,
      subject TEXT,
      body TEXT,
      gmail_draft_id TEXT,
      status TEXT DEFAULT 'DRAFT',
      created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS sequences(
      name TEXT PRIMARY KEY,
      current_value INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS settings(
      key TEXT PRIMARY KEY,
      value TEXT,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS documents(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      fingerprint TEXT UNIQUE NOT NULL,
      document_type TEXT NOT NULL,
      source_file TEXT,
      source_path TEXT,
      status TEXT NOT NULL,
      findings_json TEXT NOT NULL DEFAULT '[]',
      raw_json TEXT NOT NULL,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS supplier_bank_accounts(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      supplier_identity_type TEXT NOT NULL,
      supplier_identity_value TEXT NOT NULL,
      supplier_name TEXT,
      iban TEXT NOT NULL,
      bic TEXT,
      status TEXT NOT NULL DEFAULT 'KNOWN' CHECK(status IN ('KNOWN','PENDING','REJECTED')),
      active INTEGER NOT NULL DEFAULT 0 CHECK(active IN (0,1)),
      source_file TEXT,
      source_invoice_number TEXT,
      source_issue_date TEXT,
      first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      accepted_at TEXT,
      accepted_by TEXT,
      UNIQUE(supplier_identity_type,supplier_identity_value,iban)
    );

    CREATE INDEX IF NOT EXISTS idx_supplier_bank_identity
      ON supplier_bank_accounts(supplier_identity_type,supplier_identity_value,status);

    CREATE TABLE IF NOT EXISTS email_attachments(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      content_sha256 TEXT UNIQUE NOT NULL,
      filename TEXT,
      source_message_id TEXT,
      source_sender TEXT,
      source_subject TEXT,
      stored_path TEXT,
      status TEXT NOT NULL,
      invoice_id INTEGER REFERENCES invoices(id),
      details_json TEXT NOT NULL DEFAULT '{}',
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS ingested_files(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      sha256 TEXT UNIQUE NOT NULL,
      original_filename TEXT NOT NULL,
      stored_path TEXT,
      status TEXT NOT NULL CHECK(status IN ('PROCESSING','IMPORTED','ERROR','UNSUPPORTED')),
      linked_invoice_id INTEGER REFERENCES invoices(id),
      linked_document_id INTEGER REFERENCES documents(id),
      error_type TEXT,
      error_message TEXT,
      duplicate_count INTEGER NOT NULL DEFAULT 0,
      last_duplicate_path TEXT,
      first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      processed_at TEXT
    );
    """)
    user_columns={row["name"] for row in con.execute("PRAGMA table_info(users)")}
    if "must_change_password" not in user_columns:
        con.execute("ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 1")
    company_columns={row["name"] for row in con.execute("PRAGMA table_info(companies)")}
    if "currency" not in company_columns:
        con.execute("ALTER TABLE companies ADD COLUMN currency TEXT NOT NULL DEFAULT 'EUR'")
    invoice_columns={row["name"] for row in con.execute("PRAGMA table_info(invoices)")}
    if "company_id" not in invoice_columns:
        con.execute("ALTER TABLE invoices ADD COLUMN company_id INTEGER REFERENCES companies(id)")
    if "document_type" not in invoice_columns:
        con.execute("ALTER TABLE invoices ADD COLUMN document_type TEXT NOT NULL DEFAULT 'invoice'")
    if "document_risk_score" not in invoice_columns:
        con.execute("ALTER TABLE invoices ADD COLUMN document_risk_score INTEGER NOT NULL DEFAULT 0")
    if "fraud_risk_score" not in invoice_columns:
        con.execute("ALTER TABLE invoices ADD COLUMN fraud_risk_score INTEGER NOT NULL DEFAULT 0")
    if "validation_comment" not in invoice_columns:
        con.execute("ALTER TABLE invoices ADD COLUMN validation_comment TEXT")
    if "lines_json" not in invoice_columns:
        con.execute("ALTER TABLE invoices ADD COLUMN lines_json TEXT NOT NULL DEFAULT '[]'")
    if "updated_at" not in invoice_columns:
        con.execute("ALTER TABLE invoices ADD COLUMN updated_at TEXT")
        if "created_at" in invoice_columns:
            con.execute("UPDATE invoices SET updated_at=COALESCE(created_at,CURRENT_TIMESTAMP) WHERE updated_at IS NULL")
        else:
            con.execute("UPDATE invoices SET updated_at=CURRENT_TIMESTAMP WHERE updated_at IS NULL")
    for column, definition in (
        ("amount_paid", "REAL NOT NULL DEFAULT 0"),
        ("amount_remaining", "REAL"),
        ("account_final", "TEXT"),
        ("account_validated_by", "TEXT"),
        ("account_validated_at", "TEXT"),
        ("last_exported_at", "TEXT"),
    ):
        if column not in invoice_columns:
            con.execute(f"ALTER TABLE invoices ADD COLUMN {column} {definition}")
    if "gross_amount" in invoice_columns:
        con.execute("UPDATE invoices SET amount_remaining=COALESCE(amount_remaining,gross_amount,0) WHERE amount_remaining IS NULL")
    else:
        con.execute("UPDATE invoices SET amount_remaining=0 WHERE amount_remaining IS NULL")
    bank_columns={row["name"] for row in con.execute("PRAGMA table_info(bank_transactions)")}
    for column, definition in (
        ("source_file", "TEXT"), ("import_batch_id", "INTEGER"), ("fingerprint", "TEXT"),
        ("status", "TEXT NOT NULL DEFAULT 'PENDING'"), ("ignored_by", "TEXT"), ("ignored_at", "TEXT"),
    ):
        if column not in bank_columns:
            con.execute(f"ALTER TABLE bank_transactions ADD COLUMN {column} {definition}")
    audit_columns={row["name"] for row in con.execute("PRAGMA table_info(audit_events)")}
    for column in ("field_name", "old_value", "new_value", "reason"):
        if column not in audit_columns:
            con.execute(f"ALTER TABLE audit_events ADD COLUMN {column} TEXT")
    proposal_columns={row["name"] for row in con.execute("PRAGMA table_info(payment_match_proposals)")}
    for column in ("positive_signals_json", "negative_signals_json", "contradictions_json"):
        if column not in proposal_columns:
            con.execute(f"ALTER TABLE payment_match_proposals ADD COLUMN {column} TEXT NOT NULL DEFAULT '[]'")
    con.execute("CREATE INDEX IF NOT EXISTS idx_invoices_company_id ON invoices(company_id)")
    if "status" in invoice_columns:
        con.execute("CREATE INDEX IF NOT EXISTS idx_invoices_status ON invoices(status)")
    if "direction" in invoice_columns:
        con.execute("CREATE INDEX IF NOT EXISTS idx_invoices_direction ON invoices(direction)")
    if "issue_date" in invoice_columns:
        con.execute("CREATE INDEX IF NOT EXISTS idx_invoices_issue_date ON invoices(issue_date)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_audit_events_invoice ON audit_events(invoice_id,created_at)")
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_bank_transactions_fingerprint ON bank_transactions(fingerprint) WHERE fingerprint IS NOT NULL")
    con.execute("CREATE INDEX IF NOT EXISTS idx_bank_transactions_status ON bank_transactions(status,booking_date)")
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_payment_match_active_transaction ON payment_matches(transaction_id) WHERE cancelled_at IS NULL")
    con.execute("CREATE INDEX IF NOT EXISTS idx_payment_matches_invoice ON payment_matches(invoice_id,cancelled_at)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_export_items_invoice ON accounting_export_items(invoice_id)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_ingested_files_status ON ingested_files(status,updated_at)")
    con.commit(); con.close()
