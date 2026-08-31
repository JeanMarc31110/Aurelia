from app.db import connect


def get_setting(key, default=None):
    con = connect()
    row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    con.close()
    return row["value"] if row else default


def set_setting(key, value):
    con = connect()
    con.execute(
        """INSERT INTO settings(key,value) VALUES(?,?)
           ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=CURRENT_TIMESTAMP""",
        (key, value),
    )
    con.commit()
    con.close()
