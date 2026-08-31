import csv
import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from app.db import connect


TOLERANCE = Decimal("0.02")
ELIGIBLE_INVOICE_STATUSES = {"APPROVED", "VALIDATED"}
LEGAL_FORMS = {
    "SA", "SAS", "SASU", "SARL", "EURL", "SNC", "SCI", "SELARL",
    "SL", "SLU", "SRL", "SPA", "LTD", "LIMITED", "LLC", "INC", "GMBH", "BV", "NV",
}
BANK_LABEL_WORDS = {
    "VIREMENT", "VIR", "SEPA", "PAIEMENT", "PAYMENT", "PRELEVEMENT", "PRLV",
    "CARTE", "CB", "CHEQUE", "TRANSFER", "TRANSFERT", "REGLEMENT", "FACTURE",
    "ACHAT", "RETRAIT", "FRAIS", "BANCAIRE", "PERMANENT", "EMIS", "RECU",
}
SIGNAL_LABELS = {
    "REFERENCE_EXACT": "Référence facture exacte",
    "AMOUNT_EXACT": "Montant exact",
    "PARTY_MATCH": "Fournisseur ou client cohérent avec le libellé",
    "DATE_CLOSE": "Date proche de l’échéance",
    "REFERENCE_DIFFERENT": "Référence différente",
    "AMOUNT_DIFFERENT": "Montant différent du reste à payer",
    "PARTY_NOT_IDENTIFIED": "Tiers non identifié dans le libellé",
    "DATE_NOT_CLOSE": "Date éloignée de l’échéance",
    "PARTY_CONFLICT": "Le tiers du libellé contredit celui de la facture",
    "DIRECTION_CONFLICT": "Le sens débit/crédit contredit le type de facture",
    "CURRENCY_CONFLICT": "La devise bancaire contredit celle de la facture",
}


def _amount(value):
    cleaned = re.sub(r"[^0-9,.-]", "", str(value or "0"))
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        return float(Decimal(cleaned or "0").quantize(Decimal("0.01")))
    except InvalidOperation as exc:
        raise ValueError(f"Montant bancaire invalide: {value}") from exc


def _normalise(value):
    ascii_value = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^A-Z0-9]", "", ascii_value.upper())


def _tokens(value):
    ascii_value = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    return re.findall(r"[A-Z0-9]+", ascii_value.upper())


def _party_tokens(value):
    return {token for token in _tokens(value) if token not in LEGAL_FORMS and len(token) >= 2}


def _label_party_tokens(label, reference=None):
    reference_tokens = set(_tokens(reference))
    return {
        token for token in _tokens(label)
        if token not in LEGAL_FORMS and token not in BANK_LABEL_WORDS
        and token not in reference_tokens and not token.isdigit() and len(token) >= 3
    }


def _party_signals(label, reference, party_name, known_party_tokens):
    party = _party_tokens(party_name)
    label_party = _label_party_tokens(label, reference)
    party_match = bool(party and party <= label_party)
    known_matches = {frozenset(tokens) for tokens in known_party_tokens if tokens and tokens <= label_party}
    party_conflict = False
    if party and not party_match:
        party_conflict = bool(known_matches and frozenset(party) not in known_matches)
        if not party_conflict and len(label_party) >= 2 and party.isdisjoint(label_party):
            party_conflict = True
    return party_match, party_conflict


def _signal_text(codes):
    return [SIGNAL_LABELS.get(code, code) for code in codes]


def _transaction_fingerprint(row, occurrence):
    material = "|".join([
        str(row.get("external_id") or ""), str(row.get("booking_date") or ""),
        f"{row.get('amount', 0):.2f}", str(row.get("currency") or "EUR").upper(),
        _normalise(row.get("reference")), _normalise(row.get("label")), str(occurrence),
    ])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def import_bank_csv(path, source_file=None, username=None):
    path = Path(path)
    parsed = []
    with path.open("rb") as binary:
        file_hash = hashlib.sha256(binary.read()).hexdigest()
    with path.open(encoding="utf-8-sig", newline="") as handle:
        sample = handle.read(4096);handle.seek(0)
        try:dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:dialect = csv.excel
        occurrences = defaultdict(int)
        for source in csv.DictReader(handle, dialect=dialect):
            normalised = {str(key or "").strip().lower(): value for key, value in source.items()}
            row = {
                "booking_date": normalised.get("booking_date") or normalised.get("date"),
                "label": normalised.get("label") or normalised.get("libelle") or normalised.get("libellé") or "",
                "amount": _amount(normalised.get("amount") or normalised.get("montant")),
                "currency": (normalised.get("currency") or normalised.get("devise") or "EUR").upper(),
                "reference": normalised.get("reference") or normalised.get("ref") or "",
                "external_id": normalised.get("transaction_id") or normalised.get("id") or "",
            }
            base = "|".join(str(row[key]) for key in ("booking_date", "amount", "currency", "reference", "label", "external_id"))
            occurrences[base] += 1
            row["fingerprint"] = _transaction_fingerprint(row, occurrences[base])
            parsed.append(row)
    con = connect()
    cursor = con.execute(
        """INSERT INTO bank_import_batches(source_file,file_sha256,row_count,username)
           VALUES(?,?,?,?)""", (source_file or path.name, file_hash, len(parsed), username),
    )
    batch_id = cursor.lastrowid
    inserted = []
    duplicates = 0
    for row in parsed:
        existing = con.execute("SELECT id FROM bank_transactions WHERE fingerprint=?", (row["fingerprint"],)).fetchone()
        if existing:
            duplicates += 1
            continue
        transaction = con.execute(
            """INSERT INTO bank_transactions(
                 booking_date,label,amount,currency,reference,source_file,import_batch_id,fingerprint,status)
               VALUES(?,?,?,?,?,?,?,?,'PENDING')""",
            (row["booking_date"], row["label"], row["amount"], row["currency"], row["reference"],
             source_file or path.name, batch_id, row["fingerprint"]),
        )
        inserted.append(dict(row, id=transaction.lastrowid, import_batch_id=batch_id))
    con.execute(
        "UPDATE bank_import_batches SET imported_count=?,duplicate_count=? WHERE id=?",
        (len(inserted), duplicates, batch_id),
    )
    con.commit();con.close()
    if inserted:
        generate_match_proposals([row["id"] for row in inserted])
    return inserted


def _days_between(first, second):
    try:return abs((date.fromisoformat(first) - date.fromisoformat(second)).days)
    except (TypeError, ValueError):return None


def generate_match_proposals(transaction_ids=None):
    con = connect()
    params = []
    where = "WHERE t.status='PENDING'"
    if transaction_ids:
        where += " AND t.id IN (%s)" % ",".join("?" for _ in transaction_ids)
        params.extend(transaction_ids)
    transactions = [dict(row) for row in con.execute(
        "SELECT t.* FROM bank_transactions t " + where, params,
    )]
    invoices = [dict(row) for row in con.execute(
        """SELECT i.*,COALESCE((SELECT SUM(pm.allocated_amount) FROM payment_matches pm
             WHERE pm.invoice_id=i.id AND pm.cancelled_at IS NULL),0) matched_amount
           FROM invoices i WHERE i.status IN ('APPROVED','VALIDATED')""",
    )]
    all_invoices = [dict(row) for row in con.execute(
        "SELECT supplier_name,customer_name FROM invoices",
    )]
    known_party_tokens = []
    for known in all_invoices:
        for field in ("supplier_name", "customer_name"):
            tokens = _party_tokens(known.get(field))
            if tokens:known_party_tokens.append(tokens)
    created = []
    for transaction in transactions:
        con.execute(
            "DELETE FROM payment_match_proposals WHERE transaction_id=? AND status='PENDING'",
            (transaction["id"],),
        )
        transaction_amount = Decimal(str(abs(transaction["amount"])))
        for invoice in invoices:
            remaining = Decimal(str(invoice.get("gross_amount") or 0)) - Decimal(str(invoice.get("matched_amount") or 0))
            amount_exact = abs(transaction_amount - abs(remaining)) <= TOLERANCE
            invoice_ref = _normalise(invoice.get("invoice_number"))
            reference_exact = bool(invoice_ref and invoice_ref == _normalise(transaction.get("reference")))
            party_name = invoice.get("supplier_name") if invoice.get("direction") == "purchase" else invoice.get("customer_name")
            party_match, party_conflict = _party_signals(
                transaction.get("label"), transaction.get("reference"), party_name, known_party_tokens,
            )
            days = _days_between(transaction.get("booking_date"), invoice.get("due_date") or invoice.get("issue_date"))
            date_close = days is not None and days <= 15
            direction_conflict = (
                invoice.get("direction") == "purchase" and transaction["amount"] >= 0
            ) or (
                invoice.get("direction") == "sale" and transaction["amount"] <= 0
            )
            invoice_currency = str(invoice.get("currency") or "").upper()
            transaction_currency = str(transaction.get("currency") or "").upper()
            currency_conflict = bool(invoice_currency and transaction_currency and invoice_currency != transaction_currency)

            positive_signals = []
            negative_signals = []
            contradictions = []
            if reference_exact:positive_signals.append("REFERENCE_EXACT")
            elif invoice_ref and transaction.get("reference"):negative_signals.append("REFERENCE_DIFFERENT")
            if amount_exact:positive_signals.append("AMOUNT_EXACT")
            else:negative_signals.append("AMOUNT_DIFFERENT")
            if party_match:positive_signals.append("PARTY_MATCH")
            elif party_conflict:contradictions.append("PARTY_CONFLICT")
            else:negative_signals.append("PARTY_NOT_IDENTIFIED")
            if date_close:positive_signals.append("DATE_CLOSE")
            elif days is not None:negative_signals.append("DATE_NOT_CLOSE")
            if direction_conflict:contradictions.append("DIRECTION_CONFLICT")
            if currency_conflict:contradictions.append("CURRENCY_CONFLICT")

            score = sum({"REFERENCE_EXACT":45, "AMOUNT_EXACT":35, "PARTY_MATCH":20,
                         "DATE_CLOSE":10}.get(signal, 0) for signal in positive_signals)
            score = min(100, score)
            if "AMOUNT_DIFFERENT" in negative_signals:score = min(score, 55)
            if "PARTY_CONFLICT" in contradictions:score = min(score, 35)
            if "CURRENCY_CONFLICT" in contradictions:score = min(score, 20)
            if "DIRECTION_CONFLICT" in contradictions:score = min(score, 10)
            contradictory_link = bool(contradictions and (reference_exact or amount_exact))
            if score < 40 and not contradictory_link:continue
            reasons = _signal_text(positive_signals)
            con.execute(
                """INSERT INTO payment_match_proposals(
                     transaction_id,invoice_id,score,reasons_json,positive_signals_json,
                     negative_signals_json,contradictions_json,status)
                   VALUES(?,?,?,?,?,?,?,'PENDING') ON CONFLICT(transaction_id,invoice_id) DO UPDATE SET
                     score=excluded.score,reasons_json=excluded.reasons_json,
                     positive_signals_json=excluded.positive_signals_json,
                     negative_signals_json=excluded.negative_signals_json,
                     contradictions_json=excluded.contradictions_json,status='PENDING',
                     updated_at=CURRENT_TIMESTAMP""",
                (transaction["id"], invoice["id"], score, json.dumps(reasons, ensure_ascii=False),
                 json.dumps(positive_signals), json.dumps(negative_signals), json.dumps(contradictions)),
            )
            created.append({"transaction_id": transaction["id"], "invoice_id": invoice["id"],
                            "score": score, "reasons": reasons,
                            "positive_signals": positive_signals,
                            "negative_signals": negative_signals,
                            "contradictions": contradictions})
    con.commit();con.close()
    return created


def propose_matches(tolerance=.02):
    del tolerance
    generate_match_proposals()
    con = connect()
    rows = [dict(row) for row in con.execute(
        """SELECT p.*,i.invoice_number,i.supplier_name,i.customer_name,i.gross_amount,
           t.amount,t.reference,t.label,t.booking_date FROM payment_match_proposals p
           JOIN invoices i ON i.id=p.invoice_id JOIN bank_transactions t ON t.id=p.transaction_id
           WHERE p.status='PENDING' AND t.status='PENDING'
             AND i.status IN ('APPROVED','VALIDATED')
           ORDER BY p.score DESC,p.id""",
    )]
    con.close()
    for row in rows:
        row["confidence"] = row["score"] / 100
        row["positive_signals"] = json.loads(row.pop("positive_signals_json") or "[]")
        row["negative_signals"] = json.loads(row.pop("negative_signals_json") or "[]")
        row["contradictions"] = json.loads(row.pop("contradictions_json") or "[]")
        if row["contradictions"]:row["classification"] = "rejected_conflict"
        elif row["score"] >= 80:row["classification"] = "proposed_high"
        elif row["score"] >= 60:row["classification"] = "proposed_medium"
        else:row["classification"] = "uncertain"
        row["auto_matched"] = False
        row["reasons"] = json.loads(row.pop("reasons_json") or "[]")
        row["negative_reasons"] = _signal_text(row["negative_signals"])
        row["contradiction_reasons"] = _signal_text(row["contradictions"])
    return rows


def _ineligible_matches(transaction, invoices):
    matches = []
    transaction_amount = Decimal(str(abs(transaction.get("amount") or 0)))
    for invoice in invoices:
        if invoice.get("status") in ELIGIBLE_INVOICE_STATUSES:continue
        reference_exact = bool(
            invoice.get("invoice_number")
            and _normalise(invoice["invoice_number"]) == _normalise(transaction.get("reference"))
        )
        amount_exact = abs(transaction_amount - Decimal(str(abs(invoice.get("gross_amount") or 0)))) <= TOLERANCE
        party_name = invoice.get("supplier_name") if invoice.get("direction") == "purchase" else invoice.get("customer_name")
        party_match, _ = _party_signals(
            transaction.get("label"), transaction.get("reference"), party_name,
            [_party_tokens(party_name)],
        )
        if reference_exact and amount_exact:
            matches.append({
                "id": invoice["id"], "invoice_number": invoice.get("invoice_number"),
                "supplier_name": invoice.get("supplier_name"), "customer_name": invoice.get("customer_name"),
                "gross_amount": invoice.get("gross_amount"), "status": invoice.get("status"),
                "party_match": party_match,
            })
    return sorted(matches, key=lambda item: (not item["party_match"], item["id"]))


def bank_overview():
    proposals = propose_matches()
    con = connect()
    transactions = [dict(row) for row in con.execute(
        "SELECT * FROM bank_transactions ORDER BY id DESC LIMIT 100",
    )]
    invoices = [dict(row) for row in con.execute(
        """SELECT id,invoice_number,supplier_name,customer_name,gross_amount,direction,status
             FROM invoices WHERE status IN ('APPROVED','VALIDATED') ORDER BY issue_date DESC,id DESC""",
    )]
    all_invoices = [dict(row) for row in con.execute(
        """SELECT id,invoice_number,supplier_name,customer_name,gross_amount,direction,status
             FROM invoices ORDER BY id""",
    )]
    batches = [dict(row) for row in con.execute("SELECT * FROM bank_import_batches ORDER BY id DESC LIMIT 10")]
    con.close()
    by_transaction = defaultdict(list)
    for proposal in proposals:by_transaction[proposal["transaction_id"]].append(proposal)
    pending = []
    for transaction in transactions:
        if transaction.get("status") != "PENDING":continue
        transaction["suggested_allocation"] = f"{abs(Decimal(str(transaction.get('amount') or 0))):.2f}"
        candidates = by_transaction.get(transaction["id"], [])
        reliable = [candidate for candidate in candidates
                    if candidate["score"] >= 60 and not candidate["contradictions"]]
        ambiguous = len(reliable) > 1 and reliable[0]["score"] - reliable[1]["score"] <= 10
        transaction["ambiguous"] = ambiguous
        transaction["ambiguous_candidates"] = reliable if ambiguous else []
        transaction["proposal"] = None if ambiguous else (reliable[0] if reliable else None)
        transaction["candidates"] = candidates
        transaction["ineligible_matches"] = _ineligible_matches(transaction, all_invoices)
        pending.append(transaction)
    proposed = [item["proposal"] for item in pending if item["proposal"] and item["proposal"]["score"] >= 80]
    uncertain = [item["proposal"] for item in pending if item["proposal"] and item["proposal"]["score"] < 80]
    ambiguous = [item for item in pending if item["ambiguous"]]
    unmatched = [item for item in pending if not item["proposal"] and not item["ambiguous"]]
    return {"transactions": transactions, "pending": pending, "proposed": proposed,
            "uncertain": uncertain, "ambiguous": ambiguous, "unmatched": unmatched,
            "invoices": invoices, "batches": batches}
