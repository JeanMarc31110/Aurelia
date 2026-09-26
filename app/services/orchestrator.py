import hashlib
import json
import re
from pathlib import Path
from app.resource_paths import resource_path

from app.db import connect
from app.services.company import determine_invoice_direction, get_active_company
from app.services.learning import suggest
from app.services.parties import ensure_customer, ensure_supplier
from app.services.supplier_banks import observe_supplier_bank_account


ROOT = Path(__file__).resolve().parents[2]
POLICY = json.loads(resource_path("config", "policy.json").read_text(encoding="utf-8"))
MAPPING = json.loads(resource_path("config", "account_mapping.json").read_text(encoding="utf-8"))
ACCOUNTING_DOCUMENT_TYPES = {"invoice", "credit_note"}


def _party_identity(party):
    for field in ("siret", "siren", "vat_id", "vat_number", "nif"):
        value = re.sub(r"[^A-Z0-9]", "", str((party or {}).get(field) or "").upper())
        if value:
            return value
    return str((party or {}).get("name") or "").strip().upper()


def fingerprint(invoice):
    key = "|".join([
        _party_identity(invoice.get("supplier") or invoice.get("issuer") or {}),
        str(invoice.get("invoice_number", "")).upper(),
        str(invoice.get("issue_date", "")),
        str(invoice.get("gross_amount", "")),
        str(invoice.get("currency", "EUR")),
    ])
    return hashlib.sha256(key.encode()).hexdigest()


def account_proposal(invoice):
    descriptions = [
        (line.get("description") or "").strip() for line in invoice.get("lines", [])
        if (line.get("description") or "").strip()
    ]
    if invoice.get("lines_extraction_confidence") == "none" or not descriptions:
        return {"account": None, "confidence": .15, "source": "insufficient_line_evidence"}
    learned = suggest(invoice)
    if learned:
        return learned
    text = " ".join(descriptions).lower()
    for rule in MAPPING:
        if any(keyword.lower() in text for keyword in rule["keywords"]):
            return {"account": rule["account"], "confidence": float(rule["confidence"]), "source": "rules"}
    return {"account": None, "confidence": .25, "source": "none"}


def _document_fingerprint(invoice):
    material = "|".join([
        str(invoice.get("source_file") or ""), str(invoice.get("source_path") or ""),
        str(invoice.get("document_type") or "unknown"), str(invoice.get("raw_text") or ""),
    ])
    return hashlib.sha256(material.encode()).hexdigest()


def _process_non_accounting_document(invoice):
    document_type = invoice.get("document_type") or "unknown"
    messages = {
        "quote": "Devis détecté — document non comptabilisable comme facture",
        "purchase_order": "Bon de commande détecté — document non comptabilisable comme facture",
        "bank_statement": "Relevé bancaire détecté — utiliser le module Banque",
        "unknown": "Type de document inconnu — validation humaine nécessaire",
    }
    finding = {
        "agent": "DOCUMENT_TYPE", "code": "DOCUMENT_TYPE", "severity": "warning",
        "ok": False, "message": messages.get(document_type, "Document hors workflow facture"),
    }
    status = "NON_ACCOUNTING_DOCUMENT" if document_type != "unknown" else "REVIEW_REQUIRED"
    con = connect()
    con.execute(
        """INSERT INTO documents(fingerprint,document_type,source_file,source_path,status,findings_json,raw_json)
           VALUES(?,?,?,?,?,?,?) ON CONFLICT(fingerprint) DO NOTHING""",
        (_document_fingerprint(invoice), document_type, invoice.get("source_file"), invoice.get("source_path"),
         status, json.dumps([finding], ensure_ascii=False), json.dumps(invoice, ensure_ascii=False)),
    )
    row = con.execute("SELECT id FROM documents WHERE fingerprint=?", (_document_fingerprint(invoice),)).fetchone()
    con.commit()
    con.close()
    return {
        "status": status,
        "document_type": document_type,
        "findings": [finding],
        "document_risk_score": 10 if document_type == "unknown" else 0,
        "fraud_risk_score": 0,
        "risk_score": 10 if document_type == "unknown" else 0,
        "accounting_proposal": {"account": None, "confidence": 0.0, "source": "not_an_invoice"},
        "payment_authorized": False,
        "human_review_required": document_type == "unknown",
        "document_id": row["id"] if row else None,
    }


def process_invoice(invoice, username="system"):
    document_type = invoice.get("document_type") or "invoice"
    invoice["document_type"] = document_type
    if document_type not in ACCOUNTING_DOCUMENT_TYPES:
        return _process_non_accounting_document(invoice)

    company = get_active_company()
    direction, direction_evidence, company_id = determine_invoice_direction(
        invoice.get("issuer") or invoice.get("supplier") or {}, invoice.get("customer") or {}, company
    )
    invoice["direction"] = direction
    invoice["direction_evidence"] = direction_evidence
    invoice["company_id"] = company_id
    ensure_supplier(**{key: (invoice.get("supplier") or {}).get(key)
                       for key in ["name", "siren", "vat_number", "email", "iban"]})
    ensure_customer(**{key: (invoice.get("customer") or {}).get(key)
                       for key in ["name", "siren", "vat_number", "email", "address"]})

    bank_observation = observe_supplier_bank_account(invoice)
    fp = fingerprint(invoice)
    con = connect()
    duplicate_row = con.execute("SELECT id FROM invoices WHERE fingerprint=?", (fp,)).fetchone()
    duplicate = bool(duplicate_row)
    con.close()
    findings = []
    document_risk = 0
    fraud_risk = 0

    def add(agent, code, severity, ok, message, score=0, risk_kind="document", details=None):
        nonlocal document_risk, fraud_risk
        finding = {"agent": agent, "code": code, "severity": severity, "ok": ok, "message": message}
        if details:
            finding["details"] = details
        findings.append(finding)
        if not ok:
            if risk_kind == "fraud":
                fraud_risk += score
            else:
                document_risk += score

    supplier = (invoice.get("supplier") or {}).get("name")
    add("FOURNISSEURS", "supplier", "error", bool(supplier),
        "Fournisseur identifié" if supplier else "Fournisseur à compléter", 20)
    add("FACTURATION", "invoice_number", "error", bool(invoice.get("invoice_number")),
        "Numéro présent" if invoice.get("invoice_number") else "Numéro à compléter", 20)
    if invoice.get("needs_manual_extraction") or invoice.get("needs_manual_validation"):
        add("EXTRACTION", "manual", "error", False, "Extraction structurée à valider", 25)
    add("FACTURATION", "direction", "error", direction in {"purchase", "sale"},
        f"Direction déterminée : {direction}" if direction in {"purchase", "sale"}
        else "Direction achat/vente à valider", 20)
    if invoice.get("net_amount") or invoice.get("vat_amount"):
        expected = round(float(invoice.get("net_amount", 0)) + float(invoice.get("vat_amount", 0)), 2)
        gross = round(float(invoice.get("gross_amount", 0)), 2)
        add("TVA", "totals", "error",
            abs(expected - gross) <= POLICY["thresholds"]["rounding_tolerance"],
            f"HT+TVA={expected:.2f}; TTC={gross:.2f}", 30)
    if invoice.get("vat_mechanism") == "reverse_charge":
        add("TVA", "reverse_charge", "warning", True, "Autoliquidation détectée ; TVA nulle justifiée")
    if duplicate:
        add("WORKFLOW", "DUPLICATE", "warning", False,
            "Doublon documentaire exact détecté — aucune fraude déduite automatiquement", 40)
    if bank_observation.get("status") == "RIB_CHANGED":
        invoice["supplier_bank_details_changed"] = True
        add("TRESORERIE", "RIB_CHANGED", "critical", False,
            f"Changement de RIB pour {bank_observation.get('supplier') or 'le fournisseur'} : "
            f"{bank_observation['old_iban_masked']} → {bank_observation['new_iban_masked']}",
            50, "fraud", bank_observation)
    if invoice.get("vat_treatment") == "uncertain":
        add("TVA", "vat", "critical", False, "TVA incertaine", 40)

    proposal = account_proposal(invoice)
    add("COMPTABILITE", "account", "warning",
        proposal["confidence"] >= POLICY["thresholds"]["accounting_confidence"],
        f"Compte {proposal['account'] or 'non déterminé'} — confiance {proposal['confidence']:.0%}", 10)

    status = "DUPLICATE" if duplicate else ("REVIEW_REQUIRED" if any(not item["ok"] for item in findings) else "VALIDATED")
    legacy_risk = min(100, document_risk + fraud_risk)
    result = {
        "status": status, "risk_score": legacy_risk,
        "document_risk_score": min(100, document_risk), "fraud_risk_score": min(100, fraud_risk),
        "fingerprint": fp, "findings": findings, "accounting_proposal": proposal,
        "payment_authorized": False, "human_review_required": status != "VALIDATED",
    }
    if duplicate_row:
        result["invoice_id"] = duplicate_row["id"]
    if not duplicate:
        con = connect()
        cursor = con.cursor()
        cursor.execute(
            """INSERT INTO invoices(
               company_id,document_type,fingerprint,source_file,source_path,format,direction,invoice_number,
               supplier_name,customer_name,issue_date,due_date,net_amount,vat_amount,gross_amount,currency,status,
               risk_score,document_risk_score,fraud_risk_score,proposed_account,accounting_confidence,lines_json,raw_json)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (company_id, document_type, fp, invoice.get("source_file"), invoice.get("source_path"),
             invoice.get("format"), direction, invoice.get("invoice_number"), supplier,
             (invoice.get("customer") or {}).get("name"), invoice.get("issue_date"), invoice.get("due_date"),
             invoice.get("net_amount"), invoice.get("vat_amount"), invoice.get("gross_amount"),
             invoice.get("currency", "EUR"), status, legacy_risk, result["document_risk_score"],
             result["fraud_risk_score"], proposal.get("account"), proposal.get("confidence"),
             json.dumps(invoice.get("lines") or [], ensure_ascii=False),
             json.dumps(invoice, ensure_ascii=False)),
        )
        invoice_id = cursor.lastrowid
        for finding in findings:
            cursor.execute(
                "INSERT INTO audit_events(invoice_id,username,agent,event,details) VALUES(?,?,?,?,?)",
                (invoice_id, username, finding["agent"], finding["code"], json.dumps(finding, ensure_ascii=False)),
            )
            if not finding["ok"]:
                cursor.execute(
                    "INSERT INTO reviews(invoice_id,reason,severity) VALUES(?,?,?)",
                    (invoice_id, finding["message"], finding["severity"]),
                )
        con.commit()
        con.close()
        result["invoice_id"] = invoice_id
    return result
