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

def ensure_default_admin():
    con=connect();row=con.execute("SELECT id FROM users WHERE username='admin'").fetchone()
    if not row:
        con.execute("INSERT INTO users(username,password_hash,role) VALUES(?,?,?)",('admin',hash_password('Aurelia-ChangeMe!'),'admin'));con.commit()
    con.close()

def authenticate(username,password):
    con=connect();row=con.execute("SELECT * FROM users WHERE username=? AND active=1",(username,)).fetchone();con.close()
    return dict(row) if row and verify_password(password,row['password_hash']) else None
