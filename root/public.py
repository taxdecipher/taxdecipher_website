import json
from datetime import datetime
from xml.sax.saxutils import escape

from flask import (
    Blueprint, Response, abort, current_app, redirect, render_template,
    request, url_for
)

from routes.auth import get_db


public = Blueprint("public", __name__)

PER_PAGE = 8
CATEGORIES = ["Income Tax", "GST", "Any Other"]

# Same slug rule used everywhere: use the slug, fall back to the post id.
PUBLIC_SLUG_SQL = """
    CASE
        WHEN TRIM(COALESCE(slug, '')) = ''
        THEN CAST(post_id AS TEXT)
        ELSE slug
    END
"""


def site_url():
    """Absolute site root, from SITE_URL if set, else from the request."""
    return current_app.config["SITE_URL"] or request.url_root.rstrip("/")


def fetch_posts(category=None, page=1, per_page=PER_PAGE, query=None):
    where = "status = 'published'"
    params = []

    if category:
        where += " AND category = ?"
        params.append(category)

    if query:
        like = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where += " AND (title LIKE ? ESCAPE '\\' OR content_html LIKE ? ESCAPE '\\')"
        params.extend([like, like])

    with get_db() as db:
        total = db.execute(
            f"SELECT COUNT(*) FROM posts WHERE {where}", params
        ).fetchone()[0]

        posts = db.execute(f"""
            SELECT post_id, title, content_html, category, cover_photo,
                   slug, published_at,
                   {PUBLIC_SLUG_SQL} AS public_slug
            FROM posts
            WHERE {where}
            ORDER BY published_at DESC, post_id DESC
            LIMIT ? OFFSET ?
        """, params + [per_page, (page - 1) * per_page]).fetchall()

    pages = max(1, -(-total // per_page))
    return posts, total, pages


def category_counts():
    """{'Income Tax': 3, 'GST': 2, ...} for published articles."""
    with get_db() as db:
        rows = db.execute("""
            SELECT category, COUNT(*) AS total
            FROM posts
            WHERE status = 'published'
            GROUP BY category
        """).fetchall()
    return {row["category"]: row["total"] for row in rows}


def author_card():
    """Small summary of the public profile, or None when it is not published."""
    with get_db() as db:
        row = db.execute(
            "SELECT profile_data, status FROM professional_profile WHERE id = 1"
        ).fetchone()
    if not row or row["status"] != "published":
        return None
    try:
        data = json.loads(row["profile_data"])
    except (TypeError, ValueError):
        return None
    return {
        "name": data.get("full_name", ""),
        "title": data.get("professional_title", ""),
        "photo": data.get("profile_photo", ""),
    }


def search_term():
    return request.args.get("q", "").strip()[:80]


def listing(template, category=None, **extra):
    page = request.args.get("page", 1, type=int) or 1
    page = max(1, page)
    query = search_term()

    posts, total, pages = fetch_posts(category, page, query=query)

    if page > pages:
        abort(404)

    return render_template(
        template,
        posts=posts,
        total=total,
        page=page,
        pages=pages,
        category=category,
        categories=CATEGORIES,
        q=query,
        **extra
    )


@public.route("/")
def index():
    category = request.args.get("category", "").strip()
    if category not in CATEGORIES:
        category = None
    return listing(
        "index.html",
        category=category,
        counts=category_counts(),
        author=author_card()
    )


@public.route("/articles")
def articles():
    category = request.args.get("category", "").strip()
    if category not in CATEGORIES:
        category = None
    return listing("articles.html", category=category)


@public.route("/article/<slug>")
def article(slug):

    with get_db() as db:
        post = db.execute(f"""
            SELECT post_id, title, content_html, category, cover_photo,
                   meta_title, meta_description, meta_keywords, slug,
                   published_at, updated_at
            FROM posts
            WHERE status = 'published'
              AND ({PUBLIC_SLUG_SQL}) = ?
            LIMIT 1
        """, (slug,)).fetchone()

        related = []
        if post:
            related = db.execute(f"""
                SELECT title, cover_photo, category, published_at,
                       {PUBLIC_SLUG_SQL} AS public_slug
                FROM posts
                WHERE status = 'published' AND post_id != ?
                ORDER BY (category = ?) DESC, published_at DESC
                LIMIT 3
            """, (post["post_id"], post["category"])).fetchall()

    if post is None:
        abort(404)

    return render_template("article.html", post=post, related=related)


# ---------------------------------------------------------------
# Static information pages
# ---------------------------------------------------------------

@public.route("/about")
def about():
    return render_template("pages/about.html")


@public.route("/contact")
def contact():
    return render_template("pages/contact.html")


@public.route("/privacy-policy")
def privacy_policy():
    return render_template("pages/privacy.html")


@public.route("/disclaimer")
def disclaimer():
    return render_template("pages/disclaimer.html")


# ---------------------------------------------------------------
# SEO
# ---------------------------------------------------------------

@public.route("/favicon.ico")
def favicon():
    return redirect(url_for("static", filename="favicon.ico"), code=301)


@public.route("/robots.txt")
def robots():
    body = "\n".join([
        "User-agent: *",
        "Disallow: /root/",
        "Disallow: /login",
        "Disallow: /logout",
        "Allow: /",
        "",
        f"Sitemap: {site_url()}/sitemap.xml",
        ""
    ])
    return Response(body, mimetype="text/plain")


@public.route("/sitemap.xml")
def sitemap():
    base = site_url()

    urls = [(f"{base}{path}", None) for path in (
        "/", "/articles", "/about", "/contact",
        "/privacy-policy", "/disclaimer"
    )]

    with get_db() as db:
        posts = db.execute(f"""
            SELECT {PUBLIC_SLUG_SQL} AS public_slug,
                   COALESCE(updated_at, published_at) AS lastmod
            FROM posts
            WHERE status = 'published'
            ORDER BY published_at DESC
        """).fetchall()

        profile = db.execute(
            "SELECT status FROM professional_profile WHERE id = 1"
        ).fetchone()

    if profile and profile["status"] == "published":
        urls.append((f"{base}/siddharth", None))

    for post in posts:
        urls.append((
            f"{base}{url_for('public.article', slug=post['public_slug'])}",
            str(post["lastmod"] or "")[:10] or None
        ))

    items = []
    for loc, lastmod in urls:
        entry = f"  <url><loc>{escape(loc)}</loc>"
        if lastmod:
            entry += f"<lastmod>{lastmod}</lastmod>"
        items.append(entry + "</url>")

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(items) + "\n</urlset>\n"
    )
    return Response(xml, mimetype="application/xml")
