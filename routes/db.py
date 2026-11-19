"""Database layer for Tax Decipher.

Two back-ends behind ONE small API (``connect()``):

* **Turso** (libSQL, hosted)  - used automatically when the environment
  variables ``TURSO_DATABASE`` (database URL) and ``TURSO_API_TOKEN``
  (database auth token) are set.  This is what production (Render) uses.
* **Local SQLite file**       - used when those variables are missing, so the
  project still runs on your own computer with ``database/taxdecipher.db``.

The Turso part talks to Turso's official HTTP API (the "Hrana over HTTP"
protocol, ``POST <url>/v2/pipeline``) with plain ``requests``.  There is no
native / compiled package to install, so the build on Render cannot fail
because of it.

The connection object behaves like ``sqlite3`` (``execute``, ``fetchone``,
``fetchall``, ``rowcount``, ``lastrowid``, ``commit``, ``rollback``,
``row["column"]``) and every error is a subclass of ``sqlite3.Error`` - so
all existing routes keep working unchanged.

SECURITY: the Turso token is only ever used here, on the server.  It is never
sent to the browser.  The front-end does its CRUD by calling the Flask routes,
and those routes talk to Turso.
"""
import base64
import logging
import os
import re
import sqlite3
import threading
import time

log = logging.getLogger(__name__)

TURSO_URL_VARS = ("TURSO_DATABASE", "TURSO_DATABASE_URL")
TURSO_TOKEN_VARS = ("TURSO_API_TOKEN", "TURSO_AUTH_TOKEN")

CONNECT_TIMEOUT = 10      # seconds to open the connection
READ_TIMEOUT = 30         # seconds to wait for Turso's answer
MAX_RETRIES = 2           # extra attempts for requests that are safe to repeat


# ----------------------------------------------------------------------
# Errors  (all are sqlite3.Error so ``except sqlite3.Error`` still works)
# ----------------------------------------------------------------------
class TursoError(sqlite3.Error):
    """Generic Turso / network failure."""


class TursoAuthError(TursoError):
    """Turso rejected the token or the URL."""


class TursoIntegrityError(TursoError, sqlite3.IntegrityError):
    """UNIQUE / FOREIGN KEY / CHECK / NOT NULL violation."""


# ----------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------
def _first_env(names):
    for name in names:
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip().strip('"').strip("'")
    return ""


def _normalise_url(raw):
    """libsql://x.turso.io  ->  https://x.turso.io"""
    url = (raw or "").strip().rstrip("/")
    if not url:
        return ""
    for prefix in ("libsql://", "wss://", "ws://"):
        if url.startswith(prefix):
            url = "https://" + url[len(prefix):]
            break
    if "://" not in url:
        url = "https://" + url
    return url.split("?", 1)[0].rstrip("/")


def turso_settings():
    """Return (url, token); either may be '' when Turso is not configured."""
    return _normalise_url(_first_env(TURSO_URL_VARS)), _first_env(TURSO_TOKEN_VARS)


def using_turso():
    url, token = turso_settings()
    return bool(url and token)


def describe_backend():
    """Short, secret-free text for the start-up log."""
    if using_turso():
        host = turso_settings()[0].split("://", 1)[-1]
        return f"Turso ({host})"
    return "local SQLite file"


# ----------------------------------------------------------------------
# Row / Cursor  (mimic sqlite3.Row and sqlite3.Cursor)
# ----------------------------------------------------------------------
class Row:
    """Read-only row: ``row[0]``, ``row["name"]``, ``dict(row)``, iteration."""

    __slots__ = ("_cols", "_vals", "_index")

    def __init__(self, cols, vals, index):
        self._cols = cols
        self._vals = vals
        self._index = index

    def keys(self):
        return list(self._cols)

    def get(self, key, default=None):
        try:
            return self[key]
        except (KeyError, IndexError):
            return default

    def __getitem__(self, key):
        if isinstance(key, (int, slice)):
            return self._vals[key]
        try:
            return self._vals[self._index[str(key).lower()]]
        except KeyError:
            raise IndexError(f"No item with that key: {key!r}") from None

    def __iter__(self):
        return iter(self._vals)

    def __len__(self):
        return len(self._vals)

    def __eq__(self, other):
        if isinstance(other, Row):
            return self._vals == other._vals and self._cols == other._cols
        return NotImplemented

    def __hash__(self):
        return hash((self._cols, self._vals))

    def __repr__(self):
        return "<Row " + ", ".join(
            f"{c}={v!r}" for c, v in zip(self._cols, self._vals)
        ) + ">"


class Cursor:
    def __init__(self, rows=None, description=None, rowcount=-1, lastrowid=None):
        self._rows = rows or []
        self._pos = 0
        self.description = description
        self.rowcount = rowcount
        self.lastrowid = lastrowid
        self.arraysize = 1

    def fetchone(self):
        if self._pos >= len(self._rows):
            return None
        row = self._rows[self._pos]
        self._pos += 1
        return row

    def fetchmany(self, size=None):
        size = size or self.arraysize
        chunk = self._rows[self._pos:self._pos + size]
        self._pos += len(chunk)
        return chunk

    def fetchall(self):
        chunk = self._rows[self._pos:]
        self._pos = len(self._rows)
        return chunk

    def __iter__(self):
        return iter(self.fetchall())

    def close(self):
        pass


# ----------------------------------------------------------------------
# Value conversion (Python <-> Hrana JSON)
# ----------------------------------------------------------------------
def _encode(value):
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "integer", "value": str(int(value))}
    if isinstance(value, int):
        return {"type": "integer", "value": str(value)}
    if isinstance(value, float):
        return {"type": "float", "value": value}
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"type": "blob", "base64": base64.b64encode(bytes(value)).decode("ascii")}
    return {"type": "text", "value": str(value)}


def _decode(cell):
    kind = cell.get("type")
    if kind == "null":
        return None
    if kind == "integer":
        return int(cell["value"])
    if kind == "float":
        return float(cell["value"])
    if kind == "blob":
        data = cell.get("base64", "")
        return base64.b64decode(data + "=" * (-len(data) % 4))
    return cell.get("value")


def _statement(sql, params=None):
    stmt = {"sql": sql, "want_rows": True}
    if params is None:
        params = ()
    if isinstance(params, dict):
        stmt["named_args"] = [
            {"name": str(k).lstrip(":@$"), "value": _encode(v)}
            for k, v in params.items()
        ]
    else:
        stmt["args"] = [_encode(v) for v in params]
    return {"type": "execute", "stmt": stmt}


_WRITE_WORDS = re.compile(r"\b(insert|update|delete|replace)\b", re.I)


def _is_read_only(sql):
    """True for statements that cannot change data (safe to run outside a transaction)."""
    text = sql.lstrip().lower()
    if text.startswith(("select", "explain", "values")):
        return True
    if text.startswith("with"):
        return not _WRITE_WORDS.search(text)
    if text.startswith("pragma"):
        return "=" not in text
    return False


# ----------------------------------------------------------------------
# HTTP session (one per thread -> safe with gunicorn threads)
# ----------------------------------------------------------------------
_local = threading.local()


def _http():
    import requests  # imported lazily: not needed for local SQLite mode

    session = getattr(_local, "session", None)
    if session is None:
        session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=4)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        _local.session = session
    return session


# ----------------------------------------------------------------------
# Turso connection
# ----------------------------------------------------------------------
class TursoConnection:
    """sqlite3-like connection to a Turso database.

    Reads run as single stand-alone requests.  The first write opens a
    transaction (``BEGIN IMMEDIATE``) on a Turso "stream"; everything after
    that stays in the same transaction until ``commit()`` / ``rollback()``.
    """

    row_factory = None  # accepted for sqlite3 compatibility, rows are always Row

    def __init__(self, url, token):
        self._url = url
        self._token = token
        self._baton = None      # identifies the open Turso stream
        self._in_tx = False
        self._closed = False

    # -- low level ------------------------------------------------------
    def _post(self, requests_list, use_baton=True):
        payload = {"requests": requests_list}
        if use_baton and self._baton:
            payload["baton"] = self._baton

        safe_to_retry = not (use_baton and self._baton) and not any(
            r.get("stmt", {}).get("sql", "").strip().upper().startswith(("COMMIT", "END"))
            for r in requests_list if r.get("type") == "execute"
        )
        attempts = 1 + (MAX_RETRIES if safe_to_retry else 0)
        last_error = None

        for attempt in range(attempts):
            try:
                response = _http().post(
                    f"{self._url}/v2/pipeline",
                    json=payload,
                    headers={"Authorization": f"Bearer {self._token}"},
                    timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
                )
            except Exception as exc:  # network / timeout
                last_error = TursoError(f"Could not reach Turso: {exc}")
                if attempt + 1 < attempts:
                    time.sleep(0.3 * (attempt + 1))
                    continue
                raise last_error from exc

            if response.status_code in (502, 503, 504) and attempt + 1 < attempts:
                time.sleep(0.3 * (attempt + 1))
                continue

            if response.status_code in (401, 403):
                raise TursoAuthError(
                    "Turso rejected the credentials (HTTP "
                    f"{response.status_code}). Check TURSO_API_TOKEN: it must be a "
                    "DATABASE auth token for this database (turso db tokens create "
                    "<db-name>), and TURSO_DATABASE must be that database's URL."
                )
            if response.status_code >= 400:
                detail = ""
                try:
                    body = response.json()
                    detail = body.get("error") or body.get("message") or ""
                except Exception:
                    detail = response.text[:200]
                raise TursoError(f"Turso returned HTTP {response.status_code}: {detail}")

            data = response.json()
            self._baton = data.get("baton")
            if data.get("base_url"):
                self._url = data["base_url"].rstrip("/")
            return data.get("results", [])

        raise last_error or TursoError("Turso request failed")

    @staticmethod
    def _raise_if_error(result):
        if result.get("type") == "error":
            err = result.get("error", {})
            message = err.get("message", "Unknown Turso error")
            code = str(err.get("code", ""))
            if "CONSTRAINT" in code.upper() or "constraint failed" in message.lower():
                raise TursoIntegrityError(message)
            raise TursoError(message)

    @staticmethod
    def _to_cursor(result):
        body = result["response"]["result"]
        cols = tuple(c.get("name") or "" for c in body.get("cols", []))
        index = {}
        for i, name in enumerate(cols):
            index.setdefault(name.lower(), i)
        rows = [
            Row(cols, tuple(_decode(cell) for cell in raw), index)
            for raw in body.get("rows", [])
        ]
        last_id = body.get("last_insert_rowid")
        return Cursor(
            rows=rows,
            description=tuple((name, None, None, None, None, None, None) for name in cols) or None,
            rowcount=body.get("affected_row_count", -1),
            lastrowid=int(last_id) if last_id not in (None, "") else None,
        )

    def _abandon_stream(self):
        """Best-effort cleanup after an error."""
        if self._baton:
            try:
                self._post([{"type": "close"}])
            except Exception:
                pass
        self._baton = None
        self._in_tx = False

    # -- sqlite3-style API ---------------------------------------------
    def execute(self, sql, params=()):
        if self._closed:
            raise TursoError("Cannot operate on a closed database.")

        sql = sql.strip()
        needs_tx = self._in_tx or not _is_read_only(sql)
        requests_list = []

        if not needs_tx:
            # Stand-alone read: one request, stream closed straight away.
            requests_list = [_statement(sql, params), {"type": "close"}]
            results = self._post(requests_list, use_baton=False)
            self._baton = None
            self._raise_if_error(results[0])
            return self._to_cursor(results[0])

        opened_here = False
        if not self._in_tx:
            requests_list.append(_statement("BEGIN IMMEDIATE"))
            opened_here = True
        requests_list.append(_statement(sql, params))

        try:
            results = self._post(requests_list)
        except TursoError:
            self._baton = None
            self._in_tx = False
            raise

        if opened_here:
            self._in_tx = True
        try:
            for result in results:
                self._raise_if_error(result)
        except TursoError:
            self._abandon_stream()
            raise
        return self._to_cursor(results[-1])

    def executemany(self, sql, seq_of_params):
        total = 0
        last = Cursor()
        for params in seq_of_params:
            last = self.execute(sql, params)
            total += max(last.rowcount, 0)
        last.rowcount = total
        return last

    def executescript(self, script):
        """Run several ;-separated statements (used for schema set-up)."""
        if self._in_tx:
            self.commit()
        results = self._post(
            [{"type": "sequence", "sql": script}, {"type": "close"}], use_baton=False
        )
        self._baton = None
        self._raise_if_error(results[0])
        return Cursor()

    def commit(self):
        if not self._in_tx:
            return
        try:
            results = self._post([_statement("COMMIT"), {"type": "close"}])
            self._raise_if_error(results[0])
        finally:
            self._baton = None
            self._in_tx = False

    def rollback(self):
        if not self._in_tx:
            return
        try:
            self._post([_statement("ROLLBACK"), {"type": "close"}])
        except Exception:
            log.warning("Turso rollback request failed (the transaction is discarded anyway).")
        finally:
            self._baton = None
            self._in_tx = False

    def close(self):
        if self._closed:
            return
        if self._in_tx:
            self.rollback()
        self._closed = True

    @property
    def in_transaction(self):
        return self._in_tx

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        return False


# ----------------------------------------------------------------------
# Public entry point
# ----------------------------------------------------------------------
def connect(local_path=None):
    """Open a connection to Turso (if configured) or to the local SQLite file."""
    url, token = turso_settings()
    if url and token:
        return TursoConnection(url, token)

    if not local_path:
        raise TursoError(
            "No database configured. Set TURSO_DATABASE and TURSO_API_TOKEN "
            "(production) or DATABASE_PATH (local)."
        )

    conn = sqlite3.connect(local_path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


# ----------------------------------------------------------------------
# Core schema (shared by the app start-up and database/init_db.py)
# ----------------------------------------------------------------------
CORE_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS roles (
        role_id INTEGER PRIMARY KEY AUTOINCREMENT,
        role_name TEXT NOT NULL UNIQUE,
        description TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS users (
        slno INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        role_id INTEGER,
        is_active INTEGER NOT NULL DEFAULT 1,
        session_token TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (role_id) REFERENCES roles(role_id)
    )""",
    """CREATE TABLE IF NOT EXISTS user_activity_logs (
        log_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        performed_by INTEGER,
        action TEXT NOT NULL,
        old_value TEXT,
        new_value TEXT,
        reason TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(slno),
        FOREIGN KEY (performed_by) REFERENCES users(slno)
    )""",
    """CREATE TABLE IF NOT EXISTS site_settings (
        key TEXT PRIMARY KEY,
        value TEXT,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""",
)

DEFAULT_ROLES = (
    ("Root", "System owner with full access"),
    ("Manager", "Manages authors and users"),
    ("Author", "Creates and submits content"),
    ("User", "Standard registered user"),
)


def ensure_core_schema(db):
    """Create roles / users / logs / settings tables and the four default roles.

    Safe to run on every start: existing tables and data are left untouched.
    """
    for statement in CORE_SCHEMA:
        db.execute(statement)
    for name, description in DEFAULT_ROLES:
        db.execute(
            "INSERT OR IGNORE INTO roles (role_name, description) VALUES (?, ?)",
            (name, description),
        )
    db.commit()
