
import secrets

from flask import (
    Blueprint, current_app, flash, redirect, render_template,
    request, session, url_for
)
from werkzeug.security import check_password_hash, generate_password_hash

from routes.auth import get_db, login_required, role_required
from root.contact import (
    EMAIL_RE, MAIL_ADDRESS_KEY, MAIL_PASSWORD_KEY,
    clean_app_password, get_mail_settings, save_setting,
)


root_settings = Blueprint("root_settings", __name__)

# Change these names if your authentication database uses different names.
USERS_TABLE = "users"
USERNAME_COLUMN = "username"
PASSWORD_COLUMN = "password_hash"
SESSION_USERNAME_KEY = "username"


@root_settings.route("/root/settings", methods=["GET", "POST"])
@login_required
@role_required("Root")
def settings():

    username = session.get(SESSION_USERNAME_KEY)

    if not username:
        flash("Unable to identify your account. Please log in again.", "danger")
        return redirect(url_for("auth.login"))

    if request.method == "POST":

        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not current_password or not new_password or not confirm_password:
            flash("Please fill in all password fields.", "danger")

        elif len(new_password) < 8:
            flash("New password must contain at least 8 characters.", "danger")

        elif new_password != confirm_password:
            flash("New password and confirmation do not match.", "danger")

        elif current_password == new_password:
            flash("New password must be different from the current password.", "danger")

        else:
            try:
                with get_db() as db:

                    user = db.execute(
                        f"""
                        SELECT {PASSWORD_COLUMN}
                        FROM {USERS_TABLE}
                        WHERE {USERNAME_COLUMN} = ?
                        LIMIT 1
                        """,
                        (username,)
                    ).fetchone()

                    if user is None:
                        flash("Account not found.", "danger")

                    elif not check_password_hash(
                        user[PASSWORD_COLUMN], current_password
                    ):
                        flash("Your current password is incorrect.", "danger")

                    else:
                        hashed_password = generate_password_hash(new_password)

                        new_token = secrets.token_urlsafe(32)

                        db.execute(
                            f"""
                            UPDATE {USERS_TABLE}
                            SET {PASSWORD_COLUMN} = ?,
                                session_token = ?
                            WHERE {USERNAME_COLUMN} = ?
                            """,
                            (hashed_password, new_token, username)
                        )

                        # Keep this browser logged in; all other sessions end.
                        session["session_token"] = new_token

                        flash("Password changed successfully.", "success")

            except Exception:
                current_app.logger.exception("Password change failed")
                flash("Unable to update password. Please try again.", "danger")

        return redirect(url_for("root_settings.settings"))

    mail_cfg = get_mail_settings()

    return render_template(
        "root/settings.html",
        username=username,
        active_page="settings",
        mail_address=mail_cfg["address"],
        mail_password_set=bool(mail_cfg["password"]),
    )


@root_settings.route("/root/settings/email", methods=["POST"])
@login_required
@role_required("Root")
def update_mail_settings():
    """Change the Gmail address / app password used by the contact form."""
    address = request.form.get("mail_address", "").strip()
    app_password = clean_app_password(request.form.get("mail_app_password", ""))

    if not EMAIL_RE.match(address) or len(address) > 200:
        flash("Please enter a valid Gmail address.", "danger")
        return redirect(url_for("root_settings.settings"))

    try:
        save_setting(MAIL_ADDRESS_KEY, address)
        if app_password:
            if len(app_password) != 16:
                flash("A Google app password has 16 characters. Please check it.", "danger")
                return redirect(url_for("root_settings.settings"))
            save_setting(MAIL_PASSWORD_KEY, app_password)
        flash("Contact form e-mail settings saved.", "success")
    except Exception:
        current_app.logger.exception("Mail settings update failed")
        flash("Unable to save the e-mail settings. Please try again.", "danger")

    return redirect(url_for("root_settings.settings"))
