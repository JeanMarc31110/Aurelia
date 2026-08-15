from passlib.context import CryptContext
from app.db import connect

pwd_context=CryptContext(schemes=["bcrypt"],deprecated="auto")

def hash_password(p): return pwd_context.hash(p)
def verify_password(p,h): return pwd_context.verify(p,h)

def ensure_default_admin():
    con=connect()
    row=con.execute("SELECT id FROM users WHERE username='admin'").fetchone()
    if not row:
        con.execute("INSERT INTO users(username,password_hash,role) VALUES(?,?,?)",
                    ("admin",hash_password("Aurelia-ChangeMe!"),"admin"))
        con.commit()
    con.close()

def authenticate(username,password):
    con=connect()
    row=con.execute("SELECT * FROM users WHERE username=? AND active=1",(username,)).fetchone()
    con.close()
    return dict(row) if row and verify_password(password,row["password_hash"]) else None
