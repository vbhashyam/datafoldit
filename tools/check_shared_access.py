"""Verify shared HTTPS login using a temporary read-only account, then remove it."""
import http.cookiejar
from pathlib import Path
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from datafoldit import auth, db

base = sys.argv[1].rstrip("/")
root = Path(__file__).resolve().parents[1]
conn = db.connect(root / "tmp/shared-test/datafoldit.db")
email = "qa-" + secrets.token_hex(8) + "@example.com"
password = secrets.token_urlsafe(24)
token = auth.invite(conn, email, "read")
row = auth.invitation(conn, token)
uid = row["user_id"]
try:
    codes = auth.activate(conn, token, password, auth.totp(row["mfa_secret"], int(time.time()) // 30))
    client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    page = client.open(base + "/login", timeout=20).read().decode()
    csrf = re.search(r'name="csrf" value="([^"]+)"', page)[1]
    data = urllib.parse.urlencode({"email": email, "password": password, "code": codes[0], "csrf": csrf}).encode()
    response = client.open(urllib.request.Request(base + "/login", data, {"Origin": base}), timeout=20)
    body = response.read().decode()
    assert response.geturl() == base + "/" and 'data-role="read"' in body
    assert "Employee test" in body
    for path in ("/bank", "/expenses", "/payroll", "/invoices", "/reports"):
        assert client.open(base + path, timeout=20).status == 200
    try:
        client.open(base + "/admin/users", timeout=20)
        raise AssertionError("Read-only user accessed admin")
    except urllib.error.HTTPError as exc:
        assert exc.code == 403
    print("Public HTTPS login, MFA recovery-code verification, all six pages and admin restriction: passed.")
finally:
    with conn:
        for table in ("app_sessions", "app_invites", "app_recovery"):
            conn.execute(f"DELETE FROM {table} WHERE user_id=?", (uid,))
        conn.execute("DELETE FROM app_users WHERE id=? AND email=?", (uid, email))
    conn.close()
    print("Temporary QA account removed; employee/admin accounts unchanged.")
