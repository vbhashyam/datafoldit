import tempfile
import unittest
import json
import threading
import time
import urllib.request
from pathlib import Path
from unittest.mock import patch
from datafoldit import auth, db, web
from datafoldit.invoice_pdf import invoice_rows_from_pages, extract_invoices_from_file


def text(number):
    return f"Invoice # {number}\nBill To\nExample Client LLC\nInvoice Date: 2026-08-01\nDue Date: 2026-08-31\nTotal $100.00\nBalance Due $100.00"


class InvoicePageTests(unittest.TestCase):
    def test_real_multipart_pdf_opens_six_row_review(self):
        sample = Path("/Users/vamsikrishnabhashyam/Downloads/invoices.pdf")
        if not sample.exists():
            self.skipTest("Local user sample unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            conn = db.connect(Path(tmp) / "test.db")
            db.init_db(conn)
            auth.init(conn)
            invite = auth.invite(conn, "bulk-test@example.com", "write")
            secret = auth.invitation(conn, invite)["mfa_secret"]
            codes = auth.activate(conn, invite, "temporary multipart password", auth.totp(secret, int(time.time()) // 30))
            token = auth.login(conn, "bulk-test@example.com", "temporary multipart password", codes[0])
            csrf = conn.execute("SELECT csrf FROM app_sessions").fetchone()[0]
            handler = web.make_handler(Path(tmp) / "test.db")
            handler.log_message = lambda *args: None
            with patch.object(web, "UPLOAD_DIR", Path(tmp) / "uploads"):
                server = web.ThreadingHTTPServer(("127.0.0.1", 0), handler)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    base = f"http://127.0.0.1:{server.server_port}"
                    headers = {"Cookie": f"{auth.COOKIE}={token}", "Origin": base, "X-CSRF-Token": csrf, "Content-Type": "multipart/form-data; boundary=bulk-test"}
                    body = b'--bulk-test\r\nContent-Disposition: form-data; name="attachment"; filename="combined.pdf"\r\nContent-Type: application/pdf\r\n\r\n' + sample.read_bytes() + b'\r\n--bulk-test--\r\n'
                    result = json.loads(urllib.request.urlopen(urllib.request.Request(base + "/invoices/extract-inline", body, headers)).read())
                    html = urllib.request.urlopen(urllib.request.Request(base + result["review_url"], headers={"Cookie": headers["Cookie"]})).read().decode()
                    self.assertIn('name="row_count" value="6"', html)
                    self.assertIn('name="csrf"', html)
                    self.assertIn("INV-000003", html)
                    self.assertEqual(conn.execute("SELECT count(*) FROM invoices").fetchone()[0], 0)
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join()
                    conn.close()

    def test_distinct_invoices_and_continuations(self):
        rows = invoice_rows_from_pages([text("INV-001"), text("INV-001"), text("INV-002")], Path("combined.pdf"))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["source_pages"], [1, 2])
        self.assertEqual(rows[1]["invoice_number"], "INV-002")

    def test_unknown_and_unreadable_pages_not_silently_lost(self):
        rows = invoice_rows_from_pages([text("INV-001"), "", "Supporting document with multiple details and line items"], Path("combined.pdf"))
        self.assertEqual(len(rows), 3)
        self.assertFalse(rows[1]["_ok"])
        self.assertIn("_warning", rows[2])

    def test_batch_flattens_pages_from_each_file(self):
        with patch("datafoldit.web.extract_invoices_from_file", return_value=invoice_rows_from_pages([text("INV-001"), text("INV-002")], Path("combined.pdf"))):
            self.assertEqual(len(web.extract_invoice_batch([Path("first.pdf"), Path("second.pdf")])), 4)

    def test_scanned_second_page_uses_page_specific_ocr(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scan.pdf"
            path.write_bytes(b"%PDF-fixture")
            with patch("datafoldit.invoice_pdf.find_tool", side_effect=lambda name: name), patch("datafoldit.invoice_pdf.subprocess.run") as run, patch("datafoldit.invoice_pdf.extract_text_with_pdftoppm_ocr", return_value=text("INV-002")) as ocr:
                run.side_effect = [type("Result", (), {"stdout": "Pages: 2"})(), type("Result", (), {"stdout": text("INV-001") + "\f\f"})()]
                self.assertEqual(len(extract_invoices_from_file(path)), 2)
                ocr.assert_called_once_with(path, page_number=2)

    def test_user_pdf_all_six_review_and_save(self):
        path = Path("/Users/vamsikrishnabhashyam/Downloads/invoices.pdf")
        if not path.exists():
            self.skipTest("User sample is local and is not committed")
        rows = web.extract_invoice_batch([path])
        self.assertEqual([row["invoice_number"] for row in rows], ["INV-000015", "INV-000013", "INV-000012", "INV-000011", "INV-000008", "INV-000003"])
        self.assertEqual([row["amount"] for row in rows], [11440, 9444.5, 11440, 6240, 11440, 6240])
        with tempfile.TemporaryDirectory() as tmp:
            conn = db.connect(Path(tmp) / "test.db")
            db.init_db(conn)
            self.assertIn('name="row_count" value="6"', web.render_invoice_bulk_review(conn, rows))
            fields = {"row_count": ["6"]}
            for i, row in enumerate(rows):
                fields[f"include_{i}"] = ["on"]
                for key in ("date", "invoice_number", "customer", "amount", "due_date", "status", "balance_due", "source_pdf"):
                    fields[f"{key}_{i}"] = [str(row.get(key) or "")]
            self.assertEqual(web.add_invoice_batch(conn, fields), 6)
            self.assertEqual(conn.execute("SELECT count(*) FROM invoices").fetchone()[0], 6)
            review = web.render_invoice_bulk_review(conn, rows)
            self.assertIn("Invoice number already exists", review)
            self.assertNotIn(' checked>', review)
            conn.close()
