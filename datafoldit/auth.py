"""Invitation-only local accounts, revocable sessions and authenticator MFA.

Zoho identity federation is deliberately not enabled without registered credentials.
"""
import base64
import hashlib
import hmac
import html
import os
import re
import secrets
import struct
import time
from http.cookies import SimpleCookie
from urllib.parse import urlparse, quote, urlencode

ADMIN_EMAIL = "vamsi@datafoldit.com"
COOKIE = "dfit_account"
FORM_COOKIE = "dfit_form"
ROLES = {"admin", "write", "read"}


def init(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS app_users (
            id INTEGER PRIMARY KEY, email TEXT NOT NULL UNIQUE COLLATE NOCASE,
            role TEXT NOT NULL CHECK(role IN ('admin','write','read')),
            active INTEGER NOT NULL DEFAULT 1, password_hash TEXT,
            mfa_secret TEXT, last_totp INTEGER NOT NULL DEFAULT -1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS app_invites (
            token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES app_users(id),
            expires INTEGER NOT NULL, mfa_secret TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS app_sessions (
            token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES app_users(id),
            expires INTEGER NOT NULL, csrf TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS app_recovery (
            user_id INTEGER NOT NULL REFERENCES app_users(id), code_hash TEXT NOT NULL,
            PRIMARY KEY(user_id, code_hash)
        );
        CREATE TABLE IF NOT EXISTS app_attempts (
            bucket TEXT NOT NULL, stamp INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS app_attempt_bucket ON app_attempts(bucket, stamp);
        CREATE TABLE IF NOT EXISTS app_form_sessions (
            token_hash TEXT PRIMARY KEY, csrf TEXT NOT NULL, expires INTEGER NOT NULL
        );
    """)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def password_hash(password, salt=None):
    if not 12 <= len(password) <= 128:
        raise ValueError("Use a password between 12 and 128 characters.")
    salt = salt or secrets.token_hex(16)
    value = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 600000).hex()
    return salt + ":" + value


def password_matches(password, encoded):
    try:
        return hmac.compare_digest(password_hash(password, encoded.split(":")[0]), encoded)
    except (ValueError, AttributeError):
        return False


def totp(secret, step):
    mac = hmac.new(base64.b32decode(secret), struct.pack(">Q", step), hashlib.sha1).digest()
    offset = mac[-1] & 15
    return str((struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7fffffff) % 1000000).zfill(6)


def valid_step(secret, code):
    if not secret or not re.fullmatch(r"\d{6}", code):
        return None
    now = int(time.time()) // 30
    for step in (now, now - 1, now + 1):
        if hmac.compare_digest(totp(secret, step), code):
            return step
    return None


def invite(conn, email, role, bootstrap=False):
    email = email.strip().lower()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) or len(email) > 254:
        raise ValueError("Enter a valid email address.")
    if role not in ROLES or (role == "admin" and not (bootstrap and email == ADMIN_EMAIL)):
        raise ValueError("Only the designated owner can be admin.")
    existing = conn.execute("SELECT * FROM app_users WHERE email=?", (email,)).fetchone()
    if existing and existing["email"] == ADMIN_EMAIL:
        raise ValueError("The admin is already configured. Use account recovery.")
    with conn:
        if existing:
            uid = existing["id"]
            conn.execute("UPDATE app_users SET role=?, active=1, password_hash=NULL, mfa_secret=NULL WHERE id=?", (role, uid))
            conn.execute("DELETE FROM app_sessions WHERE user_id=?", (uid,))
            conn.execute("DELETE FROM app_recovery WHERE user_id=?", (uid,))
        else:
            uid = conn.execute("INSERT INTO app_users(email,role) VALUES (?,?)", (email, role)).lastrowid
        token = secrets.token_urlsafe(32)
        secret = base64.b32encode(secrets.token_bytes(20)).decode()
        conn.execute("DELETE FROM app_invites WHERE user_id=?", (uid,))
        conn.execute("INSERT INTO app_invites VALUES (?,?,?,?)", (digest(token), uid, int(time.time()) + 86400, secret))
    return token


def invitation(conn, token):
    return conn.execute("SELECT i.*,u.email,u.active FROM app_invites i JOIN app_users u ON u.id=i.user_id WHERE token_hash=? AND expires>? AND active=1", (digest(token), int(time.time()))).fetchone()


def activate(conn, token, password, code):
    row = invitation(conn, token)
    if row is None:
        raise ValueError("Invitation is invalid or expired. Ask the admin for a new invitation.")
    encoded = password_hash(password)
    step = valid_step(row["mfa_secret"], code)
    if step is None:
        raise ValueError("Enter the current six-digit code from your authenticator.")
    recovery = [secrets.token_hex(8) for _ in range(8)]
    with conn:
        removed = conn.execute("DELETE FROM app_invites WHERE token_hash=? AND expires>?", (digest(token), int(time.time())))
        if removed.rowcount != 1:
            raise ValueError("Invitation has already been used.")
        conn.execute("UPDATE app_users SET password_hash=?,mfa_secret=?,last_totp=? WHERE id=?", (encoded, row["mfa_secret"], step, row["user_id"]))
        conn.executemany("INSERT INTO app_recovery VALUES (?,?)", [(row["user_id"], digest(code)) for code in recovery])
    return recovery


def rate_limit(conn, key, limit=10):
    now = int(time.time())
    with conn:
        conn.execute("DELETE FROM app_attempts WHERE stamp<?", (now - 900,))
        count = conn.execute("SELECT COUNT(*) FROM app_attempts WHERE bucket=?", (digest(key),)).fetchone()[0]
        if count >= limit:
            raise ValueError("Too many attempts. Try again in 15 minutes.")
        conn.execute("INSERT INTO app_attempts VALUES (?,?)", (digest(key), now))


def login(conn, email, password, code):
    row = conn.execute("SELECT * FROM app_users WHERE email=? AND active=1", (email.strip().lower(),)).fetchone()
    # Equal-cost password verification for unknown and unactivated accounts.
    encoded = row["password_hash"] if row and row["password_hash"] else "00" * 16 + ":" + "00" * 32
    if not password_matches(password, encoded) or not row or not row["mfa_secret"]:
        raise ValueError("Email, password or verification code is incorrect.")
    step = valid_step(row["mfa_secret"], code)
    with conn:
        if step is not None:
            used = conn.execute("UPDATE app_users SET last_totp=? WHERE id=? AND last_totp<? AND active=1", (step, row["id"], step)).rowcount
        else:
            used = conn.execute("DELETE FROM app_recovery WHERE user_id=? AND code_hash=?", (row["id"], digest(code))).rowcount
        if used != 1:
            raise ValueError("Email, password or verification code is incorrect. A code can only be used once.")
        token = secrets.token_urlsafe(32)
        conn.execute("INSERT INTO app_sessions VALUES (?,?,?,?)", (digest(token), row["id"], int(time.time()) + 28800, secrets.token_urlsafe(32)))
    return token


def session(handler):
    cookie = SimpleCookie()
    try:
        cookie.load(handler.headers.get("Cookie", ""))
        token = cookie[COOKIE].value if COOKIE in cookie else ""
    except Exception:
        token = ""
    return handler.conn.execute("SELECT u.*,s.csrf,s.token_hash FROM app_sessions s JOIN app_users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires>? AND u.active=1", (digest(token), int(time.time()))).fetchone()


def cookie(token):
    from .web import secure_cookie_suffix
    return f"{COOKIE}={token}; Path=/; Max-Age=28800; HttpOnly; SameSite=Lax{secure_cookie_suffix()}"


def form_session(handler, create=False):
    """Bind pre-login forms to a server-issued browser session, not referrer headers."""
    from .web import secure_cookie_suffix
    cookies = SimpleCookie()
    try:
        cookies.load(handler.headers.get("Cookie", ""))
        token = cookies[FORM_COOKIE].value if FORM_COOKIE in cookies else ""
    except Exception:
        token = ""
    row = handler.conn.execute("SELECT csrf FROM app_form_sessions WHERE token_hash=? AND expires>?", (digest(token), int(time.time()))).fetchone()
    if row:
        handler.form_csrf = row["csrf"]
        return row["csrf"]
    if not create:
        return None
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    with handler.conn:
        handler.conn.execute("DELETE FROM app_form_sessions WHERE expires<=?", (int(time.time()),))
        handler.conn.execute("INSERT INTO app_form_sessions VALUES (?,?,?)", (digest(token), csrf, int(time.time()) + 1800))
    handler.form_csrf = csrf
    handler.form_cookie = f"{FORM_COOKIE}={token}; Path=/; Max-Age=1800; HttpOnly; SameSite=Lax{secure_cookie_suffix()}"
    return csrf


def page(title, content):
    e = html.escape
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(title)} · DataFoldIT</title><link rel="stylesheet" href="/static/styles.css"><link rel="stylesheet" href="/static/workspace.css"><script src="/static/theme.js"></script></head><body class="login-page"><main class="login-card"><div class="login-brand"><span class="brand-logo"><img src="/static/datafoldit-logo.jpg" alt="DataFoldIT — Your Vision. Our Expertise."></span></div><h1 style="font-size:22px">{e(title)}</h1>{content}</main></body></html>'''


def login_page(error=""):
    return page("Sign in", f'''<p>Use your approved email and individual password.</p><p role="alert">{html.escape(error)}</p><form method="post" action="/login"><label>Email<input type="email" name="email" autocomplete="username" required></label><label>Password<input type="password" name="password" autocomplete="current-password" required maxlength="128"></label><label>Authenticator or recovery code<input name="code" autocomplete="one-time-code" required></label><button class="button" type="submit">Sign in</button></form><p>Need access or a password reset? Contact your administrator. Zoho sign-in is not connected yet.</p>''')


def accept_page(conn, token, error=""):
    row = invitation(conn, token)
    if not row:
        return page("Invitation unavailable", "<p>This invitation has expired or was already used. Ask the admin for a new one.</p>")
    e = html.escape
    import qrcode
    from qrcode.image.svg import SvgPathFillImage
    uri = "otpauth://totp/" + quote("DataFoldIT:" + row["email"], safe="") + "?" + urlencode({"secret": row["mfa_secret"], "issuer": "DataFoldIT", "algorithm": "SHA1", "digits": 6, "period": 30})
    svg = qrcode.make(uri, image_factory=SvgPathFillImage, border=4).to_string()
    qr = base64.b64encode(svg).decode("ascii")
    qr_html = f'<p>In OneAuth, open Authenticator → Add new → Scan QR code.</p><img src="data:image/svg+xml;base64,{qr}" alt="Scan this QR code with your authenticator to enroll DataFoldIT" width="280" height="280" style="display:block;max-width:100%;height:auto;margin:20px auto;background:white;border-radius:8px"><p>Cannot scan? Use the manual setup key below.</p>'
    return page("Set up your account", f'''<p>{e(row['email'])}</p><p role="alert">{e(error)}</p>{qr_html}<code style="display:block;overflow-wrap:anywhere;margin:18px 0">{e(row['mfa_secret'])}</code><p>Keep this QR code and key private. Enter a generated code below to enable MFA.</p><form method="post" action="/accept"><input type="hidden" name="token" value="{e(token)}"><label>New password (12–128 characters)<input type="password" name="password" autocomplete="new-password" minlength="12" maxlength="128" required></label><label>Confirm password<input type="password" name="confirm" autocomplete="new-password" required></label><label>Six-digit authenticator code<input name="code" inputmode="numeric" pattern="[0-9]{{6}}" autocomplete="one-time-code" required></label><button class="button" type="submit">Activate account</button></form>''')


def delete_user(conn, uid):
    from . import db
    target = conn.execute("SELECT * FROM app_users WHERE id=?", (uid,)).fetchone()
    if not target or target["role"] == "admin" or target["email"].lower() == ADMIN_EMAIL:
        raise ValueError("The owner account cannot be deleted, or the user no longer exists.")
    with conn:
        for table in ("app_sessions", "app_invites", "app_recovery"):
            conn.execute(f"DELETE FROM {table} WHERE user_id=?", (uid,))
        conn.execute("DELETE FROM app_users WHERE id=?", (uid,))
        db.audit(conn, "delete_user", "user", uid, {"email": target["email"]})


def admin_page(handler, message=""):
    from .web import layout, panel
    e = html.escape
    rows = handler.conn.execute("SELECT * FROM app_users ORDER BY id").fetchall()
    items = []
    for row in rows:
        state = "Disabled" if not row["active"] else "Active" if row["password_hash"] else "Setup pending"
        controls = "Owner" if row["role"] == "admin" else f'''<div class="user-access-actions"><form class="user-access-form" method="post" action="/admin/users/update"><input type="hidden" name="user_id" value="{row['id']}"><select name="role" aria-label="Permission for {e(row['email'])}"><option value="read" {'selected' if row['role']=='read' else ''}>Read-only</option><option value="write" {'selected' if row['role']=='write' else ''}>Read/write</option></select><select name="active" aria-label="Account status for {e(row['email'])}"><option value="1" {'selected' if row['active'] else ''}>Enabled</option><option value="0" {'' if row['active'] else 'selected'}>Disabled</option></select><button class="button" type="submit">Save access</button></form><form method="post" action="/admin/users/delete" data-confirm-message="Delete access for {e(row['email'])}? This revokes their login and pending invitations. Transaction history will be preserved."><input type="hidden" name="user_id" value="{row['id']}"><button class="button danger" type="submit">Delete</button></form></div>'''
        items.append(f"<tr><td>{e(row['email'])}</td><td>{e(row['role'])}</td><td>{state}</td><td>{controls}</td></tr>")
    content = panel("Invite or reset access", '''<div class="panel-body"><p>Invitations expire in 24 hours. Reinviting an existing user resets their password and MFA and signs them out. Verify their identity first. When Zoho delivery is configured, creating an invitation sends an email. Check the delivery message after submitting.</p><form class="form-grid" method="post" action="/admin/users/invite"><label>Email<input type="email" name="email" required></label><label>Permission<select name="role"><option value="read">Read-only</option><option value="write">Read/write</option></select></label><button class="button" type="submit">Create invitation</button></form></div>''')
    content += panel("Users", '<div class="table-wrap"><table><thead><tr><th>Email</th><th>Role</th><th>Status</th><th>Access</th></tr></thead><tbody>' + ''.join(items) + '</tbody></table></div>')
    if message:
        content = '<div class="flash">' + message + '</div>' + content
    return layout(handler.conn, "User access", "Administration", "/admin/users", content)


def handle(handler, path, query, post=False):
    """Handle account routes and enforce permission/CSRF checks before app writes."""
    from . import db
    from .web import first
    if not post and (path.startswith("/static/") or path == "/healthz"):
        return False
    user = session(handler)
    handler.account = user
    db.ACTOR.set(user["email"] if user else None)
    if post:
        if path == "/import" and os.environ.get("DATAFOLDIT_SHARED_TEST") == "1":
            handler.send_text("Server file-path imports are disabled in shared testing. Use file uploads instead.", 403)
            return True
        source = handler.headers.get("Origin") or handler.headers.get("Referer", "")
        # Some browsers send Origin: null and omit Referer under no-referrer.
        # Missing provenance is allowed only with the verified CSRF token below.
        if source and source != "null" and urlparse(source).netloc.lower() != handler.headers.get("Host", "").lower():
            handler.send_json({"error": "Request origin is invalid. Reload the page."}, 403)
            return True
        if path in {"/login", "/accept"}:
            if int(handler.headers.get("Content-Length", "0")) > 16384:
                handler.send_text("Form is too large.", 413)
                return True
            expected = form_session(handler)
            csrf = first(handler.read_form(), "csrf", "")
            if not expected or not hmac.compare_digest(expected, csrf):
                handler.send_html(page("Please reopen the form", '<p>Your setup or sign-in form needs refreshing. Reopen your original invitation link or <a href="/login">return to sign in</a>, then submit again.</p>'), 403)
                return True
        if path not in {"/login", "/accept"}:
            if not user:
                handler.send_json({"error": "Login required"}, 401)
                return True
            if path != "/logout" and user["role"] == "read":
                handler.send_json({"error": "Your account has read-only access."}, 403)
                return True
            if (path.startswith("/admin/") or path == "/import") and user["role"] != "admin":
                handler.send_json({"error": "Administrator access required."}, 403)
                return True
            if int(handler.headers.get("Content-Length", "0")) > 50 * 1024 * 1024:
                handler.send_json({"error": "Upload limit is 50 MB per request."}, 413)
                return True
            if handler.headers.get("Content-Type", "").startswith("multipart/form-data"):
                fields, _ = handler.read_multipart_form()
                csrf = fields.get("csrf", "")
            else:
                csrf = first(handler.read_form(), "csrf", "")
            csrf = handler.headers.get("X-CSRF-Token", csrf)
            if not hmac.compare_digest(user["csrf"], csrf):
                handler.send_json({"error": "Session verification failed. Reload the page."}, 403)
                return True
    if path in {"/login", "/accept"}:
        try:
            if not post:
                form_session(handler, create=True)
                body = login_page() if path == "/login" else accept_page(handler.conn, first(query, "token", ""))
                handler.send_html(body)
                return True
            fields = handler.read_form()
            rate_limit(handler.conn, "ip:" + handler.client_address[0], 40)
            if path == "/login":
                email = first(fields, "email", "").lower().strip()
                rate_limit(handler.conn, "email:" + email)
                token = login(handler.conn, email, first(fields, "password", ""), first(fields, "code", "").strip())
                handler.redirect("/", set_cookie=cookie(token))
            else:
                token = first(fields, "token", "")
                rate_limit(handler.conn, "invite:" + token)
                if first(fields, "password") != first(fields, "confirm"):
                    raise ValueError("Passwords do not match.")
                codes = activate(handler.conn, token, first(fields, "password", ""), first(fields, "code", "").strip())
                handler.send_html(page("Account activated", '<p>Save these one-time recovery codes in a password manager. They replace your authenticator code if you lose your device. They will not be shown again.</p><pre>' + '\n'.join(codes) + '</pre><a class="button" href="/login">Continue to sign in</a><p>Wait for the next authenticator code before signing in.</p>'))
        except ValueError as exc:
            body = login_page(str(exc)) if path == "/login" else accept_page(handler.conn, first(handler.read_form(), "token", ""), str(exc))
            handler.send_html(body, 400)
        return True
    if not user:
        handler.redirect("/login")
        return True
    if path == "/logout":
        if post:
            with handler.conn:
                handler.conn.execute("DELETE FROM app_sessions WHERE token_hash=?", (user["token_hash"],))
            handler.redirect("/login", set_cookie=cookie("").replace("Max-Age=28800", "Max-Age=0"))
        else:
            handler.send_html(page("Sign out", '<form method="post" action="/logout"><button class="button">Sign out</button></form>'))
        return True
    if (path.startswith("/admin/") or path == "/backup.db") and user["role"] != "admin":
        handler.send_text("Administrator access required.", 403)
        return True
    if user["role"] == "read" and path.endswith("/edit"):
        handler.send_text("Read-only access.", 403)
        return True
    if path.startswith("/admin/"):
        message = ""
        try:
            if post:
                fields = handler.read_form()
                if path == "/admin/users/invite":
                    email = first(fields, "email", "")
                    role = first(fields, "role", "read")
                    token = invite(handler.conn, email, role)
                    db.audit(handler.conn, "invite_user", "user", None, {"email": email, "role": role})
                    handler.conn.commit()
                    from .invitation_mail import send_invitation
                    delivery_error = send_invitation(email, token, role)
                    message = f'Invitation created. Send this private, single-use link to {html.escape(email)}: <a href="/accept?token={token}">Set up account</a> (right-click to copy link). No email has been sent.'
                    if delivery_error:
                        message = f'Invitation created. {html.escape(delivery_error)} Share this private link manually if needed: <a href="/accept?token={token}">Set up account</a>.'
                    else:
                        message = f'Zoho accepted the invitation email for {html.escape(email)}. Ask them to check their inbox and spam folder. The link expires in 24 hours.'
                elif path == "/admin/users/delete":
                    delete_user(handler.conn, int(first(fields, "user_id", "0")))
                    message = "User access deleted. Sessions and invitations revoked; transaction history preserved."
                elif path == "/admin/users/update":
                    uid = int(first(fields, "user_id", "0"))
                    target = handler.conn.execute("SELECT * FROM app_users WHERE id=?", (uid,)).fetchone()
                    role = first(fields, "role", "read")
                    if not target or target["role"] == "admin" or role not in {"read", "write"}:
                        raise ValueError("The owner account cannot be changed here.")
                    active = int(first(fields, "active", "0") == "1")
                    handler.conn.execute("UPDATE app_users SET role=?,active=? WHERE id=?", (role, active, uid))
                    handler.conn.execute("DELETE FROM app_sessions WHERE user_id=?", (uid,))
                    if not active:
                        handler.conn.execute("DELETE FROM app_invites WHERE user_id=?", (uid,))
                    db.audit(handler.conn, "update_access", "user", uid, {"role": role, "active": active})
                    message = "Access updated. Existing sessions have been revoked."
                else:
                    handler.send_text("Not found", 404)
                    return True
                handler.conn.commit()
        except ValueError as exc:
            handler.conn.rollback()
            message = html.escape(str(exc))
        handler.send_html(admin_page(handler, message))
        return True
    return False
