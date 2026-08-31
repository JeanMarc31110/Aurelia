import json
import re
import unicodedata

from app.db import connect


COMPANY_FIELDS = (
    "legal_name", "trade_name", "country", "address", "postal_code", "city",
    "vat_id", "nif", "siren", "siret", "iban", "bic", "email", "phone", "currency",
)


def _normalise(value):
    value = unicodedata.normalize("NFKD", value or "")
    folded = "".join(character for character in value if not unicodedata.combining(character))
    return re.sub(r"[^A-Z0-9]", "", folded.upper())


def get_active_company():
    con = connect()
    row = con.execute("SELECT * FROM companies WHERE active=1 ORDER BY id LIMIT 1").fetchone()
    con.close()
    return dict(row) if row else None


def save_active_company(values, aliases=None):
    cleaned = {field: (values.get(field) or "").strip() or None for field in COMPANY_FIELDS}
    cleaned["currency"] = (cleaned.get("currency") or "EUR").upper()
    if not cleaned["legal_name"]:
        raise ValueError("La raison sociale est obligatoire")
    aliases = [alias.strip() for alias in (aliases or []) if alias and alias.strip()]
    aliases_json = json.dumps(list(dict.fromkeys(aliases)), ensure_ascii=False)

    con = connect()
    existing = con.execute("SELECT id FROM companies WHERE active=1 ORDER BY id LIMIT 1").fetchone()
    if existing:
        assignments = ",".join(f"{field}=?" for field in COMPANY_FIELDS)
        con.execute(
            f"UPDATE companies SET {assignments},aliases_json=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            tuple(cleaned[field] for field in COMPANY_FIELDS) + (aliases_json, existing["id"]),
        )
        company_id = existing["id"]
    else:
        columns = ",".join(COMPANY_FIELDS)
        placeholders = ",".join("?" for _ in COMPANY_FIELDS)
        cursor = con.execute(
            f"INSERT INTO companies({columns},aliases_json,active) VALUES({placeholders},?,1)",
            tuple(cleaned[field] for field in COMPANY_FIELDS) + (aliases_json,),
        )
        company_id = cursor.lastrowid
    con.commit()
    row = con.execute("SELECT * FROM companies WHERE id=?", (company_id,)).fetchone()
    con.close()
    return dict(row)


def company_matches_party(company, party):
    if not company or not party:
        return False
    company_ids = {
        _normalise(company.get(field)) for field in ("vat_id", "nif", "siren", "siret")
        if company.get(field)
    }
    party_ids = {
        _normalise(party.get(field)) for field in ("vat_id", "vat_number", "nif", "siren", "siret", "legal_id")
        if party.get(field)
    }
    if company_ids and party_ids:
        return bool(company_ids.intersection(party_ids))

    aliases = []
    try:
        aliases = json.loads(company.get("aliases_json") or "[]")
    except (TypeError, json.JSONDecodeError):
        aliases = []
    company_names = {
        _normalise(name) for name in (company.get("legal_name"), company.get("trade_name"), *aliases)
        if name and len(_normalise(name)) >= 6
    }
    party_name = _normalise(party.get("name"))
    return bool(party_name and party_name in company_names)


def determine_invoice_direction(issuer, customer, company=None):
    if company is None:
        company = get_active_company()
    if not company:
        return "unknown", "no_active_company", None
    issuer_match = company_matches_party(company, issuer)
    customer_match = company_matches_party(company, customer)
    if issuer_match and not customer_match:
        return "sale", "active_company_matches_issuer", company["id"]
    if customer_match and not issuer_match:
        return "purchase", "active_company_matches_customer", company["id"]
    return "unknown", "no_reliable_company_match", company["id"]
