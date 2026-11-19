# Tax Decipher

Flask website for publishing Income Tax / GST articles, with a private admin area
(write, draft, publish, profile, portfolio, settings) and a public portfolio page at `/siddharth`.

## Run locally

```bash
python -m venv venv
venv\Scripts\activate            # Windows   (macOS/Linux: source venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env           # macOS/Linux: cp .env.example .env
```

Edit `.env` and set `COOKIE_SECURE=false` for http://localhost, then:

```bash
python app.py                    # http://127.0.0.1:5000
```

Admin login: `/login` (username `Siddharth`), or use the **Login** button on the home page.

**Contact form:** see `CONTACT_FORM_SETUP.md` to make it deliver messages to your e-mail.

## Before you go live (checklist)

1. **Change the admin password.** The bundled database still uses the default password
   `root`. Log in, open **Settings**, change it. The dashboard shows a red warning until you do.
2. **Set a real `SECRET_KEY`** in the environment:
   `python -c "import secrets; print(secrets.token_hex(32))"`
3. Set `SITE_URL` (e.g. `https://www.yourdomain.com`) so `sitemap.xml` and canonical links are right.
4. Set `CONTACT_EMAIL` if you want an email address on the Contact page.
5. Delete the test articles (`Hello`, `aa`, `QQ`, ...) under **Published Articles / Drafts**.
6. Make sure HTTPS is on, keep `COOKIE_SECURE=true`, and `FLASK_DEBUG=false`.
7. Review `/privacy-policy` and `/disclaimer` and adjust the wording to your needs.

## Deploying

The app is a standard WSGI app:

```bash
gunicorn wsgi:app --workers 2 --threads 4 --bind 0.0.0.0:8000     # Linux
waitress-serve --port=8000 wsgi:app                                # Windows
```

Behind Nginx / a PaaS, set `TRUSTED_PROXY=true`. A `Procfile` is included.

**Database - Turso.** All data (users, articles, drafts, profile, settings) lives in a
[Turso](https://turso.tech) database, so it survives Render restarts and deploys.
The site picks the database automatically (`routes/db.py`):

| Environment variables set                  | Database used                              |
|--------------------------------------------|--------------------------------------------|
| `TURSO_DATABASE` **and** `TURSO_API_TOKEN` | Turso (production)                         |
| not set                                    | local file `database/taxdecipher.db` (dev) |

* `TURSO_DATABASE` = database URL, e.g. `libsql://my-db-myorg.turso.io`
* `TURSO_API_TOKEN` = a **database** auth token (`turso db tokens create <db-name>`),
  not the account/platform API token.
* The token stays on the server. The browser never sees it: the front-end does its
  create / read / update / delete by calling the Flask routes, and those talk to Turso.
* On Render put both variables (plus `SECRET_KEY`, `COOKIE_SECURE=true`, `TRUSTED_PROXY=true`,
  `SITE_URL`) in the `taxdecipher` environment group and **link the group to the web service**.
* Tip: create the Turso database in a region close to your Render service (lower latency).

**Copy an existing `taxdecipher.db` into Turso (once):**

```bash
pip install -r requirements.txt
# put TURSO_DATABASE and TURSO_API_TOKEN in your local .env, then:
python database/migrate_to_turso.py
```

It never modifies the local file and never overwrites rows already in Turso
(use `--overwrite` to make the local file win). For a brand-new empty Turso database,
run `python database/init_db.py` instead to create the first admin account.

**Important - uploaded files.** Images / resume uploaded from the admin area are saved in
`static/uploads/`. Turso stores the database only, not these files, and Render's normal disk
is wiped on every deploy. Use a Render persistent disk (mounted at `static/uploads`) or
external file storage to keep newly uploaded files.

Do **not** upload the `env/` or `venv/` folder; run `pip install -r requirements.txt` on the server.

## Project layout

```
app.py                app factory, security headers, CSRF, error pages
wsgi.py               production entry point
routes/auth.py        login, logout, sessions, role checks, DB helper
root/                 admin + public blueprints (content, profile/portfolio, settings, public, dashboard, contact)
templates/            public_base.html (site layout), pages/, partials/, root/ (admin)
static/css/site.css   public site styles
static/css/admin-sidebar.css + static/js/admin-sidebar.js   the one shared admin sidebar
templates/root/_sidebar.html     sidebar menu - edit here to change it on every admin page
routes/db.py          database layer: Turso (HTTP API) or local SQLite, one common interface
database/init_db.py   creates tables + first admin (python database/init_db.py)
database/migrate_to_turso.py   one-time copy of taxdecipher.db into Turso
```
