
import sqlite3
import secrets
import time
import hmac

from contextlib import contextmanager
from datetime import timedelta
from functools import wraps

from flask import (
    Blueprint, current_app, render_template,
    request, redirect, url_for,
    session, flash
)

from werkzeug.security import check_password_hash

from routes.db import connect


auth = Blueprint("auth", __name__)

IDLE_TIMEOUT = 30 * 60
MAX_SESSION_TIME = 8 * 60 * 60


# ROLE-WISE DASHBOARD URLS
ROLE_DASHBOARDS = {
    "root": "/root/dashboard",
    "manager": "/manager/dashboard",
    "author": "/author/dashboard",
    "user": "/user/dashboard"
}


# DATABASE CONNECTION
@contextmanager
def get_db():
    """Database connection that commits on success and rolls back on error.

    Uses Turso when TURSO_DATABASE + TURSO_API_TOKEN are set (production),
    otherwise the local SQLite file (local development). Both behave the same
    way for the rest of the application: ``db.execute(...)``, ``fetchone()``,
    ``fetchall()``, ``rowcount``, ``row["column"]``.
    """
    db = connect(current_app.config["DATABASE"])

    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# GET USER ROLE FROM DATABASE
def get_user_role(user_id):

    with get_db() as db:

        user = db.execute(
            """
            SELECT roles.role_name
            FROM users
            JOIN roles ON users.role_id = roles.role_id
            WHERE users.slno = ?
            """,
            (user_id,)
        ).fetchone()

    return user["role_name"] if user else None


# GET ROLE DASHBOARD
def get_dashboard_url(role_name):

    if not role_name:
        return url_for("auth.login")

    return ROLE_DASHBOARDS.get(
        role_name.strip().lower(),
        url_for("auth.login")
    )


# LOGIN REQUIRED
def login_required(function):

    @wraps(function)
    def wrapper(*args, **kwargs):

        user_id = session.get("user_id")
        token = session.get("session_token")

        if not user_id or not token:
            session.clear()
            return redirect(url_for("auth.login"))

        now = int(time.time())

        last_activity = session.get("last_activity", 0)
        login_time = session.get("login_time", 0)

        # SESSION EXPIRY
        if (
            now - last_activity > IDLE_TIMEOUT
            or now - login_time > MAX_SESSION_TIME
        ):

            session.clear()

            flash("Your session expired. Please login again.", "warning")

            return redirect(url_for("auth.login"))

        # VERIFY SESSION TOKEN AND ACCOUNT STATUS
        try:

            with get_db() as db:

                user = db.execute(
                    """
                    SELECT session_token, is_active
                    FROM users
                    WHERE slno = ?
                    """,
                    (user_id,)
                ).fetchone()

        except sqlite3.Error:

            current_app.logger.exception("Session verification failed")

            session.clear()

            flash("Session verification failed.", "danger")

            return redirect(url_for("auth.login"))

        if (
            user is None
            or not user["is_active"]
            or not user["session_token"]
            or not hmac.compare_digest(
                str(user["session_token"]), str(token)
            )
        ):

            session.clear()

            flash("Your session is no longer valid.", "warning")

            return redirect(url_for("auth.login"))

        session["last_activity"] = now
        session.permanent = True

        return function(*args, **kwargs)

    return wrapper


# ROLE REQUIRED
def role_required(required_role):

    def decorator(function):

        @wraps(function)
        @login_required
        def wrapper(*args, **kwargs):

            try:
                actual_role = get_user_role(session["user_id"])

            except sqlite3.Error:

                current_app.logger.exception("Role verification failed")

                session.clear()

                flash("Unable to verify your account role.", "danger")

                return redirect(url_for("auth.login"))

            if not actual_role:

                session.clear()

                return redirect(url_for("auth.login"))

            # CHECK ROLE PERMISSION
            if actual_role.strip().lower() != required_role.strip().lower():

                flash("You are not authorized to access that page.", "danger")

                return redirect(get_dashboard_url(actual_role))

            return function(*args, **kwargs)

        return wrapper

    return decorator


# LOGIN THROTTLING (in-memory, per IP + username)
MAX_FAILED_LOGINS = 5
LOCKOUT_SECONDS = 15 * 60
_failed_logins = {}


def _throttle_key(username):
    return (request.remote_addr or "unknown", (username or "").lower())


def _is_locked_out(username):
    key = _throttle_key(username)
    entry = _failed_logins.get(key)
    if not entry:
        return False
    count, first_failure = entry
    if time.time() - first_failure > LOCKOUT_SECONDS:
        _failed_logins.pop(key, None)
        return False
    return count >= MAX_FAILED_LOGINS


def _register_failed_login(username):
    key = _throttle_key(username)
    count, first = _failed_logins.get(key, (0, time.time()))
    if time.time() - first > LOCKOUT_SECONDS:
        count, first = 0, time.time()
    _failed_logins[key] = (count + 1, first)


def _clear_failed_logins(username):
    _failed_logins.pop(_throttle_key(username), None)


# LOGIN
@auth.route("/login", methods=["GET", "POST"])
def login():

    # ALREADY LOGGED IN? REDIRECT ACCORDING TO ROLE.
    if session.get("user_id"):

        try:
            role = get_user_role(session["user_id"])

            if role:
                return redirect(get_dashboard_url(role))

        except sqlite3.Error:
            current_app.logger.exception("Could not retrieve user role")

        session.clear()

    if request.method == "POST":

        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if not username or not password:

            flash("Please enter username and password.", "danger")

            return render_template("users/login.html")

        if _is_locked_out(username):

            flash(
                "Too many failed attempts. Please try again in 15 minutes.",
                "danger"
            )

            return render_template("users/login.html"), 429

        try:

            with get_db() as db:

                user = db.execute(
                    """
                    SELECT
                        users.slno,
                        users.username,
                        users.password_hash,
                        users.is_active,
                        roles.role_name

                    FROM users

                    JOIN roles
                    ON users.role_id = roles.role_id

                    WHERE users.username = ?

                    LIMIT 1
                    """,
                    (username,)
                ).fetchone()

                if (
                    user is None
                    or not user["is_active"]
                    or not user["password_hash"]
                    or not check_password_hash(
                        user["password_hash"], password
                    )
                ):

                    _register_failed_login(username)

                    flash("Invalid username or password.", "danger")

                    return render_template("users/login.html"), 401

                role = user["role_name"]

                if role.strip().lower() not in ROLE_DASHBOARDS:

                    flash("Your account role is not configured.", "danger")

                    return render_template("users/login.html")

                # CREATE NEW SESSION TOKEN
                token = secrets.token_urlsafe(32)

                db.execute(
                    """
                    UPDATE users
                    SET session_token = ?
                    WHERE slno = ?
                    """,
                    (token, user["slno"])
                )

                db.commit()

        except sqlite3.Error:

            current_app.logger.exception("Login database error")

            flash("Unable to login. Please try again.", "danger")

            return render_template("users/login.html")

        _clear_failed_logins(username)

        # CREATE NEW FLASK SESSION
        session.clear()

        now = int(time.time())

        session.permanent = True

        session["user_id"] = user["slno"]
        session["username"] = user["username"]
        session["role"] = role
        session["session_token"] = token
        session["login_time"] = now
        session["last_activity"] = now

        # ROLE-BASED REDIRECTION
        return redirect(get_dashboard_url(role))

    return render_template("users/login.html")


# LOGOUT
@auth.route("/logout", methods=["GET", "POST"])
@login_required
def logout():

    user_id = session.get("user_id")
    token = session.get("session_token")

    try:

        with get_db() as db:

            db.execute(
                """
                UPDATE users
                SET session_token = NULL
                WHERE slno = ? AND session_token = ?
                """,
                (user_id, token)
            )

            db.commit()

    except sqlite3.Error:

        current_app.logger.exception("Logout database error")

    # CLEAR FLASK SESSION
    session.clear()

    flash("You have been logged out.", "success")

    return redirect(url_for("auth.login"))


# AUTHENTICATION CONFIGURATION
def configure_auth(app):

    app.config.setdefault(
        "PERMANENT_SESSION_LIFETIME",
        timedelta(minutes=30)
    )

    app.config.setdefault("SESSION_COOKIE_HTTPONLY", True)
    app.config.setdefault("SESSION_COOKIE_SAMESITE", "Lax")

    app.register_blueprint(auth)
