"""Contact form (used on the public /contact page and on /siddharth).

Validation, spam protection and CSRF are handled here.  Messages are e-mailed
through the Gmail account configured in Admin -> Settings (or in ``.env``).
See CONTACT_FORM_SETUP.md.
"""
import logging
import os
import re
import smtplib
import time
from email.message import EmailMessage

from flask import Blueprint, flash, redirect, request, url_for

from routes.auth import get_db


contact_form = Blueprint("contact_form", __name__)

log = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^\+?[0-9][0-9\s\-()]{5,18}$")  # optional mobile number

# Very small in-memory limiter: max 5 messages per visitor per hour.
RATE_LIMIT = 5
RATE_WINDOW = 60 * 60
_recent = {}


def _rate_limited():
    ip = request.remote_addr or "unknown"
    now = time.time()
    hits = [t for t in _recent.get(ip, []) if now - t < RATE_WINDOW]
    if len(hits) >= RATE_LIMIT:
        _recent[ip] = hits
        return True
    hits.append(now)
    _recent[ip] = hits
    return False


# ----------------------------------------------------------------------
# Mail settings (Gmail account that sends / receives contact messages)
#
# Values saved from  Admin -> Settings  (stored in the database) win over the
# values in ``.env``.  If neither is set, the defaults below are used.
# ----------------------------------------------------------------------
DEFAULT_MAIL_ADDRESS = "taxdecipher@gmail.com"
DEFAULT_SMTP_HOST = "smtp.gmail.com"
DEFAULT_SMTP_PORT = 587

MAIL_ADDRESS_KEY = "contact_mail_address"
MAIL_PASSWORD_KEY = "contact_mail_app_password"


def _ensure_settings_table(db):
    db.execute(
        """CREATE TABLE IF NOT EXISTS site_settings (
               key TEXT PRIMARY KEY,
               value TEXT,
               updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
           )"""
    )


def get_setting(key):
    """Return a saved setting (or None)."""
    try:
        with get_db() as db:
            _ensure_settings_table(db)
            row = db.execute(
                "SELECT value FROM site_settings WHERE key = ?", (key,)
            ).fetchone()
        return row["value"] if row and row["value"] else None
    except Exception:
        log.exception("Could not read setting %s", key)
        return None


def save_setting(key, value):
    with get_db() as db:
        _ensure_settings_table(db)
        db.execute(
            """INSERT INTO site_settings (key, value, updated_at)
               VALUES (?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(key) DO UPDATE
               SET value = excluded.value, updated_at = CURRENT_TIMESTAMP""",
            (key, value),
        )


def clean_app_password(raw):
    """Google shows app passwords as 'abcd efgh ijkl mnop' - remove the spaces."""
    return re.sub(r"\s+", "", raw or "")


def get_mail_settings():
    """Work out the effective mail settings (database -> .env -> defaults)."""
    address = (
        get_setting(MAIL_ADDRESS_KEY)
        or os.environ.get("SMTP_USERNAME", "").strip()
        or DEFAULT_MAIL_ADDRESS
    )
    password = clean_app_password(
        get_setting(MAIL_PASSWORD_KEY) or os.environ.get("SMTP_PASSWORD", "")
    )
    return {
        "address": address,
        "password": password,
        "host": os.environ.get("SMTP_HOST", "").strip() or DEFAULT_SMTP_HOST,
        "port": int(os.environ.get("SMTP_PORT", "") or DEFAULT_SMTP_PORT),
        # Messages are delivered to this address (defaults to the same Gmail account).
        "recipient": os.environ.get("CONTACT_FORM_TO", "").strip() or address,
    }


# ----------------------------------------------------------------------
def deliver_message(name, email, subject, message, phone=""):
    """E-mail the visitor's message to the site owner through Gmail (SMTP).

    Returns True when it was sent, False when it could not be sent.
    The visitor's address is set as Reply-To so you can answer directly.
    """
    cfg = get_mail_settings()

    if not cfg["address"] or not cfg["password"]:
        log.warning(
            "Contact form message from %s <%s> was NOT delivered: "
            "the Gmail app password is not set (Admin -> Settings).", name, email
        )
        return False

    mail = EmailMessage()
    mail["Subject"] = f"[Tax Decipher] {subject or 'New contact message'}"
    mail["From"] = cfg["address"]
    mail["To"] = cfg["recipient"]
    mail["Reply-To"] = email
    mail.set_content(
        f"Name: {name}\n"
        f"Email: {email}\n"
        f"Mobile: {phone or 'Not provided'}\n\n"
        f"{message}\n"
    )

    try:
        if cfg["port"] == 465:
            server = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=15)
        else:
            server = smtplib.SMTP(cfg["host"], cfg["port"], timeout=15)
            server.starttls()
        with server:
            server.login(cfg["address"], cfg["password"])
            server.send_message(mail)
        return True
    except (smtplib.SMTPException, OSError):
        log.exception("Contact form e-mail could not be sent")
        return False


# ----------------------------------------------------------------------
@contact_form.route("/contact/send", methods=["POST"])
def send():
    target = request.form.get("next", "contact")

    if target == "profile":
        back = url_for("siddharth_public.siddharth_profile") + "#contact"
    else:
        back = url_for("public.contact") + "#contact-form"

    # Hidden trap field: real people never fill it in.
    if request.form.get("website_url"):
        flash("Thank you! Your message has been sent.", "success")
        return redirect(back)

    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip()
    phone = request.form.get("phone", "").strip()
    subject = request.form.get("subject", "").strip()
    message = request.form.get("message", "").strip()

    if len(name) < 2 or len(name) > 100:
        flash("Please enter your name.", "danger")
    elif not EMAIL_RE.match(email) or len(email) > 200:
        flash("Please enter a valid email address.", "danger")
    elif phone and not (
        PHONE_RE.match(phone) and 7 <= len(re.sub(r"\D", "", phone)) <= 15
    ):
        flash("Please enter a valid mobile number or leave it empty.", "danger")
    elif len(subject) > 150:
        flash("The subject is too long (150 characters at most).", "danger")
    elif len(message) < 10 or len(message) > 3000:
        flash("Please write a message between 10 and 3000 characters.", "danger")
    elif _rate_limited():
        flash("Too many messages from your connection. Please try again later.", "danger")
    elif deliver_message(name, email, subject, message, phone):
        flash("Thank you! Your message has been sent.", "success")
    else:
        flash(
            "Sorry, messages cannot be delivered right now. "
            "Please contact directly by email or phone.", "danger"
        )

    return redirect(back)
