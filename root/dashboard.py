from flask import Blueprint, flash, render_template, session
from werkzeug.security import check_password_hash

from routes.auth import get_db, role_required


root_dashboard = Blueprint("root_dashboard", __name__)

# Passwords that must be changed before the site goes live.
INSECURE_PASSWORDS = ("root", "admin", "password", "12345678")


@root_dashboard.route("/root/dashboard")
@role_required("Root")
def dashboard():

    with get_db() as db:

        counts = {
            row["status"]: row["total"]
            for row in db.execute(
                "SELECT status, COUNT(*) AS total FROM posts GROUP BY status"
            ).fetchall()
        }

        account = db.execute(
            "SELECT password_hash FROM users WHERE slno = ?",
            (session.get("user_id"),)
        ).fetchone()

    if account and any(
        check_password_hash(account["password_hash"], weak)
        for weak in INSECURE_PASSWORDS
    ):
        flash(
            "Security warning: you are still using the default password. "
            "Open Settings and change it before publishing the site.",
            "danger"
        )

    return render_template(
        "root/dashboard.html",
        username=session.get("username"),
        draft_count=counts.get("draft", 0),
        published_count=counts.get("published", 0),
        active_page="dashboard"
    )
