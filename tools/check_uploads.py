"""Exercise real multipart uploads on an ephemeral server and test DB copy."""
import http.cookiejar
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from datafoldit import web, auth, db


def main():
    root = Path(__file__).resolve().parents[1]
    attachments = root / "data-test" / "attachments"
    with tempfile.TemporaryDirectory(prefix="dfit-upload-check-") as tmp:
        db_path = Path(tmp) / "test.db"
        shutil.copy2(root / "data-test" / "datafoldit.db", db_path)
        with db.connect(db_path) as conn:
            db.init_db(conn)
            auth.init(conn)
            invite = auth.invite(conn, "upload-test@example.com", "write")
            secret = auth.invitation(conn, invite)["mfa_secret"]
            import time
            codes = auth.activate(conn, invite, "temporary upload test password", auth.totp(secret, int(time.time()) // 30))
            token = auth.login(conn, "upload-test@example.com", "temporary upload test password", codes[0])
            csrf = conn.execute("SELECT csrf FROM app_sessions WHERE token_hash=?", (auth.digest(token),)).fetchone()[0]
        web.UPLOAD_DIR = Path(tmp) / "attachments"
        handler = web.make_handler(db_path)
        handler.log_message = lambda *args: None
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        base = f"http://127.0.0.1:{server.server_port}"
        client.addheaders = [("Cookie", f"{auth.COOKIE}={token}"), ("Origin", base), ("X-CSRF-Token", csrf)]
        cases = [
            ("invoice PDF", "/invoices/extract-inline", ["20260529-120729-INV-000001.pdf"]),
            ("invoice image", "/invoices/extract-inline", ["20260529-120613-IMG_3042.PNG"]),
            ("bank Excel", "/bank/extract-inline", ["20260529-130751-datafoldit-transactions-to-2026-may-29.xlsx"]),
            ("expense image", "/expenses/extract-inline", ["20260529-162852-Screenshot-2026-05-29-at-4.28.42-PM.png"]),
            ("payroll PDF", "/payroll/extract-inline", ["20260704-135231-Payslip_DATW23_Jun_2026.pdf"]),
            ("invoice bulk PDFs", "/invoices/extract", ["20260529-120729-INV-000001.pdf", "20260529-124219-INV-000005.pdf"]),
            ("bank bulk text", "/bank/extract", ["20260529-194827-dfit-bank-one.txt", "20260529-194827-dfit-bank-two.txt"]),
            ("payroll bulk PDFs", "/payroll/extract", ["20260704-135231-Payslip_DATW23_Jun_2026.pdf", "20260704-135351-Payslip_DATW22_Jun_2026.pdf"]),
        ]
        try:
            for label, route, names in cases:
                boundary = "dfit-upload-smoke-boundary"
                body = bytearray()
                for name in names:
                    body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="attachment"; filename="{name}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode())
                    body.extend((attachments / name).read_bytes())
                    body.extend(b"\r\n")
                body.extend(f"--{boundary}--\r\n".encode())
                try:
                    response = client.open(urllib.request.Request(base + route, bytes(body), {"Content-Type": f"multipart/form-data; boundary={boundary}"}), timeout=90)
                    content = response.read().decode()
                    if route.endswith("-inline"):
                        result = json.loads(content)
                        assert not result.get("error"), result.get("error")
                        print(label + ": extracted JSON successfully", flush=True)
                    else:
                        assert "review" in content.lower() and 'name="row_count"' in content
                        print(label + ": bulk review rendered", flush=True)
                except Exception as exc:
                    print(label + ": FAILED " + str(exc), flush=True)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    main()
