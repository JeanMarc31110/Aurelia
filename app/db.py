import sqlite3, os
from pathlib import Path

BASE=Path(__file__).resolve().parents[1]
DB_PATH=BASE/"data"/"aurelia_v5.db"
DB_PATH.parent.mkdir(parents=True,exist_ok=True)

def connect():
    con=sqlite3.connect(DB_PATH)
    con.row_factory=sqlite3.Row
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

    CREATE TABLE IF NOT EXISTS invoices(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
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
      risk_score INTEGER,
      proposed_account TEXT,
      accounting_confidence REAL,
      approved_account TEXT,
      approved_by TEXT,
      approved_at TEXT,
      rejected_by TEXT,
      rejected_at TEXT,
      rejection_reason TEXT,
      raw_json TEXT NOT NULL,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP
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
      created_at TEXT DEFAULT CURRENT_TIMESTAMP
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
    """)
    con.commit(); con.close()
