"""Create the database tables and the first Root account.

Works with BOTH back-ends (chosen automatically, see routes/db.py):
  * Turso  - when TURSO_DATABASE and TURSO_API_TOKEN are set (environment or .env)
  * local SQLite file (database/taxdecipher.db) - otherwise

Usage (from the project folder):
    python database/init_db.py                  # asks for a password
    ADMIN_PASSWORD='...' python database/init_db.py

Safe to re-run: existing tables and users are left untouched.
(The website also creates the tables by itself on start-up - this script is
only needed to create the first admin account on an EMPTY database.
If you already have a taxdecipher.db, use database/migrate_to_turso.py instead.)
"""
import getpass
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent
sys.path.insert(0, str(PROJECT_DIR))

from werkzeug.security import generate_password_hash  # noqa: E402

from routes.db import connect, describe_backend, ensure_core_schema  # noqa: E402


def load_env_file():
    env_path = PROJECT_DIR / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env_file()

DB_PATH = os.environ.get("DATABASE_PATH", str(BASE_DIR / "taxdecipher.db"))
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "Siddharth")

print(f"Database: {describe_backend()}")
db = connect(DB_PATH)

try:
    ensure_core_schema(db)

    exists = db.execute(
        "SELECT 1 FROM users WHERE username = ?", (ADMIN_USERNAME,)
    ).fetchone()

    if exists:
        print(f"User '{ADMIN_USERNAME}' already exists - not changed.")
    else:
        password = os.environ.get("ADMIN_PASSWORD")
        if not password:
            password = getpass.getpass(
                f"Choose a password for '{ADMIN_USERNAME}' (min 8 chars): "
            )
        if len(password) < 8:
            sys.exit("Password must be at least 8 characters. Nothing was created.")

        role_id = db.execute(
            "SELECT role_id FROM roles WHERE role_name = 'Root'"
        ).fetchone()[0]

        db.execute(
            "INSERT INTO users (username, password_hash, role_id, is_active) "
            "VALUES (?, ?, ?, 1)",
            (ADMIN_USERNAME, generate_password_hash(password), role_id),
        )
        db.commit()
        print(f"Root account '{ADMIN_USERNAME}' created.")
finally:
    db.close()

print("Tax Decipher database is ready.")
