import json
from datetime import datetime

from flask import Blueprint, render_template, abort

from routes.auth import get_db
from root.profile import normalize_profile


siddharth_public = Blueprint("siddharth_public", __name__)

# How many items the profile page shows before "Show more"
PREVIEW_LIMIT = 5
# How many of the latest published posts are shown under "My Posts"
LATEST_POSTS = 3


def load_published_profile():
    """Return the published profile dict or abort with 404."""
    with get_db() as db:
        row = db.execute("""
            SELECT profile_data, status
            FROM professional_profile
            WHERE id = 1
            LIMIT 1
        """).fetchone()

    # Profile does not exist or is not published
    if row is None or row["status"] != "published":
        abort(404)

    try:
        return normalize_profile(json.loads(row["profile_data"]))
    except (TypeError, json.JSONDecodeError):
        abort(404)


def latest_posts(limit=LATEST_POSTS):
    with get_db() as db:
        return db.execute("""
            SELECT title, content_html, category, cover_photo, published_at,
                   CASE
                       WHEN TRIM(COALESCE(slug, '')) = ''
                       THEN CAST(post_id AS TEXT)
                       ELSE slug
                   END AS public_slug
            FROM posts
            WHERE status = 'published'
            ORDER BY published_at DESC, post_id DESC
            LIMIT ?
        """, (limit,)).fetchall()


@siddharth_public.route("/siddharth")
def siddharth_profile():
    profile = load_published_profile()

    projects = profile.get("projects", [])
    services = profile.get("services", [])

    return render_template(
        "siddharth.html",
        profile=profile,
        projects=projects[:PREVIEW_LIMIT],
        projects_total=len(projects),
        services=services[:PREVIEW_LIMIT],
        services_total=len(services),
        posts=latest_posts(),
        preview_limit=PREVIEW_LIMIT,
        current_year=datetime.now().year
    )


@siddharth_public.route("/siddharth/portfolio")
def all_portfolio():
    profile = load_published_profile()

    return render_template(
        "siddharth_portfolio.html",
        profile=profile,
        projects=profile.get("projects", []),
        current_year=datetime.now().year
    )


@siddharth_public.route("/siddharth/services")
def all_services():
    profile = load_published_profile()

    return render_template(
        "siddharth_services.html",
        profile=profile,
        services=profile.get("services", []),
        current_year=datetime.now().year
    )
