import http.cookiejar
import re
import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from datafoldit import auth, db, web


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "accounts.db"
        self.conn = db.connect(self.path)
        db.init_db(self.conn)
        auth.init(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def account(self, email="reader@example.com", role="read"):
        invitation = auth.invite(self.conn, email, role, bootstrap=role == "admin")
        secret = auth.invitation(self.conn, invitation)["mfa_secret"]
        codes = auth.activate(self.conn, invitation, "a unique long test password", auth.totp(secret, int(time.time()) // 30))
        return invitation, secret, codes

    def test_invitation_expiry_one_use_password_and_mfa(self):
        token, secret, codes = self.account()
        self.assertIsNone(auth.invitation(self.conn, token))
        with self.assertRaises(ValueError):
            auth.activate(self.conn, token, "a unique long test password", "000000")
        with self.assertRaises(ValueError):
            auth.login(self.conn, "reader@example.com", "wrong password", codes[0])
        auth.login(self.conn, "reader@example.com", "a unique long test password", codes[0])
        with self.assertRaises(ValueError):
            auth.login(self.conn, "reader@example.com", "a unique long test password", codes[0])
        with self.assertRaises(ValueError):
            auth.login(self.conn, "reader@example.com", "a unique long test password", auth.totp(secret, int(time.time()) // 30))
        self.assertNotIn("a unique long test password", self.conn.execute("SELECT password_hash FROM app_users").fetchone()[0])
        expired = auth.invite(self.conn, "expired@example.com", "read")
        self.conn.execute("UPDATE app_invites SET expires=0")
        self.conn.commit()
        self.assertIsNone(auth.invitation(self.conn, expired))

    def test_rate_limit_and_owner_protection(self):
        for _ in range(10):
            auth.rate_limit(self.conn, "test-key")
        with self.assertRaises(ValueError):
            auth.rate_limit(self.conn, "test-key")
        with self.assertRaises(ValueError):
            auth.invite(self.conn, "outsider@example.com", "admin", bootstrap=True)
        self.account(auth.ADMIN_EMAIL, "admin")
        with self.assertRaises(ValueError):
            auth.invite(self.conn, auth.ADMIN_EMAIL, "read")


class AccountHTTPTests(AccountTests):
    def setUp(self):
        super().setUp()
        handler = web.make_handler(self.path)
        handler.log_message = lambda *args: None
        self.server = web.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        super().tearDown()

    def client(self, email, role):
        _, _, codes = self.account(email, role)
        token = auth.login(self.conn, email, "a unique long test password", codes[0])
        csrf = self.conn.execute("SELECT csrf FROM app_sessions WHERE token_hash=?", (auth.digest(token),)).fetchone()[0]
        return token, csrf

    def request(self, path, token="", fields=None, origin=None):
        headers = {"Cookie": f"{auth.COOKIE}={token}", "Origin": self.base if origin is None else origin}
        client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        if fields is not None and path in {"/accept", "/login"}:
            setup_path = path + ("?token=" + fields.get("token", "") if path == "/accept" else "")
            body = client.open(self.base + setup_path).read().decode()
            fields = {**fields, "csrf": re.search(r'name="csrf" value="([^"]+)"', body)[1]}
            del headers["Cookie"]
        data = urllib.parse.urlencode(fields).encode() if fields is not None else None
        request = urllib.request.Request(self.base + path, data, headers)
        try:
            with client.open(request) as response:
                return response.status, response.read().decode(errors="replace")
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode()

    def test_read_only_enforced_for_every_write_route_and_backup(self):
        token, csrf = self.client("reader@example.com", "read")
        for path in ["/", "/bank", "/expenses", "/payroll", "/invoices", "/reports"]:
            status, body = self.request(path, token)
            self.assertEqual(status, 200)
            self.assertIn('data-role="read"', body)
        for path in ["/bank/create", "/bank/extract", "/expenses/create", "/expenses/extract-inline", "/payroll/create", "/payroll/create-bulk", "/invoices/update", "/invoices/delete", "/invoices/status", "/import", "/admin/users/invite"]:
            self.assertEqual(self.request(path, token, {"csrf": csrf})[0], 403, path)
        self.assertEqual(self.request("/backup.db", token)[0], 403)
        self.assertEqual(self.request("/admin/users", token)[0], 403)

    def test_writer_csrf_role_revocation_and_audit(self):
        token, csrf = self.client("writer@example.com", "write")
        fields = {"date": "2026-09-01", "invoice_number": "AUTH-TEST", "amount": "100"}
        self.assertEqual(self.request("/invoices/create", token, fields)[0], 403)
        fields["csrf"] = csrf
        self.assertEqual(self.request("/invoices/create", token, fields, "https://attacker.example")[0], 403)
        self.assertEqual(self.request("/invoices/create", token, fields)[0], 200)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0], 1)
        audit = self.conn.execute("SELECT details FROM audit_log ORDER BY id DESC").fetchone()[0]
        self.assertIn("writer@example.com", audit)
        self.assertNotIn(csrf, audit)
        self.assertEqual(self.request("/admin/users/invite", token, {"csrf": csrf})[0], 403)
        admin, admin_csrf = self.client(auth.ADMIN_EMAIL, "admin")
        uid = self.conn.execute("SELECT id FROM app_users WHERE email='writer@example.com'").fetchone()[0]
        status, _ = self.request("/admin/users/update", admin, {"csrf": admin_csrf, "user_id": uid, "role": "read", "active": "0"})
        self.assertEqual(status, 200)
        self.assertEqual(self.request("/invoices/create", token, fields)[0], 401)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM app_sessions WHERE user_id=?", (uid,)).fetchone()[0], 0)

    def test_old_shared_password_and_cookie_rejected(self):
        self.assertEqual(self.request("/login", fields={"password": web.DEFAULT_PASSWORD})[0], 400)
        _, body = self.request("/", "admin:9999999999:fake")
        self.assertIn("Use your approved email", body)

    def test_browser_style_invitation_activation_and_login(self):
        token = auth.invite(self.conn, "new@example.com", "read")
        secret = auth.invitation(self.conn, token)["mfa_secret"]
        status, body = self.request("/accept?token=" + token)
        self.assertEqual(status, 200)
        self.assertIn("Set up your account", body)
        fields = {"token": token, "password": "a unique long test password", "confirm": "a unique long test password", "code": auth.totp(secret, int(time.time()) // 30)}
        status, body = self.request("/accept", fields=fields)
        self.assertEqual(status, 200)
        self.assertIn("Account activated", body)
        recovery = body.split("<pre>")[1].split("\n")[0]
        client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        login_html = client.open(self.base + "/login").read().decode()
        csrf = re.search(r'name="csrf" value="([^"]+)"', login_html)[1]
        data = urllib.parse.urlencode({"email": "new@example.com", "password": fields["password"], "code": recovery, "csrf": csrf}).encode()
        response = client.open(urllib.request.Request(self.base + "/login", data, {"Origin": self.base}))
        self.assertEqual(response.geturl(), self.base + "/")
        self.assertIn('data-role="read"', response.read().decode())

    def test_setup_with_null_origin_and_csrf_rejections(self):
        token = auth.invite(self.conn, "null-origin@example.com", "read")
        secret = auth.invitation(self.conn, token)["mfa_secret"]
        fields = {"token": token, "password": "a unique long test password", "confirm": "a unique long test password", "code": auth.totp(secret, int(time.time()) // 30)}
        self.assertEqual(self.request("/accept", fields=fields, origin="https://attacker.example")[0], 403)
        self.assertIsNotNone(auth.invitation(self.conn, token))
        self.assertEqual(self.request("/accept", fields=fields, origin="null")[0], 200)
        request = urllib.request.Request(self.base + "/login", urllib.parse.urlencode({"email": "null-origin@example.com"}).encode())
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request)
        self.assertEqual(caught.exception.code, 403)

    def test_missing_origin_still_requires_session_csrf(self):
        token, csrf = self.client("writer@example.com", "write")
        fields = {"date": "2026-09-01", "invoice_number": "NO-ORIGIN", "amount": "50"}
        self.assertEqual(self.request("/invoices/create", token, fields, origin="")[0], 403)
        self.assertEqual(self.request("/invoices/create", token, {**fields, "csrf": csrf}, origin="")[0], 200)


if __name__ == "__main__":
    unittest.main()
