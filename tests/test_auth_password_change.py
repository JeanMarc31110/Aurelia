import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import auth


class PasswordChangeAtomicityTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.database=Path(self.temporary.name)/"auth.db"
        connection=sqlite3.connect(self.database)
        connection.execute("""CREATE TABLE users(
          id INTEGER PRIMARY KEY,username TEXT UNIQUE,password_hash TEXT NOT NULL,
          role TEXT NOT NULL,active INTEGER NOT NULL,must_change_password INTEGER NOT NULL)""")
        connection.execute(
            "INSERT INTO users VALUES(1,'admin',?,'admin',1,1)",
            (auth.hash_password("Temporary-Only!"),),
        )
        connection.commit();connection.close()

    def tearDown(self):
        self.temporary.cleanup()

    def connect(self):
        connection=sqlite3.connect(self.database)
        connection.row_factory=sqlite3.Row
        return connection

    def test_update_failure_rolls_back_hash_and_mandatory_change_flag(self):
        connection=sqlite3.connect(self.database)
        connection.execute("""CREATE TRIGGER fail_password_update
          BEFORE UPDATE OF password_hash ON users BEGIN SELECT RAISE(ABORT,'forced failure'); END""")
        connection.commit();connection.close()
        with patch.object(auth,"connect",self.connect):
            with self.assertRaisesRegex(ValueError,"n’a pas pu être enregistré"):
                auth.change_password("admin","Temporary-Only!","New-Password-Only!","New-Password-Only!")
        connection=sqlite3.connect(self.database)
        row=connection.execute(
            "SELECT password_hash,must_change_password FROM users WHERE username='admin'"
        ).fetchone()
        connection.close()
        self.assertTrue(auth.verify_password("Temporary-Only!",row[0]))
        self.assertFalse(auth.verify_password("New-Password-Only!",row[0]))
        self.assertEqual(row[1],1)


if __name__ == "__main__":
    unittest.main()
