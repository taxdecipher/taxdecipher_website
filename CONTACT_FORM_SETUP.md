# Making the contact form work

The contact form is already on the site (the **Contact** page and the **Get In Touch**
section of `/siddharth`). It validates the input, blocks simple spam (hidden trap field,
CSRF token, 5 messages per visitor per hour) and then calls **one function**:

```
root/contact.py  ->  deliver_message(name, email, subject, message)
```

Until you set it up, visitors see *"messages cannot be delivered right now"* and the
message is only written to the server log. Pick ONE of the options below.

---

## Option A - E-mail through SMTP (no code change)

1. Copy `.env.example` to `.env` (if you have not already).
2. Fill in these lines:

```
CONTACT_FORM_TO=you@yourdomain.com      # where you want to receive messages
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=youraccount@gmail.com
SMTP_PASSWORD=your-app-password
```

3. Restart the app. Send a test message from the Contact page.

**Gmail:** turn on 2-step verification, then create an *App Password*
(Google Account -> Security -> App passwords) and use that as `SMTP_PASSWORD`.
Your normal Gmail password will not work.
Port `587` uses STARTTLS, port `465` uses SSL - both are supported.

Replies go straight to the visitor because the visitor's address is set as *Reply-To*.

---

## Option B - Use another service (Formspree, WhatsApp, Google Sheet, database ...)

Open `root/contact.py` and replace the body of `deliver_message()` with your own code.
It must `return True` when the message was delivered and `False` otherwise.
Example - save every message in the existing SQLite database:

```python
from routes.auth import get_db

def deliver_message(name, email, subject, message):
    with get_db() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS contact_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, email TEXT,
            subject TEXT, message TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
        db.execute("INSERT INTO contact_messages (name, email, subject, message) VALUES (?,?,?,?)",
                   (name, email, subject, message))
    return True
```

---

## Files involved

| File | Purpose |
|------|---------|
| `templates/partials/contact_form.html` | The form fields (shared by both pages) |
| `root/contact.py` | Validation, spam protection and `deliver_message()` |
| `static/css/site.css`, `static/css/siddharth.css` | Form styling (light / dark) |

On a host with several workers, the "5 per hour" limiter is per worker. For strict limits
use a proper limiter such as Flask-Limiter.
