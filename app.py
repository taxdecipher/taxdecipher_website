"""Tax Decipher - Flask application entry point.

Configuration is read from environment variables (see .env.example).
Run locally:       python app.py
Run in production: gunicorn wsgi:app
"""
import logging
import os
import re
import secrets
from datetime import datetime
from html import unescape
from pathlib import Path

from flask import Flask, abort, jsonify, render_template, request, session
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from routes.auth import configure_auth, get_db
from routes.db import describe_backend, ensure_core_schema, using_turso
from root.contact import contact_form
from root.content import init_posts_table, root_content
from root.dashboard import root_dashboard
from root.profile import init_profile_table, root_profile
from root.public import public
from root.settings import root_settings
from root.siddharth import siddharth_public

BASE_DIR = Path(__file__).resolve().parent


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def load_env_file():
    """Tiny .env loader so no extra dependency is needed."""
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def get_secret_key():
    """Use SECRET_KEY from the environment; otherwise persist a random one."""
    key = os.environ.get("SECRET_KEY")
    if key and key != "YOUR_LONG_RANDOM_SECRET_KEY":
        return key

    logging.getLogger(__name__).warning(
        "SECRET_KEY is not set. Using a generated key stored in "
        "instance/secret_key. Set SECRET_KEY in your environment for production."
    )

    key_file = BASE_DIR / "instance" / "secret_key"
    key_file.parent.mkdir(exist_ok=True)
    if key_file.exists():
        return key_file.read_text().strip()

    key = secrets.token_hex(32)
    key_file.write_text(key)
    try:
        key_file.chmod(0o600)
    except OSError:
        pass
    return key


def create_app():
    load_env_file()
    debug = env_bool("FLASK_DEBUG", False)

    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=get_secret_key(),
        DATABASE=os.environ.get(
            "DATABASE_PATH", str(BASE_DIR / "database" / "taxdecipher.db")
        ),
        # Secure cookies need HTTPS. Set COOKIE_SECURE=false for plain-http
        # local development, otherwise the browser drops the login cookie.
        SESSION_COOKIE_SECURE=env_bool("COOKIE_SECURE", not debug),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        MAX_CONTENT_LENGTH=12 * 1024 * 1024,
        SITE_NAME="Tax Decipher",
        SITE_URL=os.environ.get("SITE_URL", "").rstrip("/"),
        CONTACT_EMAIL=os.environ.get("CONTACT_EMAIL", ""),
    )

    if env_bool("TRUSTED_PROXY", False):
        # Behind Nginx / a PaaS router: trust one proxy hop.
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    configure_auth(app)
    app.register_blueprint(public)
    app.register_blueprint(root_dashboard)
    app.register_blueprint(root_content)
    app.register_blueprint(root_profile)
    app.register_blueprint(root_settings)
    app.register_blueprint(siddharth_public)
    app.register_blueprint(contact_form)

    # Database: Turso when TURSO_DATABASE + TURSO_API_TOKEN are set, otherwise
    # the local SQLite file (see routes/db.py).
    app.logger.info("Database backend: %s", describe_backend())
    if os.environ.get("RENDER") and not using_turso():
        app.logger.warning(
            "Running on Render WITHOUT Turso: data is stored on the temporary disk "
            "and will be LOST on every deploy. Link the 'taxdecipher' environment "
            "group (TURSO_DATABASE, TURSO_API_TOKEN) to this service."
        )

    with app.app_context():
        try:
            with get_db() as db:
                ensure_core_schema(db)
            init_posts_table()
            init_profile_table()
        except Exception:
            # Keep the site booting so the real error is visible in the logs.
            app.logger.exception(
                "Database start-up check failed - check TURSO_DATABASE / TURSO_API_TOKEN."
            )

    register_security(app)
    register_template_helpers(app)
    register_error_handlers(app)
    return app


# ----------------------------------------------------------------------
# CSRF protection (no extra dependency)
# ----------------------------------------------------------------------
def csrf_token():
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


def register_security(app):

    @app.before_request
    def protect_against_csrf():
        if request.method in {"GET", "HEAD", "OPTIONS"}:
            return None

        expected = session.get("_csrf_token")
        supplied = (
            request.form.get("csrf_token")
            or request.headers.get("X-CSRFToken")
        )

        if (
            not expected
            or not supplied
            or not secrets.compare_digest(str(expected), str(supplied))
        ):
            abort(400, description=(
                "Your session expired. Please go back, refresh the page "
                "and try again."
            ))
        return None

    @app.after_request
    def add_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault(
            "Referrer-Policy", "strict-origin-when-cross-origin"
        )
        response.headers.setdefault(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
        )
        if request.is_secure:
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )

        path = request.path
        if path.startswith("/root") or path in {"/login", "/logout"}:
            # Never cache logged-in pages in browsers or proxies.
            response.headers["Cache-Control"] = (
                "no-store, no-cache, must-revalidate, max-age=0"
            )
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        elif path.startswith("/static/"):
            response.headers.setdefault("Cache-Control", "public, max-age=604800")
        elif (
            request.method == "GET"
            and response.status_code == 200
            and not session.get("user_id")
        ):
            response.headers.setdefault("Cache-Control", "public, max-age=300")
        return response


# ----------------------------------------------------------------------
# Jinja helpers
# ----------------------------------------------------------------------
def register_template_helpers(app):

    app.jinja_env.globals["csrf_token"] = csrf_token

    def _profile_row():
        """Return (profile dict, status) of the single professional profile."""
        import json
        from routes.auth import get_db
        try:
            with get_db() as db:
                row = db.execute(
                    "SELECT profile_data, status FROM professional_profile WHERE id = 1 LIMIT 1"
                ).fetchone()
            if row:
                return json.loads(row["profile_data"]) or {}, row["status"]
        except Exception:
            pass
        return {}, None

    def _footer_contact():
        """Contact details shown in the public footer (from the published profile)."""
        if not request or not (request.endpoint or "").startswith(("public.", "siddharth_public.")):
            return {}
        data, status = _profile_row()
        if status != "published":
            return {}
        return {
            "email": data.get("email", ""),
            "phone": data.get("phone", ""),
            "linkedin": data.get("linkedin", ""),
            "instagram": data.get("instagram", ""),
            "website": data.get("website", ""),
        }

    def _admin_user():
        """Name, role label and photo for the top-right corner of admin pages."""
        if not session.get("user_id"):
            return {}
        data, _status = _profile_row()
        username = session.get("username") or ""
        name = (data.get("full_name") or "").strip() or username or "Root"
        return {
            "name": name,
            "role": "Root User",
            "photo": data.get("profile_photo", ""),
            "initial": (name[:1] or "R").upper(),
        }

    @app.context_processor
    def inject_globals():
        year = datetime.now().year
        return {
            "site_name": app.config["SITE_NAME"],
            "current_year": year,
            "now_year": year,
            "contact_email": app.config["CONTACT_EMAIL"],
            "footer_contact": _footer_contact(),
            "admin_user": _admin_user(),
        }

    @app.template_filter("nice_date")
    def nice_date(value):
        """'2026-11-14 16:55:29' -> '14 Nov 2026'."""
        if not value:
            return ""
        try:
            parsed = datetime.fromisoformat(str(value)[:19].replace(" ", "T"))
        except ValueError:
            return str(value)
        return f"{parsed.day} {parsed.strftime('%b %Y')}"

    @app.template_filter("month_year")
    def month_year(value):
        """'2024-05' -> 'May 2024' (anything else is shown as typed)."""
        try:
            parsed = datetime.strptime(str(value), "%Y-%m")
        except ValueError:
            return str(value or "")
        return parsed.strftime("%b %Y")

    @app.template_filter("iso_date")
    def iso_date(value):
        if not value:
            return ""
        return str(value)[:19].replace(" ", "T")

    @app.template_filter("reading_time")
    def reading_time(html):
        words = len(re.sub(r"<[^>]+>", " ", html or "").split())
        return max(1, round(words / 200))

    @app.template_filter("excerpt")
    def excerpt(html, length=200):
        text = unescape(re.sub(r"<[^>]+>", " ", html or ""))
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) <= length:
            return text
        return text[:length].rsplit(" ", 1)[0].rstrip(",.;:") + "…"


# ----------------------------------------------------------------------
# Error pages
# ----------------------------------------------------------------------
ERROR_COPY = {
    400: ("Bad Request", "We could not process that request. Please go back, refresh the page and try again."),
    403: ("Access Denied", "You do not have permission to open this page."),
    404: ("Page Not Found", "Sorry, the page you are looking for does not exist or may have been moved."),
    405: ("Method Not Allowed", "That action is not allowed on this page."),
    413: ("File Too Large", "The file you tried to upload is too large."),
    429: ("Too Many Requests", "Please wait a little while and try again."),
    500: ("Something Went Wrong", "An unexpected error occurred on our side. Please try again in a few minutes."),
}


def register_error_handlers(app):

    def render_error(code, description=None):
        title, message = ERROR_COPY.get(
            code, ("Something Went Wrong", "An unexpected error occurred.")
        )
        if code == 400 and description and "session expired" in description:
            message = description

        if (
            request.path.startswith("/root/upload-image")
            or request.accept_mimetypes.best == "application/json"
        ):
            return jsonify(success=False, error=message), code

        return render_template(
            "error.html",
            error_code=code,
            error_title=title,
            error_message=message,
            page_title=f"{title} | Tax Decipher",
            noindex=True,
        ), code

    @app.errorhandler(HTTPException)
    def handle_http(error):
        return render_error(error.code or 500, getattr(error, "description", None))

    @app.errorhandler(Exception)
    def handle_unexpected(error):
        app.logger.exception("Unhandled exception")
        return render_error(500)


app = create_app()

if __name__ == "__main__":
    app.run(
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", 5000)),
        debug=env_bool("FLASK_DEBUG", False),
    )
