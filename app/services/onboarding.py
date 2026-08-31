from app.auth import hash_password
from app.db import connect


def setup_required():
    connection = connect()
    try:
        admin = connection.execute(
            "SELECT 1 FROM users WHERE role='admin' AND active=1 LIMIT 1"
        ).fetchone()
        company = connection.execute(
            "SELECT 1 FROM companies WHERE active=1 LIMIT 1"
        ).fetchone()
        return not admin or not company
    finally:
        connection.close()


def create_initial_setup(username, password, password_confirmation, legal_name,
                         tax_id, country, currency="EUR"):
    values = {
        "username": (username or "").strip(), "legal_name": (legal_name or "").strip(),
        "tax_id": (tax_id or "").strip(), "country": (country or "").strip(),
        "currency": (currency or "EUR").strip().upper(),
    }
    if not values["username"]:raise ValueError("Le nom d'utilisateur est obligatoire")
    if len(password or "") < 8:raise ValueError("Le mot de passe doit contenir au moins 8 caractères")
    if password != password_confirmation:raise ValueError("Les mots de passe ne correspondent pas")
    if not values["legal_name"]:raise ValueError("La raison sociale est obligatoire")
    if not values["tax_id"]:raise ValueError("L'identifiant fiscal est obligatoire")
    if not values["country"]:raise ValueError("Le pays est obligatoire")
    if len(values["currency"]) != 3 or not values["currency"].isalpha():
        raise ValueError("La devise doit être un code ISO sur 3 lettres")

    connection = connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        if connection.execute("SELECT 1 FROM users WHERE role='admin' AND active=1 LIMIT 1").fetchone():
            raise ValueError("Un administrateur existe déjà")
        if connection.execute("SELECT 1 FROM companies WHERE active=1 LIMIT 1").fetchone():
            raise ValueError("Une société active existe déjà")
        connection.execute(
            "INSERT INTO users(username,password_hash,role,active) VALUES(?,?,'admin',1)",
            (values["username"], hash_password(password)),
        )
        connection.execute(
            """INSERT INTO companies(legal_name,country,vat_id,currency,active)
               VALUES(?,?,?,?,1)""",
            (values["legal_name"],values["country"],values["tax_id"],values["currency"]),
        )
        connection.commit()
    except Exception:
        connection.rollback();raise
    finally:
        connection.close()
    return values
