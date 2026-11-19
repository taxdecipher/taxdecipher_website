"""One-time copy of your local SQLite database (taxdecipher.db) into Turso.

Run this ONCE, on your own computer, from the project folder:

    1. Put these two lines in your local .env file (never commit .env):
           TURSO_DATABASE=libsql://<your-db>.turso.io
           TURSO_API_TOKEN=<your database auth token>
    2. python database/migrate_to_turso.py
       (or: python database/migrate_to_turso.py path/to/other.db)

What it does
  * creates every table in Turso exactly like in your local file
  * copies all rows (users, posts, profile, settings ...) keeping the same ids
  * by default NEVER overwrites rows that already exist in Turso
    (add --overwrite if you want the local file to win)
  * the local file is only read, never modified
"""
import os
import sqlite3
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent
sys.path.insert(0, str(PROJECT_DIR))

from routes.db import TursoError, connect, using_turso  # noqa: E402

# Parents first, so foreign keys are happy.
TABLE_ORDER = [
    "roles", "users", "user_activity_logs", "posts",
    "professional_profile", "site_settings",
]


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


def main():
    load_env_file()
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    overwrite = "--overwrite" in sys.argv

    if not using_turso():
        sys.exit(
            "TURSO_DATABASE and TURSO_API_TOKEN are not set.\n"
            "Add them to your local .env file (or export them) and run again."
        )

    source_path = Path(args[0]) if args else BASE_DIR / "taxdecipher.db"
    if not source_path.exists():
        sys.exit(f"Local database not found: {source_path}")

    src = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row

    tables = {
        r["name"]: r["sql"]
        for r in src.execute(
            "SELECT name, sql FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    ordered = [t for t in TABLE_ORDER if t in tables] + [
        t for t in tables if t not in TABLE_ORDER
    ]

    dst = connect()
    verb = "INSERT OR REPLACE" if overwrite else "INSERT OR IGNORE"

    try:
        for name in ordered:
            ddl = tables[name].strip()
            if ddl.upper().startswith("CREATE TABLE ") and "IF NOT EXISTS" not in ddl[:40].upper():
                ddl = "CREATE TABLE IF NOT EXISTS " + ddl[len("CREATE TABLE "):]
            dst.execute(ddl)
            dst.commit()

            rows = src.execute(f'SELECT * FROM "{name}"').fetchall()
            if not rows:
                print(f"  {name:<22} 0 rows (nothing to copy)")
                continue

            columns = rows[0].keys()
            sql = (
                f'{verb} INTO "{name}" ('
                + ", ".join(f'"{c}"' for c in columns)
                + ") VALUES ("
                + ", ".join("?" for _ in columns)
                + ")"
            )
            copied = 0
            for row in rows:
                cur = dst.execute(sql, tuple(row))
                copied += 1 if cur.rowcount else 0
            dst.commit()

            skipped = len(rows) - copied
            note = f", {skipped} already existed (kept)" if skipped else ""
            print(f"  {name:<22} {copied} of {len(rows)} rows copied{note}")

        print("\nVerification (rows now in Turso):")
        for name in ordered:
            n = dst.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            print(f"  {name:<22} {n}")
        print("\nDone. Your Turso database now has your data.")
    except TursoError as exc:
        dst.rollback()
        sys.exit(f"\nTurso error: {exc}")
    finally:
        dst.close()
        src.close()


if __name__ == "__main__":
    main()
