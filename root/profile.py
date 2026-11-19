
import json
import os
import uuid
from datetime import datetime
from pathlib import Path

from flask import (
    Blueprint, render_template, request,
    redirect, url_for, flash, abort, current_app, jsonify
)
from werkzeug.utils import secure_filename

from routes.auth import login_required, role_required, get_db


root_profile = Blueprint("root_profile", __name__)

UPLOAD_FOLDER = "uploads/profile"
ALLOWED_IMAGES = {"jpg", "jpeg", "png", "webp"}
ALLOWED_RESUMES = {"pdf", "doc", "docx"}
MAX_FILE_SIZE = 5 * 1024 * 1024


def init_profile_table():
    with get_db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS professional_profile (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                profile_data TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'draft',
                updated_at TEXT,
                published_at TEXT
            )
        """)
        db.commit()


def save_upload(file, allowed_extensions):
    if not file or not file.filename:
        return None

    filename = secure_filename(file.filename)
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if extension not in allowed_extensions:
        raise ValueError("Invalid file format.")

    header = file.stream.read(12)
    file.stream.seek(0)

    if extension in ALLOWED_IMAGES:
        is_image = (
            header.startswith(b"\xff\xd8\xff")
            or header.startswith(b"\x89PNG\r\n\x1a\n")
            or (header.startswith(b"RIFF") and header[8:12] == b"WEBP")
        )
        if not is_image:
            raise ValueError("The selected file is not a valid image.")

    file.seek(0, os.SEEK_END)
    size = file.tell()
    file.seek(0)

    if size > MAX_FILE_SIZE:
        raise ValueError("File size must not exceed 5 MB.")

    upload_path = Path(current_app.static_folder) / UPLOAD_FOLDER
    upload_path.mkdir(parents=True, exist_ok=True)

    new_filename = f"{uuid.uuid4().hex}.{extension}"
    file.save(upload_path / new_filename)

    return f"/static/{UPLOAD_FOLDER}/{new_filename}"


def get_profile():
    with get_db() as db:
        row = db.execute("""
            SELECT profile_data, status, updated_at, published_at
            FROM professional_profile
            WHERE id = 1
        """).fetchone()

    if not row:
        return {}, "draft"

    return json.loads(row["profile_data"]), row["status"]


def clean_url(value):
    """Return a safe http(s) URL, or '' if the value is empty/unsafe."""
    value = (value or "").strip()
    if not value:
        return ""
    if not value.lower().startswith(("http://", "https://")):
        if ":" in value.split("/")[0]:
            # javascript:, data:, mailto: ... are not allowed here
            return ""
        value = "https://" + value.lstrip("/")
    return value


def normalize_profile(profile):
    """Make older saved profiles work with the newer structure.

    * Single work-experience fields become an ``experience`` list.
    * ``projects`` always exists (the Portfolio page owns it).
    """
    profile = dict(profile or {})

    if not profile.get("experience"):
        if profile.get("company") or profile.get("designation") or profile.get("work_description"):
            profile["experience"] = [{
                "company": profile.get("company", ""),
                "designation": profile.get("designation", ""),
                "start": profile.get("work_start", ""),
                "end": profile.get("work_end", ""),
                "current": not profile.get("work_end"),
                "description": profile.get("work_description", ""),
            }]
        else:
            profile["experience"] = []

    profile.setdefault("projects", [])
    return profile


def save_profile_data(data, status=None):
    """Write the profile JSON. Keeps the current status unless one is given."""
    now = datetime.now().isoformat(timespec="seconds")

    with get_db() as db:
        existing = db.execute("""
            SELECT status, published_at
            FROM professional_profile
            WHERE id = 1
        """).fetchone()

        new_status = status or (existing["status"] if existing else "draft")
        published_at = None

        if new_status == "published":
            published_at = (
                existing["published_at"]
                if existing and existing["published_at"]
                else now
            )

        db.execute("""
            INSERT INTO professional_profile
                (id, profile_data, status, updated_at, published_at)
            VALUES (1, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                profile_data = excluded.profile_data,
                status = excluded.status,
                updated_at = excluded.updated_at,
                published_at = excluded.published_at
        """, (json.dumps(data), new_status, now, published_at))
        db.commit()

    return new_status


def delete_uploaded_file(web_path):
    """Delete a file we previously saved under static/uploads/profile."""
    if not web_path:
        return
    prefix = f"/static/{UPLOAD_FOLDER}/"
    if not web_path.startswith(prefix):
        return
    name = Path(web_path).name
    target = Path(current_app.static_folder) / UPLOAD_FOLDER / name
    try:
        if target.is_file():
            target.unlink()
    except OSError:
        current_app.logger.warning("Could not delete %s", target)


def collect_profile_data(old_data):
    data = {}

    fields = [
        "full_name", "professional_title", "location",
        "short_intro", "hero_button_text",
        "about_heading", "about_description",
        "experience_years", "projects_completed",
        "qualification",
        "skills", "email", "phone", "linkedin",
        "instagram", "website", "meta_title",
        "meta_description", "meta_keywords",
        "contact_heading", "contact_subtitle"
    ]

    for field in fields:
        data[field] = request.form.get(field, "").strip()

    for link_field in ("linkedin", "instagram", "website"):
        value = data[link_field]
        if value and not value.lower().startswith(("http://", "https://")):
            data[link_field] = "https://" + value.lstrip("/")

    # Education
    data["education"] = [
        {
            "qualification": q,
            "institution": i,
            "year": y,
            "status": s
        }
        for q, i, y, s in zip(
            request.form.getlist("education_qualification[]"),
            request.form.getlist("education_institution[]"),
            request.form.getlist("education_year[]"),
            request.form.getlist("education_status[]")
        )
        if q.strip() or i.strip()
    ]
    for item in data["education"]:
        for key in item:
            item[key] = item[key].strip()

    # Services
    data["services"] = [
        {"title": title, "description": description}
        for title, description in zip(
            request.form.getlist("service_title[]"),
            request.form.getlist("service_description[]")
        )
        if title.strip()
    ]
    for item in data["services"]:
        item["title"] = item["title"].strip()
        item["description"] = item["description"].strip()

    # Work experience (any number of entries)
    companies = request.form.getlist("exp_company[]")
    designations = request.form.getlist("exp_designation[]")
    starts = request.form.getlist("exp_start[]")
    ends = request.form.getlist("exp_end[]")
    currents = request.form.getlist("exp_current[]")
    descriptions = request.form.getlist("exp_description[]")

    experience = []
    for index, company in enumerate(companies):
        company = company.strip()
        designation = (designations[index] if index < len(designations) else "").strip()
        if not company and not designation:
            continue
        is_current = (currents[index] if index < len(currents) else "0") == "1"
        experience.append({
            "company": company,
            "designation": designation,
            "start": (starts[index] if index < len(starts) else "").strip(),
            "end": "" if is_current else (ends[index] if index < len(ends) else "").strip(),
            "current": is_current,
            "description": (descriptions[index] if index < len(descriptions) else "").strip(),
        })
    data["experience"] = experience

    # The About card uses the newest / current job
    if experience:
        data["company"] = experience[0]["company"]
        data["designation"] = experience[0]["designation"]
    else:
        data["company"] = ""
        data["designation"] = ""

    # Portfolio projects are managed on their own page: keep them untouched.
    data["projects"] = old_data.get("projects", [])

    # Profile photo and resume
    data["profile_photo"] = (
        save_upload(request.files.get("profile_photo"), ALLOWED_IMAGES)
        or old_data.get("profile_photo", "")
    )

    new_resume = save_upload(request.files.get("resume"), ALLOWED_RESUMES)
    if new_resume:
        delete_uploaded_file(old_data.get("resume", ""))
    data["resume"] = new_resume or old_data.get("resume", "")

    return data


@root_profile.route("/root/profile", methods=["GET"])
@login_required
@role_required("Root")
def edit_profile():
    init_profile_table()
    profile, status = get_profile()
    profile = normalize_profile(profile)

    return render_template(
        "root/profile.html",
        profile=profile,
        profile_status=status
    )


@root_profile.route("/root/profile/save", methods=["POST"])
@login_required
@role_required("Root")
def save_profile():
    init_profile_table()

    old_data, old_status = get_profile()

    try:
        profile_data = collect_profile_data(old_data)
    except ValueError as error:
        flash(str(error), "danger")
        return redirect(url_for("root_profile.edit_profile"))

    action = request.form.get("action", "draft")
    status = "published" if action == "publish" else "draft"

    save_profile_data(profile_data, status)

    if status == "published":
        flash("Your profile has been published successfully.", "success")
    else:
        flash("Profile saved as draft.", "success")

    return redirect(url_for("root_profile.edit_profile"))


# ----------------------------------------------------------------------
# Remove the uploaded CV
# ----------------------------------------------------------------------
@root_profile.route("/root/profile/remove-resume", methods=["POST"])
@login_required
@role_required("Root")
def remove_resume():
    init_profile_table()
    data, status = get_profile()

    if data.get("resume"):
        delete_uploaded_file(data["resume"])
        data["resume"] = ""
        save_profile_data(data)  # status is left exactly as it was

    return jsonify(success=True)


# ----------------------------------------------------------------------
# Portfolio (own page in the sidebar)
# ----------------------------------------------------------------------
@root_profile.route("/root/portfolio", methods=["GET"])
@login_required
@role_required("Root")
def portfolio():
    init_profile_table()
    profile, status = get_profile()
    profile = normalize_profile(profile)

    return render_template(
        "root/portfolio.html",
        projects=profile.get("projects", []),
        profile_status=status
    )


@root_profile.route("/root/portfolio/save", methods=["POST"])
@login_required
@role_required("Root")
def save_portfolio():
    init_profile_table()
    data, status = get_profile()

    titles = request.form.getlist("project_title[]")
    categories = request.form.getlist("project_category[]")
    descriptions = request.form.getlist("project_description[]")
    links = request.form.getlist("project_link[]")
    existing_images = request.form.getlist("project_existing_image[]")
    uploads = request.files.getlist("project_image[]")

    old_images = {
        item.get("image") for item in data.get("projects", []) if item.get("image")
    }

    projects = []
    kept_images = set()

    try:
        for index, title in enumerate(titles):
            title = title.strip()
            description = (descriptions[index] if index < len(descriptions) else "").strip()

            if not title and not description:
                continue

            image = existing_images[index] if index < len(existing_images) else ""
            if image not in old_images:
                image = ""  # never trust a path that did not come from us

            upload = uploads[index] if index < len(uploads) else None
            new_image = save_upload(upload, ALLOWED_IMAGES) if upload else None
            if new_image:
                image = new_image

            if image:
                kept_images.add(image)

            projects.append({
                "title": title,
                "category": (categories[index] if index < len(categories) else "").strip(),
                "description": description,
                "link": clean_url(links[index] if index < len(links) else ""),
                "image": image,
            })
    except ValueError as error:
        flash(str(error), "danger")
        return redirect(url_for("root_profile.portfolio"))

    # Remove image files that are no longer used
    for old in old_images - kept_images:
        delete_uploaded_file(old)

    data["projects"] = projects
    save_profile_data(data)

    flash("Portfolio saved.", "success")
    return redirect(url_for("root_profile.portfolio"))
