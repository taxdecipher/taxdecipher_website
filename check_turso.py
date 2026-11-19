"""Turso connection checker. Prints NO secrets.

Put this file in the project folder (next to app.py) and run:
    python check_turso.py
"""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# 1) Do Windows/system variables already exist? (they would override .env)
pre_url = os.environ.get("TURSO_DATABASE")
pre_tok = os.environ.get("TURSO_API_TOKEN")
if pre_url or pre_tok:
    print("WARNING: TURSO_* variables already exist in your Windows/system environment.")
    print("         The app uses THOSE instead of the .env file. Remove them or fix them.\n")

# 2) Read .env
env_path = HERE / ".env"
if not env_path.exists():
    sys.exit("PROBLEM: .env file not found next to this script.")
for line in env_path.read_text(encoding="utf-8-sig").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from routes.db import connect, turso_settings, TursoError  # noqa: E402

url, token = turso_settings()
print("URL host      :", url.split("://", 1)[-1] or "(EMPTY)")
print("Token length  :", len(token), "(a real database token is usually 150-400 characters)")
print("Token starts  :", token[:3] or "(EMPTY)", "(should be 'eyJ')")
print("Token has dots:", token.count("."), "(should be 2)")
print()

if "YAHAN" in url or "YAHAN" in token or not url or not token:
    sys.exit("PROBLEM: the placeholder text is still in .env. Put your real URL and token.")
if " " in token or " " in url:
    sys.exit("PROBLEM: there is a space inside the URL or token. Re-copy it.")

try:
    row = connect().execute("SELECT 1").fetchone()
    print("SUCCESS: connected to Turso. (SELECT 1 ->", row[0], ")")
    print("You can now run:  python database\\migrate_to_turso.py")
except TursoError as exc:
    print("FAILED:", exc)
    sys.exit(1)
