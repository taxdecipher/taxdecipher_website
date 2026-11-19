
import os
import re
import sqlite3
import secrets
from pathlib import Path

import bleach
from bleach.css_sanitizer import CSSSanitizer

from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for
)

from werkzeug.utils import secure_filename

from routes.auth import get_db, role_required


root_content = Blueprint("root_content", __name__)

# --------------------------------------------------
# CONFIGURATION
# --------------------------------------------------

ALLOWED_CATEGORIES = {
    "Income Tax",
    "GST",
    "Any Other"
}

MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5 MB

IMAGE_SIGNATURES = {
    "jpg": (b"\xff\xd8\xff",),
    "png": (b"\x89PNG\r\n\x1a\n",),
    "webp": (b"RIFF",)
}


# --------------------------------------------------
# HTML SANITIZATION
# --------------------------------------------------

ALLOWED_TAGS = set(bleach.sanitizer.ALLOWED_TAGS).union({
    "p", "br", "hr",
    "h1", "h2", "h3", "h4",
    "strong", "em", "u", "s",
    "span", "div",
    "ul", "ol", "li",
    "blockquote", "pre", "code",
    "a", "img"
})

ALLOWED_ATTRIBUTES = {
    "*": ["class"],

    "a": ["href", "title", "target", "rel"],

    # Preserve image formatting from the editor.
    "img": [
        "src",
        "alt",
        "width",
        "height",
        "style",
        "data-image-width",
        "data-image-position"
    ],

    "p": ["style"],
    "span": ["style"],
    "div": ["style"],
    "h1": ["style"],
    "h2": ["style"],
    "h3": ["style"],
    "h4": ["style"],

    "li": ["data-list"]
}


# CSS properties allowed in article content.
# These are required for image resizing and text wrapping.
CSS_SANITIZER = CSSSanitizer(
    allowed_css_properties=[
        "color",
        "background-color",
        "text-align",
        "font-size",
        "font-family",

        # Image resizing
        "width",
        "max-width",
        "min-width",
        "height",
        "max-height",

        # Image positioning and text wrapping
        "float",
        "display",
        "clear",
        "margin",
        "margin-top",
        "margin-right",
        "margin-bottom",
        "margin-left",

        # Image appearance
        "vertical-align",
        "border-radius"
    ]
)


def clean_article_html(content):
    """
    Sanitize article HTML while preserving image formatting.

    Preserves:
    - Image width (10% to 100%)
    - Float left/right
    - Display properties
    - Image margins
    - Image height and aspect ratio
    - Image positioning data attributes
    """

    return bleach.clean(
        content or "",
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        protocols=["http", "https", "mailto"],
        css_sanitizer=CSS_SANITIZER,
        strip=True
    )


def article_has_text(content):
    plain_text = re.sub(r"<[^>]*>", "", content or "")
    return bool(plain_text.strip())


# --------------------------------------------------
# HELPER FUNCTIONS
# --------------------------------------------------

def slugify(value):
    value = (value or "").lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")


def unique_slug(db, slug, post_id=None):
    """Return a slug no other post is using (appends -2, -3, ...)."""
    if not slug:
        return slug

    candidate = slug
    counter = 2

    while True:
        row = db.execute(
            "SELECT post_id FROM posts WHERE slug = ? AND post_id != ?",
            (candidate, post_id or 0)
        ).fetchone()

        if row is None:
            return candidate

        suffix = f"-{counter}"
        candidate = slug[:200 - len(suffix)] + suffix
        counter += 1


def clean_image_url(value):
    value = (value or "").strip()

    if not value:
        return None

    if value.startswith("/static/uploads/articles/"):
        return value

    if value.startswith("https://") or value.startswith("http://"):
        return value

    return None


def get_image_extension(file_data):
    if file_data.startswith(b"\xff\xd8\xff"):
        return "jpg"

    if file_data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"

    if (
        file_data.startswith(b"RIFF")
        and file_data[8:12] == b"WEBP"
    ):
        return "webp"

    return None


def get_form_data():

    title = request.form.get("title", "").strip()

    content_html = clean_article_html(
        request.form.get("content_html", "")
    )

    category = request.form.get(
        "category",
        "Any Other"
    ).strip()

    if category not in ALLOWED_CATEGORIES:
        category = "Any Other"

    cover_photo = clean_image_url(
        request.form.get("cover_photo", "")
    )

    meta_title = request.form.get(
        "meta_title",
        ""
    ).strip()

    meta_description = request.form.get(
        "meta_description",
        ""
    ).strip()

    meta_keywords = request.form.get(
        "meta_keywords",
        ""
    ).strip()

    slug = slugify(
        request.form.get("slug", "")
    )

    if not slug:
        slug = slugify(title)

    return {
        "title": title,
        "content_html": content_html,
        "category": category,
        "cover_photo": cover_photo,
        "meta_title": meta_title[:200],
        "meta_description": meta_description[:320],
        "meta_keywords": meta_keywords[:500],
        "slug": slug[:200]
    }


# --------------------------------------------------
# DATABASE INITIALIZATION AND MIGRATION
# --------------------------------------------------

def init_posts_table():

    with get_db() as db:

        db.execute("""
            CREATE TABLE IF NOT EXISTS posts (
                post_id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                content_html TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'draft'
                    CHECK(status IN ('draft', 'published')),
                created_by INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                published_at TIMESTAMP,
                FOREIGN KEY (created_by) REFERENCES users(slno)
            )
        """)

        existing_columns = {
            row["name"]
            for row in db.execute(
                "PRAGMA table_info(posts)"
            ).fetchall()
        }

        new_columns = {
            "cover_photo": "TEXT",
            "category": "TEXT DEFAULT 'Any Other'",
            "meta_title": "TEXT",
            "meta_description": "TEXT",
            "meta_keywords": "TEXT",
            "slug": "TEXT"
        }

        for column_name, column_type in new_columns.items():

            if column_name not in existing_columns:

                db.execute(
                    f"ALTER TABLE posts ADD COLUMN "
                    f"{column_name} {column_type}"
                )

        # One-time repair: older data may contain posts sharing a slug,
        # which made all but the first unreachable on the public site.
        seen = set()
        for row in db.execute(
            "SELECT post_id, slug FROM posts "
            "WHERE TRIM(COALESCE(slug, '')) != '' ORDER BY post_id"
        ).fetchall():
            slug = row["slug"]
            if slug in seen:
                counter = 2
                while True:
                    candidate = f"{slug[:190]}-{counter}"
                    taken = candidate in seen or db.execute(
                        "SELECT 1 FROM posts WHERE slug = ?", (candidate,)
                    ).fetchone()
                    if not taken:
                        break
                    counter += 1
                db.execute(
                    "UPDATE posts SET slug = ? WHERE post_id = ?",
                    (candidate, row["post_id"])
                )
                slug = candidate
            seen.add(slug)

        db.commit()


# --------------------------------------------------
# IMAGE UPLOAD
# --------------------------------------------------

@root_content.route("/root/upload-image", methods=["POST"])
@role_required("Root")
def upload_image():

    uploaded_file = request.files.get("image")

    if not uploaded_file or not uploaded_file.filename:

        return jsonify({
            "success": False,
            "error": "Please select an image."
        }), 400

    image_data = uploaded_file.stream.read(
        MAX_IMAGE_SIZE + 1
    )

    if not image_data:

        return jsonify({
            "success": False,
            "error": "The selected file is empty."
        }), 400

    if len(image_data) > MAX_IMAGE_SIZE:

        return jsonify({
            "success": False,
            "error": "Image size cannot exceed 5 MB."
        }), 413

    extension = get_image_extension(image_data)

    if not extension:

        return jsonify({
            "success": False,
            "error": "Only JPG, PNG and WEBP images are allowed."
        }), 400

    original_name = secure_filename(
        uploaded_file.filename
    )

    filename = f"{secrets.token_hex(16)}.{extension}"

    upload_folder = (
        Path(current_app.static_folder)
        / "uploads"
        / "articles"
    )

    upload_folder.mkdir(
        parents=True,
        exist_ok=True
    )

    file_path = upload_folder / filename

    try:

        with open(file_path, "wb") as image_file:
            image_file.write(image_data)

    except OSError:

        current_app.logger.exception(
            "Image upload failed"
        )

        return jsonify({
            "success": False,
            "error": "Unable to upload image. Please try again."
        }), 500

    image_url = url_for(
        "static",
        filename=f"uploads/articles/{filename}"
    )

    return jsonify({
        "success": True,
        "url": image_url
    }), 200


# --------------------------------------------------
# WRITE / EDIT ARTICLE
# --------------------------------------------------

@root_content.route("/root/write", methods=["GET"])
@role_required("Root")
def write():

    post = None

    edit_id = request.args.get(
        "edit",
        type=int
    )

    if edit_id:

        with get_db() as db:

            post = db.execute("""
                SELECT *
                FROM posts
                WHERE post_id = ?
                AND status IN ('draft', 'published')
            """, (edit_id,)).fetchone()

        if not post:

            flash(
                "Draft not found.",
                "warning"
            )

            return redirect(
                url_for("root_content.drafts")
            )

    return render_template(
        "root/write.html",
        username=session.get("username"),
        active_page="write",
        post=post
    )


# --------------------------------------------------
# SAVE OR UPDATE DRAFT
# --------------------------------------------------

@root_content.route(
    "/root/write/save",
    methods=["POST"]
)
@role_required("Root")
def save_draft():

    data = get_form_data()

    post_id = request.form.get(
        "post_id",
        type=int
    )

    if not data["title"]:

        flash(
            "Please enter an article title.",
            "danger"
        )

        return redirect(
            url_for("root_content.write")
        )

    if len(data["title"]) > 200:

        flash(
            "Title cannot exceed 200 characters.",
            "danger"
        )

        return redirect(
            url_for("root_content.write")
        )

    if not article_has_text(data["content_html"]):

        flash(
            "Please write some content before saving.",
            "danger"
        )

        return redirect(
            url_for("root_content.write")
        )

    try:

        with get_db() as db:

            data["slug"] = unique_slug(db, data["slug"], post_id)

            if post_id:

                cursor = db.execute("""
                    UPDATE posts
                    SET
                        title = ?,
                        content_html = ?,
                        category = ?,
                        cover_photo = ?,
                        meta_title = ?,
                        meta_description = ?,
                        meta_keywords = ?,
                        slug = ?,
                        status = 'draft',
                        published_at = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE post_id = ?
                    AND status IN ('draft', 'published')
                """, (
                    data["title"],
                    data["content_html"],
                    data["category"],
                    data["cover_photo"],
                    data["meta_title"],
                    data["meta_description"],
                    data["meta_keywords"],
                    data["slug"],
                    post_id
                ))

                if cursor.rowcount != 1:

                    flash(
                        "Draft could not be updated.",
                        "warning"
                    )

                    return redirect(
                        url_for("root_content.drafts")
                    )

            else:

                db.execute("""
                    INSERT INTO posts (
                        title,
                        content_html,
                        status,
                        created_by,
                        category,
                        cover_photo,
                        meta_title,
                        meta_description,
                        meta_keywords,
                        slug
                    )
                    VALUES (?, ?, 'draft', ?, ?, ?, ?, ?, ?, ?)
                """, (
                    data["title"],
                    data["content_html"],
                    session["user_id"],
                    data["category"],
                    data["cover_photo"],
                    data["meta_title"],
                    data["meta_description"],
                    data["meta_keywords"],
                    data["slug"]
                ))

            db.commit()

    except sqlite3.Error:

        current_app.logger.exception(
            "Draft saving failed"
        )

        flash(
            "Unable to save draft. Please try again.",
            "danger"
        )

        return redirect(
            url_for("root_content.write")
        )

    flash(
        "Your article has been saved as a draft.",
        "success"
    )

    return redirect(
        url_for("root_content.drafts")
    )


# --------------------------------------------------
# ARTICLE PREVIEW
# --------------------------------------------------

@root_content.route(
    "/root/preview",
    methods=["POST"]
)
@role_required("Root")
def preview():

    data = get_form_data()

    if (
        not data["title"]
        or not article_has_text(data["content_html"])
    ):

        return (
            "Please enter an article title and content.",
            400
        )

    return render_template(
        "root/preview.html",
        title=data["title"],
        content_html=data["content_html"],
        category=data["category"],
        cover_photo=data["cover_photo"],
        meta_title=data["meta_title"],
        meta_description=data["meta_description"],
        meta_keywords=data["meta_keywords"],
        slug=data["slug"]
    )


# --------------------------------------------------
# DRAFTS PAGE
# --------------------------------------------------

@root_content.route(
    "/root/drafts",
    methods=["GET"]
)
@role_required("Root")
def drafts():

    with get_db() as db:

        drafts_list = db.execute("""
            SELECT
                post_id,
                title,
                content_html,
                category,
                cover_photo,
                meta_title,
                meta_description,
                meta_keywords,
                slug,
                created_at,
                updated_at
            FROM posts
            WHERE status = 'draft'
            ORDER BY updated_at DESC
        """).fetchall()

    return render_template(
        "root/drafts.html",
        username=session.get("username"),
        active_page="drafts",
        drafts=drafts_list,
        posts=drafts_list
    )


# --------------------------------------------------
# PUBLISH DRAFT
# --------------------------------------------------

@root_content.route(
    "/root/drafts/<int:post_id>/publish",
    methods=["POST"]
)
@role_required("Root")
def publish_draft(post_id):

    if request.form.get("confirmation") != "CONFIRM":

        flash(
            "Invalid confirmation. Article was not published.",
            "danger"
        )

        return redirect(
            url_for("root_content.drafts")
        )

    try:

        with get_db() as db:

            cursor = db.execute("""
                UPDATE posts
                SET
                    status = 'published',
                    published_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE post_id = ?
                AND status = 'draft'
            """, (post_id,))

            db.commit()

            if cursor.rowcount != 1:

                flash(
                    "Draft not found or already published.",
                    "warning"
                )

                return redirect(
                    url_for("root_content.drafts")
                )

    except sqlite3.Error:

        current_app.logger.exception(
            "Publishing draft failed"
        )

        flash(
            "Unable to publish this article.",
            "danger"
        )

        return redirect(
            url_for("root_content.drafts")
        )

    flash(
        "Article published successfully.",
        "success"
    )

    return redirect(
        url_for("root_content.published")
    )


# --------------------------------------------------
# DELETE DRAFT
# --------------------------------------------------

@root_content.route(
    "/root/drafts/<int:post_id>/delete",
    methods=["POST"]
)
@role_required("Root")
def delete_draft(post_id):

    if request.form.get("confirmation") != "CONFIRM":

        flash(
            "Invalid confirmation. Draft was not deleted.",
            "danger"
        )

        return redirect(
            url_for("root_content.drafts")
        )

    try:

        with get_db() as db:

            cursor = db.execute("""
                DELETE FROM posts
                WHERE post_id = ?
                AND status = 'draft'
            """, (post_id,))

            db.commit()

            if cursor.rowcount != 1:

                flash(
                    "Draft not found or already published.",
                    "warning"
                )

                return redirect(
                    url_for("root_content.drafts")
                )

    except sqlite3.Error:

        current_app.logger.exception(
            "Deleting draft failed"
        )

        flash(
            "Unable to delete this draft.",
            "danger"
        )

        return redirect(
            url_for("root_content.drafts")
        )

    flash(
        "Draft deleted successfully.",
        "success"
    )

    return redirect(
        url_for("root_content.drafts")
    )


# --------------------------------------------------
# PUBLISHED ARTICLES
# --------------------------------------------------

@root_content.route(
    "/root/published",
    methods=["GET"]
)
@role_required("Root")
def published():

    with get_db() as db:

        published_posts = db.execute("""
            SELECT *
            FROM posts
            WHERE status = 'published'
            ORDER BY published_at DESC
        """).fetchall()

    return render_template(
        "root/publish.html",
        username=session.get("username"),
        active_page="published",
        published_posts=published_posts
    )


# --------------------------------------------------
# PREVIEW PUBLISHED ARTICLE
# --------------------------------------------------

@root_content.route(
    "/root/published/<int:post_id>/preview",
    methods=["GET"]
)
@role_required("Root")
def preview_published(post_id):

    with get_db() as db:

        post = db.execute("""
            SELECT *
            FROM posts
            WHERE post_id = ?
            AND status = 'published'
        """, (post_id,)).fetchone()

    if not post:

        flash(
            "Published article not found.",
            "warning"
        )

        return redirect(
            url_for("root_content.published")
        )

    return render_template(
        "root/preview.html",
        title=post["title"],
        content_html=clean_article_html(
            post["content_html"]
        ),
        category=post["category"],
        cover_photo=post["cover_photo"],
        meta_title=post["meta_title"],
        meta_description=post["meta_description"],
        meta_keywords=post["meta_keywords"],
        slug=post["slug"]
    )


# --------------------------------------------------
# DELETE PUBLISHED ARTICLE
# --------------------------------------------------

@root_content.route(
    "/root/published/<int:post_id>/delete",
    methods=["POST"]
)
@role_required("Root")
def delete_published(post_id):

    if request.form.get("confirmation") != "CONFIRM":

        flash(
            "Invalid confirmation. Article was not deleted.",
            "danger"
        )

        return redirect(
            url_for("root_content.published")
        )

    try:

        with get_db() as db:

            cursor = db.execute("""
                DELETE FROM posts
                WHERE post_id = ?
                AND status = 'published'
            """, (post_id,))

            db.commit()

            if cursor.rowcount != 1:

                flash(
                    "Published article not found.",
                    "warning"
                )

                return redirect(
                    url_for("root_content.published")
                )

    except sqlite3.Error:

        current_app.logger.exception(
            "Published article deletion failed"
        )

        flash(
            "Unable to delete article.",
            "danger"
        )

        return redirect(
            url_for("root_content.published")
        )

    flash(
        "Published article deleted successfully.",
        "success"
    )

    return redirect(
        url_for("root_content.published")
    )
