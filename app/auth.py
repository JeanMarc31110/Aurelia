import os, base64, hashlib, hmac
from app.db import connect

_ITERATIONS = 210_000

def hash_password(password: str) -> str:
    salt=os.urandom(16)
    digest=hashlib.pbkdf2_hmac('sha256',password.encode('utf-8'),salt,_ITERATIONS)
    return 'pbkdf2_sha256$%d$%s$%s' % (_ITERATIONS,base64.b64encode(salt).decode(),base64.b64encode(digest).decode())

def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme,iters,salt_b64,digest_b64=encoded.split('$',3)
        if scheme!='pbkdf2_sha256': return False
        salt=base64.b64decode(salt_b64); expected=base64.b64decode(digest_b64)
        actual=hashlib.pbkdf2_hmac('sha256',password.encode('utf-8'),salt,int(iters))
        return hmac.compare_digest(actual,expected)
    except Exception:
        return False

def has_active_admin():
    con=connect();row=con.execute(
        "SELECT 1 FROM users WHERE role='admin' AND active=1 LIMIT 1"
    ).fetchone();con.close()
    return bool(row)


def create_admin(username, password):
    username=(username or "").strip()
    if not username:raise ValueError("Le nom d'utilisateur est obligatoire")
    if len(password or "") < 8:raise ValueError("Le mot de passe doit contenir au moins 8 caractères")
    con=connect()
    try:
        con.execute("INSERT INTO users(username,password_hash,role,active) VALUES(?,?,'admin',1)",
                    (username,hash_password(password)))
        con.commit()
    except Exception:
        con.rollback();raise
    finally:
        con.close()

def authenticate(username,password):
    con=connect();row=con.execute("SELECT * FROM users WHERE username=? AND active=1",(username,)).fetchone();con.close()
    return dict(row) if row and verify_password(password,row['password_hash']) else None


def change_password(username, current_password, new_password, confirmation):
    if len(new_password or "") < 8:raise ValueError("Le nouveau mot de passe doit contenir au moins 8 caractères")
    if new_password != confirmation:raise ValueError("Les mots de passe ne correspondent pas")
    con=connect()
    try:
        con.execute("BEGIN IMMEDIATE")
        user=con.execute(
            "SELECT id,password_hash FROM users WHERE username=? AND active=1",(username,)
        ).fetchone()
        if not user or not verify_password(current_password,user["password_hash"]):
            raise ValueError("Le mot de passe actuel est incorrect")
        encoded=hash_password(new_password)
        updated=con.execute(
            "UPDATE users SET password_hash=?,must_change_password=0 WHERE id=?",
            (encoded,user["id"]),
        )
        persisted=con.execute(
            "SELECT password_hash,must_change_password FROM users WHERE id=?",(user["id"],)
        ).fetchone()
        if updated.rowcount != 1 or not persisted or persisted["must_change_password"] != 0 \
                or not verify_password(new_password,persisted["password_hash"]):
            raise RuntimeError("password_update_verification_failed")
        con.commit()
    except ValueError:
        con.rollback();raise
    except Exception:
        con.rollback()
        raise ValueError("Le mot de passe n’a pas pu être enregistré. Réessayez.") from None
    finally:
        con.close()
