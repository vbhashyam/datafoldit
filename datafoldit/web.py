from __future__ import annotations

import argparse
from email import policy
from email.parser import BytesParser
import hashlib
import hmac
import html
import os
import re
import sqlite3
import tempfile
import time
from datetime import date
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlparse

from . import db
from .excel_io import DEFAULT_SOURCE_XLSX, export_report_workbook, import_company_workbook
from .invoice_pdf import extract_invoice_from_pdf
from .paystub_import import extract_paystub_from_file
from .transaction_import import extract_transaction_from_file


FileInfo = dict[str, bytes | str]
ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = Path(__file__).resolve().parent / "static"
DEFAULT_DATA_DIR = Path(os.environ.get("DATAFOLDIT_DATA_DIR", ROOT / "data")).expanduser()
UPLOAD_DIR = Path(os.environ.get("DATAFOLDIT_UPLOAD_DIR", DEFAULT_DATA_DIR / "attachments")).expanduser()
SESSION_COOKIE = "dfit_session"
DEFAULT_PASSWORD = "datafoldit-local"
MONTHS = [
    ("01", "January"),
    ("02", "February"),
    ("03", "March"),
    ("04", "April"),
    ("05", "May"),
    ("06", "June"),
    ("07", "July"),
    ("08", "August"),
    ("09", "September"),
    ("10", "October"),
    ("11", "November"),
    ("12", "December"),
]
BANK_SORT_KEYS = {"date", "type", "category", "detail", "source", "amount", "signed", "attachment"}
EXPENSE_SORT_KEYS = {"date", "category", "vendor", "description", "amount", "paid_by", "frequency", "notes", "attachment"}
PAYROLL_SORT_KEYS = {"month", "name", "vendor", "client", "hours", "gross", "commission", "employee_pay", "credit_date", "paystub_sent", "attachment"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run DataFold IT local dashboard")
    parser.add_argument("--host", default=os.environ.get("DATAFOLDIT_HOST", "127.0.0.1"))
    parser.add_argument("--port", default=int(os.environ.get("DATAFOLDIT_PORT", "8765")), type=int)
    parser.add_argument("--db", default=os.environ.get("DATAFOLDIT_DB", str(DEFAULT_DATA_DIR / "datafoldit.db")))
    parser.add_argument("--import-xlsx", default=None)
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--init-only", action="store_true")
    args = parser.parse_args()

    db_path = Path(args.db)
    conn = db.connect(db_path)
    db.init_db(conn)
    source = Path(args.import_xlsx) if args.import_xlsx else DEFAULT_SOURCE_XLSX
    auto_import = os.environ.get("DATAFOLDIT_AUTO_IMPORT", "1").strip().lower()
    auto_import_enabled = auto_import not in {"0", "false", "no", "off"}
    if args.import_xlsx or (auto_import_enabled and db.is_empty(conn) and source.exists()):
        counts = import_company_workbook(conn, source, replace=args.replace or db.is_empty(conn))
        print(f"Imported workbook: {source}")
        print(", ".join(f"{key}={value}" for key, value in counts.items()))
    conn.close()

    if args.init_only:
        return

    handler = make_handler(db_path)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"DataFold IT dashboard running at http://{args.host}:{args.port}")
    print(f"Password: {os.environ.get('DATAFOLDIT_PASSWORD', DEFAULT_PASSWORD)}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping dashboard")
    finally:
        server.server_close()


def make_handler(db_path: Path):
    class DataFoldHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:
            print("%s - %s" % (self.address_string(), format % args))

        @property
        def conn(self) -> sqlite3.Connection:
            if not hasattr(self, "_conn"):
                self._conn = db.connect(db_path)
                db.init_db(self._conn)
            return self._conn

        def finish(self) -> None:
            try:
                if hasattr(self, "_conn"):
                    self._conn.close()
            finally:
                super().finish()

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            query = parse_qs(parsed.query)
            if path.startswith("/static/"):
                self.serve_static(path)
                return
            if path == "/healthz":
                self.send_text("ok\n")
                return
            if path == "/login":
                self.send_html(login_page(error=first(query, "error")))
                return
            if path == "/logout":
                self.redirect("/login", clear_cookie=True)
                return
            if not self.is_authenticated():
                self.redirect("/login")
                return
            if path.startswith("/attachments/"):
                self.serve_attachment(path)
                return
            flash = first(query, "flash")
            filters = date_filter_from_query(query)
            if path == "/":
                self.send_html(render_dashboard(self.conn, flash))
            elif path == "/bank/extract":
                self.redirect("/bank")
            elif path == "/invoices/extract":
                self.redirect("/invoices")
            elif path == "/bank":
                self.send_html(render_bank(self.conn, flash, bank_filter_from_query(query)))
            elif path == "/bank/edit":
                self.send_html(render_bank_edit(self.conn, int(first(query, "id") or 0), flash))
            elif path == "/expenses":
                self.send_html(render_expenses(self.conn, flash, expense_filter_from_query(query)))
            elif path == "/expenses/edit":
                self.send_html(render_expense_edit(self.conn, int(first(query, "id") or 0), flash))
            elif path == "/payroll":
                self.send_html(render_payroll(self.conn, flash, payroll_filter_from_query(query)))
            elif path == "/payroll/extract":
                self.redirect("/payroll")
            elif path == "/invoices":
                self.send_html(render_invoices(self.conn, flash, invoice_filter_from_query(query)))
            elif path == "/invoices/edit":
                self.send_html(render_invoice_edit(self.conn, int(first(query, "id") or 0), flash))
            elif path == "/reports":
                self.send_html(render_reports(self.conn, flash, filters))
            elif path == "/export.xlsx":
                self.handle_export(query)
            elif path == "/backup.db":
                self.handle_db_backup(db_path)
            else:
                self.send_error(HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/login":
                fields = self.read_form()
                if verify_password(fields.get("password", [""])[0]):
                    self.redirect("/", set_cookie=session_cookie())
                else:
                    self.redirect("/login?error=Invalid+password")
                return
            if not self.is_authenticated():
                self.redirect("/login")
                return
            try:
                if parsed.path == "/bank/extract":
                    fields, files = self.read_multipart_form()
                    uploaded_files = uploaded_file_list(files.get("attachment"))
                    if not uploaded_files:
                        raise ValueError("Choose an attachment to import")
                    saved_paths = [save_uploaded_file(uploaded) for uploaded in uploaded_files]
                    if len(saved_paths) == 1:
                        extracted = extract_transaction_from_file(saved_paths[0])
                        self.send_html(render_transaction_review(self.conn, extracted, "Review extracted transaction before saving"))
                    else:
                        extracted_rows = extract_transaction_batch(saved_paths)
                        self.send_html(render_transaction_bulk_review(self.conn, extracted_rows, "Review extracted transactions before saving"))
                    return
                if parsed.path == "/invoices/extract":
                    if self.headers.get("Content-Type", "").startswith("multipart/form-data"):
                        upload_fields, files = self.read_multipart_form()
                        uploaded_files = uploaded_file_list(files.get("attachment"))
                        if uploaded_files:
                            source_paths = [save_uploaded_file(uploaded) for uploaded in uploaded_files]
                            if len(source_paths) == 1:
                                extracted = extract_invoice_from_pdf(source_paths[0])
                                self.send_html(render_invoice_review(self.conn, extracted, "Review extracted invoice fields before saving"))
                            else:
                                extracted_rows = extract_invoice_batch(source_paths)
                                self.send_html(render_invoice_bulk_review(self.conn, extracted_rows, "Review extracted invoice fields before saving"))
                            return
                        source_path = upload_fields.get("pdf_path", "")
                    else:
                        source_path = flatten_form(self.read_form()).get("pdf_path", "")
                    if not source_path:
                        raise ValueError("Upload an invoice file or enter a PDF path")
                    extracted = extract_invoice_from_pdf(source_path)
                    self.send_html(render_invoice_review(self.conn, extracted, "Review extracted invoice fields before saving"))
                    return
                if parsed.path == "/invoices/create-bulk":
                    saved_count = add_invoice_batch(self.conn, self.read_form())
                    self.conn.commit()
                    self.redirect(f"/invoices?flash={quote(f'{saved_count} invoices saved')}")
                    return
                if parsed.path == "/bank/create-bulk":
                    saved_count = add_bank_transaction_batch(self.conn, self.read_form())
                    self.conn.commit()
                    self.redirect(f"/bank?flash={quote(f'{saved_count} transactions saved')}")
                    return
                if parsed.path == "/payroll/extract":
                    upload_fields, files = self.read_multipart_form()
                    uploaded_files = uploaded_file_list(files.get("attachment"))
                    if not uploaded_files:
                        raise ValueError("Choose a paystub file to import")
                    saved_paths = [save_uploaded_file(uploaded) for uploaded in uploaded_files]
                    if len(saved_paths) == 1:
                        extracted = extract_paystub_from_file(saved_paths[0])
                        self.send_html(render_paystub_review(self.conn, extracted, "Review extracted paystub before saving"))
                    else:
                        extracted_rows = extract_paystub_batch(saved_paths)
                        self.send_html(render_paystub_bulk_review(self.conn, extracted_rows, "Review extracted paystubs before saving"))
                    return
                if parsed.path == "/payroll/create-bulk":
                    saved_count = add_payroll_batch(self.conn, self.read_form())
                    self.conn.commit()
                    self.redirect(f"/payroll?flash={quote(f'{saved_count} payroll entries saved')}")
                    return
                fields = self.read_fields_with_optional_attachment()
                if parsed.path == "/bank/create":
                    db.add_bank_transaction(self.conn, fields)
                    self.conn.commit()
                    self.redirect("/bank?flash=Bank+transaction+saved")
                elif parsed.path == "/bank/update":
                    db.update_bank_transaction(self.conn, int(fields.get("transaction_id") or 0), fields)
                    self.conn.commit()
                    self.redirect("/bank?flash=Bank+transaction+updated")
                elif parsed.path == "/bank/delete":
                    db.delete_bank_transaction(self.conn, int(fields.get("transaction_id") or 0))
                    self.conn.commit()
                    self.redirect("/bank?flash=Bank+transaction+deleted")
                elif parsed.path == "/expenses/create":
                    db.add_expense(self.conn, fields)
                    self.conn.commit()
                    self.redirect("/expenses?flash=Expense+saved")
                elif parsed.path == "/expenses/update":
                    db.update_expense(self.conn, int(fields.get("expense_id") or 0), fields)
                    self.conn.commit()
                    self.redirect("/expenses?flash=Expense+updated")
                elif parsed.path == "/expenses/delete":
                    db.delete_expense(self.conn, int(fields.get("expense_id") or 0))
                    self.conn.commit()
                    self.redirect("/expenses?flash=Expense+deleted")
                elif parsed.path == "/payroll/create":
                    db.add_payroll_entry(self.conn, fields)
                    self.conn.commit()
                    self.redirect("/payroll?flash=Payroll+entry+saved")
                elif parsed.path == "/payroll/update":
                    db.update_payroll_entry(self.conn, int(fields.get("payroll_id") or 0), fields)
                    self.conn.commit()
                    self.redirect("/payroll?flash=Payroll+entry+updated")
                elif parsed.path == "/payroll/delete":
                    db.delete_payroll_entry(self.conn, int(fields.get("payroll_id") or 0))
                    self.conn.commit()
                    self.redirect("/payroll?flash=Payroll+entry+deleted")
                elif parsed.path == "/invoices/create":
                    if fields.get("attachment_path") and not fields.get("source_pdf"):
                        fields["source_pdf"] = fields.pop("attachment_path")
                    db.add_invoice(self.conn, fields)
                    self.conn.commit()
                    self.redirect("/invoices?flash=Invoice+saved")
                elif parsed.path == "/invoices/update":
                    if fields.get("attachment_path") and not fields.get("source_pdf"):
                        fields["source_pdf"] = fields.pop("attachment_path")
                    db.update_invoice(self.conn, int(fields.get("invoice_id") or 0), fields)
                    self.conn.commit()
                    self.redirect("/invoices?flash=Invoice+updated")
                elif parsed.path == "/invoices/status":
                    db.update_invoice_status(self.conn, int(fields.get("invoice_id") or 0), fields.get("status") or "")
                    self.conn.commit()
                    self.redirect(self.headers.get("Referer", "/invoices"))
                elif parsed.path == "/invoices/delete":
                    db.delete_invoice(self.conn, int(fields.get("invoice_id") or 0))
                    self.conn.commit()
                    self.redirect("/invoices?flash=Invoice+deleted")
                elif parsed.path == "/import":
                    path = fields.get("workbook_path") or str(DEFAULT_SOURCE_XLSX)
                    counts = import_company_workbook(self.conn, path, replace=bool(fields.get("replace")))
                    message = "Imported+" + "+".join(f"{key}:{value}" for key, value in counts.items())
                    self.redirect(f"/reports?flash={message}")
                else:
                    self.send_error(HTTPStatus.NOT_FOUND)
            except Exception as exc:
                self.redirect(f"{self.headers.get('Referer', '/')}?{urlencode({'flash': 'Error: ' + str(exc)})}")

        def handle_export(self, query: dict[str, list[str]]) -> None:
            period = first(query, "period", "all")
            month = first(query, "month")
            day = first(query, "day")
            suffix = "all-time"
            if period == "monthly" and month:
                suffix = month
            elif period == "daily" and day:
                suffix = day
            output_path = Path(tempfile.gettempdir()) / f"datafoldit-report-{suffix}.xlsx"
            export_report_workbook(self.conn, output_path, period=period, month=month, day=day)
            content = output_path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            self.send_header("Content-Disposition", f'attachment; filename="datafoldit-report-{suffix}.xlsx"')
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def handle_db_backup(self, db_path_value: Path) -> None:
            content = db_path_value.read_bytes()
            stamp = time.strftime("%Y%m%d-%H%M%S")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition", f'attachment; filename="datafoldit-db-backup-{stamp}.sqlite"')
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def read_form(self) -> dict[str, list[str]]:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8")
            return parse_qs(body, keep_blank_values=True)

        def read_multipart_form(self) -> tuple[dict[str, str], dict[str, list[FileInfo]]]:
            content_type = self.headers.get("Content-Type", "")
            if not content_type.startswith("multipart/form-data"):
                raise ValueError("Upload form must use multipart/form-data")
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            message = BytesParser(policy=policy.default).parsebytes(
                b"Content-Type: " + content_type.encode("utf-8") + b"\r\nMIME-Version: 1.0\r\n\r\n" + body
            )
            fields: dict[str, str] = {}
            files: dict[str, list[FileInfo]] = {}
            for part in message.iter_parts():
                if part.get_content_disposition() != "form-data":
                    continue
                name = part.get_param("name", header="content-disposition")
                if not name:
                    continue
                filename = part.get_filename()
                payload = part.get_payload(decode=True) or b""
                if filename:
                    files.setdefault(name, []).append(
                        {
                            "filename": filename,
                            "content": payload,
                            "content_type": part.get_content_type(),
                        }
                    )
                else:
                    charset = part.get_content_charset() or "utf-8"
                    fields[name] = payload.decode(charset, errors="replace").strip()
            return fields, files

        def read_fields_with_optional_attachment(self) -> dict[str, str]:
            if self.headers.get("Content-Type", "").startswith("multipart/form-data"):
                fields, files = self.read_multipart_form()
                saved_paths = save_optional_uploaded_files(files.get("attachment"))
                if saved_paths:
                    fields["attachment_path"] = join_attachment_paths(saved_paths)
                return {key: value for key, value in fields.items() if value.strip() != ""}
            return flatten_form(self.read_form())

        def send_html(self, body: str, status: HTTPStatus = HTTPStatus.OK) -> None:
            payload = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def send_text(self, body: str, status: HTTPStatus = HTTPStatus.OK) -> None:
            payload = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def redirect(self, location: str, set_cookie: str | None = None, clear_cookie: bool = False) -> None:
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", location)
            if set_cookie:
                self.send_header("Set-Cookie", set_cookie)
            if clear_cookie:
                self.send_header(
                    "Set-Cookie",
                    f"{SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax{secure_cookie_suffix()}",
                )
            self.end_headers()

        def serve_static(self, path: str) -> None:
            requested = (STATIC_DIR / path.removeprefix("/static/")).resolve()
            if not str(requested).startswith(str(STATIC_DIR.resolve())) or not requested.exists():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            content = requested.read_bytes()
            content_type = {".css": "text/css", ".js": "application/javascript", ".png": "image/png"}.get(
                requested.suffix, "application/octet-stream"
            )
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def serve_attachment(self, path: str) -> None:
            requested = (UPLOAD_DIR / path.removeprefix("/attachments/")).resolve()
            if not str(requested).startswith(str(UPLOAD_DIR.resolve())) or not requested.exists():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            content = requested.read_bytes()
            content_type = content_type_for(requested)
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Disposition", f'inline; filename="{requested.name}"')
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def is_authenticated(self) -> bool:
            raw = self.headers.get("Cookie", "")
            cookie = SimpleCookie(raw)
            morsel = cookie.get(SESSION_COOKIE)
            return bool(morsel and verify_session(morsel.value))

    return DataFoldHandler


def secret_key() -> str:
    return os.environ.get("DATAFOLDIT_SECRET", "local-datafoldit-secret-change-before-hosting")


def verify_password(password: str) -> bool:
    expected = os.environ.get("DATAFOLDIT_PASSWORD", DEFAULT_PASSWORD)
    return hmac.compare_digest(password, expected)


def session_cookie() -> str:
    expiry = int(time.time()) + 60 * 60 * 24 * 7
    payload = f"admin:{expiry}"
    signature = hmac.new(secret_key().encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{SESSION_COOKIE}={payload}:{signature}; Path=/; Max-Age=604800; HttpOnly; SameSite=Lax{secure_cookie_suffix()}"


def secure_cookie_suffix() -> str:
    enabled = os.environ.get("DATAFOLDIT_COOKIE_SECURE", "").strip().lower()
    return "; Secure" if enabled in {"1", "true", "yes", "on"} else ""


def verify_session(value: str) -> bool:
    try:
        user, expiry_text, signature = value.split(":", 2)
        payload = f"{user}:{expiry_text}"
        expected = hmac.new(secret_key().encode(), payload.encode(), hashlib.sha256).hexdigest()
        return user == "admin" and int(expiry_text) >= int(time.time()) and hmac.compare_digest(signature, expected)
    except Exception:
        return False


def first(query: dict[str, list[str]], key: str, default: str | None = None) -> str | None:
    values = query.get(key)
    if not values:
        return default
    return values[0]


def date_filter_from_query(query: dict[str, list[str]]) -> dict[str, str]:
    year = (first(query, "year") or "").strip()
    month = (first(query, "month") or "").strip()
    if not (len(year) == 4 and year.isdigit()):
        year = ""
    if month not in {value for value, _ in MONTHS}:
        month = ""
    return {"year": year, "month": month}


def bank_filter_from_query(query: dict[str, list[str]]) -> dict[str, str]:
    filters = date_filter_from_query(query)
    filters["source"] = (first(query, "source") or "").strip()
    add_sort_filters(query, filters, BANK_SORT_KEYS, "date")
    return filters


def expense_filter_from_query(query: dict[str, list[str]]) -> dict[str, str]:
    filters = date_filter_from_query(query)
    filters["paid_by"] = (first(query, "paid_by") or "").strip()
    add_sort_filters(query, filters, EXPENSE_SORT_KEYS, "date")
    return filters


def payroll_filter_from_query(query: dict[str, list[str]]) -> dict[str, str]:
    filters = date_filter_from_query(query)
    filters["candidate"] = (first(query, "candidate") or "").strip()
    add_sort_filters(query, filters, PAYROLL_SORT_KEYS, "month")
    return filters


def invoice_filter_from_query(query: dict[str, list[str]]) -> dict[str, str]:
    filters = date_filter_from_query(query)
    filters["customer"] = (first(query, "customer") or "").strip()
    status = (first(query, "status") or "").strip()
    filters["status"] = status if status in set(INVOICE_STATUS_OPTIONS) else ""
    add_sort_filters(query, filters, INVOICE_SORT_KEYS, "date")
    return filters


def add_sort_filters(query: dict[str, list[str]], filters: dict[str, str], sort_keys: set[str], default_key: str) -> None:
    sort_key = (first(query, "sort") or default_key).strip()
    filters["sort"] = sort_key if sort_key in sort_keys else default_key
    direction = (first(query, "direction") or "desc").strip().lower()
    filters["direction"] = direction if direction in {"asc", "desc"} else "desc"


def filter_rows_by_period(rows: list[sqlite3.Row], date_key: str, filters: dict[str, str]) -> list[sqlite3.Row]:
    year = filters.get("year", "")
    month = filters.get("month", "")
    if not year and not month:
        return rows
    visible_rows = []
    for row in rows:
        value = str(row[date_key] or "")
        row_year = value[:4]
        row_month = value[5:7] if len(value) >= 7 else ""
        if year and row_year != year:
            continue
        if month and row_month != month:
            continue
        visible_rows.append(row)
    return visible_rows


def filter_payroll_rows(rows: list[sqlite3.Row], filters: dict[str, str]) -> list[sqlite3.Row]:
    rows = filter_rows_by_period(rows, "month", filters)
    candidate = filters.get("candidate", "")
    if candidate:
        rows = [row for row in rows if payroll_employee_name(row) == candidate]
    return rows


def filter_bank_rows(rows: list[sqlite3.Row], filters: dict[str, str]) -> list[sqlite3.Row]:
    rows = filter_rows_by_period(rows, "date", filters)
    source = filters.get("source", "")
    if source:
        rows = [row for row in rows if str(row["source"] or "") == source]
    return rows


def filter_expense_rows(rows: list[sqlite3.Row], filters: dict[str, str]) -> list[sqlite3.Row]:
    rows = filter_rows_by_period(rows, "date", filters)
    paid_by = filters.get("paid_by", "")
    if paid_by:
        rows = [row for row in rows if str(row["paid_by"] or "") == paid_by]
    return rows


def sort_table_rows(rows: list[sqlite3.Row], filters: dict[str, str], sort_value) -> list[sqlite3.Row]:
    reverse = filters.get("direction", "desc") == "desc"
    return sorted(rows, key=sort_value, reverse=reverse)


def sort_bank_rows(rows: list[sqlite3.Row], filters: dict[str, str]) -> list[sqlite3.Row]:
    sort_key = filters.get("sort", "date")

    def value(row: sqlite3.Row):
        if sort_key == "signed":
            return db.bank_signed_amount(row)
        if sort_key == "amount":
            return db.amount_value(row["amount"])
        if sort_key == "attachment":
            return str(row["attachment_path"] if "attachment_path" in row.keys() else "").lower()
        return str(row[sort_key] or "").lower()

    return sort_table_rows(rows, filters, value)


def sort_expense_rows(rows: list[sqlite3.Row], filters: dict[str, str]) -> list[sqlite3.Row]:
    sort_key = filters.get("sort", "date")

    def value(row: sqlite3.Row):
        if sort_key == "amount":
            return db.amount_value(row["amount"])
        if sort_key == "attachment":
            return str(row["attachment_path"] if "attachment_path" in row.keys() else "").lower()
        return str(row[sort_key] or "").lower()

    return sort_table_rows(rows, filters, value)


def sort_payroll_rows(rows: list[sqlite3.Row], filters: dict[str, str]) -> list[sqlite3.Row]:
    sort_key = filters.get("sort", "month")

    def value(row: sqlite3.Row):
        if sort_key == "name":
            return payroll_employee_name(row).lower()
        if sort_key in {"hours", "gross", "commission", "employee_pay"}:
            return db.amount_value(row[sort_key])
        if sort_key == "paystub_sent":
            return "yes" if str(row["paystub_sent"] or "").upper() == "Y" else "no"
        if sort_key == "attachment":
            return str(row["attachment_path"] if "attachment_path" in row.keys() else "").lower()
        return str(row[sort_key] or "").lower()

    return sort_table_rows(rows, filters, value)


def filter_invoice_rows(rows: list[sqlite3.Row], filters: dict[str, str]) -> list[sqlite3.Row]:
    rows = filter_rows_by_period(rows, "date", filters)
    customer = filters.get("customer", "")
    status = filters.get("status", "")
    if customer:
        rows = [row for row in rows if str(row["customer"] or "") == customer]
    if status:
        rows = [row for row in rows if invoice_status_label(row) == status]
    return rows


def period_label(filters: dict[str, str]) -> str:
    year = filters.get("year", "")
    month = filters.get("month", "")
    month_names = dict(MONTHS)
    if year and month:
        return f"{month_names[month]} {year}"
    if year:
        return year
    if month:
        return month_names[month]
    return "All data"


def payroll_label(filters: dict[str, str]) -> str:
    candidate = filters.get("candidate", "")
    period = period_label(filters)
    if candidate and period != "All data":
        return f"{candidate} · {period}"
    return candidate or period


def payroll_employee_name(row: sqlite3.Row) -> str:
    name = f"{row['first_name'] or ''} {row['last_name'] or ''}".strip()
    return name or "Unassigned"


def payroll_employees(conn) -> list[str]:
    rows = conn.execute(
        """
        SELECT DISTINCT trim(COALESCE(first_name, '') || ' ' || COALESCE(last_name, '')) AS employee
        FROM payroll_entries
        ORDER BY employee
        """
    ).fetchall()
    return [row["employee"] for row in rows if row["employee"]]


def years_for_scope(conn, scope: str) -> list[str]:
    sources = {
        "bank": [("bank_transactions", "date")],
        "expenses": [("expenses", "date")],
        "payroll": [("payroll_entries", "month")],
        "invoices": [("invoices", "date")],
        "reports": [
            ("bank_transactions", "date"),
            ("expenses", "date"),
            ("payroll_entries", "month"),
            ("invoices", "date"),
            ("audit_log", "created_at"),
        ],
    }
    years: set[str] = set()
    for table, column in sources.get(scope, []):
        rows = conn.execute(
            f"SELECT DISTINCT substr({column}, 1, 4) AS year FROM {table} WHERE {column} IS NOT NULL AND {column} != ''"
        ).fetchall()
        years.update(row["year"] for row in rows if row["year"] and str(row["year"]).isdigit())
    return sorted(years, reverse=True)


def period_filter_form(conn, path: str, scope: str, filters: dict[str, str]) -> str:
    selected_year = filters.get("year", "")
    selected_month = filters.get("month", "")
    year_options = ['<option value="">All years</option>']
    year_options.extend(
        f'<option value="{esc(year)}"{" selected" if selected_year == year else ""}>{esc(year)}</option>'
        for year in years_for_scope(conn, scope)
    )
    month_options = ['<option value="">All months</option>']
    month_options.extend(
        f'<option value="{esc(value)}"{" selected" if selected_month == value else ""}>{esc(label)}</option>'
        for value, label in MONTHS
    )
    clear_button = f'<a class="button muted compact" href="{esc(path)}">Clear</a>' if selected_year or selected_month else ""
    return f"""
    <form class="filter-form" method="get" action="{esc(path)}">
      <label>Year
        <select name="year">{''.join(year_options)}</select>
      </label>
      <label>Month
        <select name="month">{''.join(month_options)}</select>
      </label>
      <button class="button compact" type="submit">Apply</button>
      {clear_button}
    </form>
    """


def bank_filter_form(conn, filters: dict[str, str]) -> str:
    selected_source = filters.get("source", "")
    selected_year = filters.get("year", "")
    selected_month = filters.get("month", "")
    source_options = ['<option value="">All paid by / sources</option>']
    source_options.extend(
        f'<option value="{esc(source)}"{" selected" if selected_source == source else ""}>{esc(source)}</option>'
        for source in db.distinct_values(conn, "bank_transactions", "source")
    )
    year_options = ['<option value="">All years</option>']
    year_options.extend(
        f'<option value="{esc(year)}"{" selected" if selected_year == year else ""}>{esc(year)}</option>'
        for year in years_for_scope(conn, "bank")
    )
    month_options = ['<option value="">All months</option>']
    month_options.extend(
        f'<option value="{esc(value)}"{" selected" if selected_month == value else ""}>{esc(label)}</option>'
        for value, label in MONTHS
    )
    clear_button = '<a class="button muted compact" href="/bank">Clear</a>' if selected_source or selected_year or selected_month else ""
    return f"""
    <form class="filter-form" method="get" action="/bank">
      <label class="wide">Paid by / Source
        <select name="source">{''.join(source_options)}</select>
      </label>
      <label>Year
        <select name="year">{''.join(year_options)}</select>
      </label>
      <label>Month
        <select name="month">{''.join(month_options)}</select>
      </label>
      <button class="button compact" type="submit">Apply</button>
      {clear_button}
    </form>
    """


def expense_filter_form(conn, filters: dict[str, str]) -> str:
    selected_paid_by = filters.get("paid_by", "")
    selected_year = filters.get("year", "")
    selected_month = filters.get("month", "")
    paid_by_options = ['<option value="">All paid by</option>']
    paid_by_options.extend(
        f'<option value="{esc(paid_by)}"{" selected" if selected_paid_by == paid_by else ""}>{esc(paid_by)}</option>'
        for paid_by in db.distinct_values(conn, "expenses", "paid_by")
    )
    year_options = ['<option value="">All years</option>']
    year_options.extend(
        f'<option value="{esc(year)}"{" selected" if selected_year == year else ""}>{esc(year)}</option>'
        for year in years_for_scope(conn, "expenses")
    )
    month_options = ['<option value="">All months</option>']
    month_options.extend(
        f'<option value="{esc(value)}"{" selected" if selected_month == value else ""}>{esc(label)}</option>'
        for value, label in MONTHS
    )
    clear_button = '<a class="button muted compact" href="/expenses">Clear</a>' if selected_paid_by or selected_year or selected_month else ""
    return f"""
    <form class="filter-form" method="get" action="/expenses">
      <label class="wide">Paid by
        <select name="paid_by">{''.join(paid_by_options)}</select>
      </label>
      <label>Year
        <select name="year">{''.join(year_options)}</select>
      </label>
      <label>Month
        <select name="month">{''.join(month_options)}</select>
      </label>
      <button class="button compact" type="submit">Apply</button>
      {clear_button}
    </form>
    """


def payroll_filter_form(conn, filters: dict[str, str]) -> str:
    selected_candidate = filters.get("candidate", "")
    selected_year = filters.get("year", "")
    selected_month = filters.get("month", "")
    candidate_options = ['<option value="">All candidates</option>']
    candidate_options.extend(
        f'<option value="{esc(candidate)}"{" selected" if selected_candidate == candidate else ""}>{esc(candidate)}</option>'
        for candidate in payroll_employees(conn)
    )
    year_options = ['<option value="">All years</option>']
    year_options.extend(
        f'<option value="{esc(year)}"{" selected" if selected_year == year else ""}>{esc(year)}</option>'
        for year in years_for_scope(conn, "payroll")
    )
    month_options = ['<option value="">All months</option>']
    month_options.extend(
        f'<option value="{esc(value)}"{" selected" if selected_month == value else ""}>{esc(label)}</option>'
        for value, label in MONTHS
    )
    clear_button = (
        '<a class="button muted compact" href="/payroll">Clear</a>'
        if selected_candidate or selected_year or selected_month
        else ""
    )
    return f"""
    <form class="filter-form" method="get" action="/payroll">
      <label class="wide">Candidate Name
        <select name="candidate">{''.join(candidate_options)}</select>
      </label>
      <label>Year
        <select name="year">{''.join(year_options)}</select>
      </label>
      <label>Month
        <select name="month">{''.join(month_options)}</select>
      </label>
      <button class="button compact" type="submit">Apply</button>
      {clear_button}
    </form>
    """


def invoice_filter_form(conn, filters: dict[str, str]) -> str:
    selected_customer = filters.get("customer", "")
    selected_status = filters.get("status", "")
    selected_year = filters.get("year", "")
    selected_month = filters.get("month", "")
    customer_options = ['<option value="">All customers</option>']
    customer_options.extend(
        f'<option value="{esc(customer)}"{" selected" if selected_customer == customer else ""}>{esc(customer)}</option>'
        for customer in db.distinct_values(conn, "invoices", "customer")
    )
    status_options = ['<option value="">All statuses</option>']
    status_options.extend(
        f'<option value="{esc(status)}"{" selected" if selected_status == status else ""}>{esc(status)}</option>'
        for status in INVOICE_STATUS_OPTIONS
    )
    year_options = ['<option value="">All years</option>']
    year_options.extend(
        f'<option value="{esc(year)}"{" selected" if selected_year == year else ""}>{esc(year)}</option>'
        for year in years_for_scope(conn, "invoices")
    )
    month_options = ['<option value="">All months</option>']
    month_options.extend(
        f'<option value="{esc(value)}"{" selected" if selected_month == value else ""}>{esc(label)}</option>'
        for value, label in MONTHS
    )
    clear_button = (
        '<a class="button muted compact" href="/invoices">Clear</a>'
        if selected_customer or selected_status or selected_year or selected_month
        else ""
    )
    return f"""
    <form class="filter-form" method="get" action="/invoices">
      <label class="wide">Customer
        <select name="customer">{''.join(customer_options)}</select>
      </label>
      <label>Status
        <select name="status">{''.join(status_options)}</select>
      </label>
      <label>Year
        <select name="year">{''.join(year_options)}</select>
      </label>
      <label>Month
        <select name="month">{''.join(month_options)}</select>
      </label>
      <button class="button compact" type="submit">Apply</button>
      {clear_button}
    </form>
    """


def uploaded_file_list(files: list[FileInfo] | None) -> list[FileInfo]:
    return [file_info for file_info in (files or []) if file_info.get("content")]


def first_uploaded_file(files: list[FileInfo] | None) -> FileInfo | None:
    uploaded = uploaded_file_list(files)
    return uploaded[0] if uploaded else None


def extract_invoice_batch(paths: list[Path]) -> list[dict]:
    extracted_rows: list[dict] = []
    for path in paths:
        try:
            extracted = extract_invoice_from_pdf(path)
            extracted["_ok"] = True
            extracted["_source_name"] = path.name
            extracted_rows.append(extracted)
        except Exception as exc:
            extracted_rows.append(
                {
                    "_ok": False,
                    "_source_name": path.name,
                    "source_pdf": str(path),
                    "error": str(exc),
                }
            )
    return extracted_rows


def extract_transaction_batch(paths: list[Path]) -> list[dict]:
    extracted_rows: list[dict] = []
    for path in paths:
        try:
            extracted = extract_transaction_from_file(path)
            extracted["_ok"] = True
            extracted["_source_name"] = path.name
            extracted_rows.append(extracted)
        except Exception as exc:
            extracted_rows.append(
                {
                    "_ok": False,
                    "_source_name": path.name,
                    "attachment_path": str(path),
                    "error": str(exc),
                }
            )
    return extracted_rows


def extract_paystub_batch(paths: list[Path]) -> list[dict]:
    extracted_rows: list[dict] = []
    for path in paths:
        try:
            extracted = extract_paystub_from_file(path)
            extracted["_ok"] = True
            extracted["_source_name"] = path.name
            extracted_rows.append(extracted)
        except Exception as exc:
            extracted_rows.append(
                {
                    "_ok": False,
                    "_source_name": path.name,
                    "attachment_path": str(path),
                    "error": str(exc),
                }
            )
    return extracted_rows


def add_bank_transaction_batch(conn: sqlite3.Connection, fields: dict[str, list[str]]) -> int:
    try:
        row_count = int(form_value(fields, "row_count") or "0")
    except ValueError:
        row_count = 0
    saved_count = 0
    for index in range(row_count):
        if form_value(fields, f"include_{index}").lower() not in {"on", "1", "yes"}:
            continue
        payload = {
            "date": form_value(fields, f"date_{index}"),
            "type": form_value(fields, f"type_{index}"),
            "category": form_value(fields, f"category_{index}"),
            "amount": form_value(fields, f"amount_{index}"),
            "detail": form_value(fields, f"detail_{index}"),
            "source": form_value(fields, f"source_{index}"),
            "notes": form_value(fields, f"notes_{index}"),
            "attachment_path": form_value(fields, f"attachment_path_{index}"),
        }
        if not any(payload.get(key) for key in ("date", "amount", "detail")):
            continue
        db.add_bank_transaction(conn, payload)
        saved_count += 1
    if saved_count == 0:
        raise ValueError("Select at least one transaction to save")
    return saved_count


def add_payroll_batch(conn: sqlite3.Connection, fields: dict[str, list[str]]) -> int:
    try:
        row_count = int(form_value(fields, "row_count") or "0")
    except ValueError:
        row_count = 0
    saved_count = 0
    for index in range(row_count):
        if form_value(fields, f"include_{index}").lower() not in {"on", "1", "yes"}:
            continue
        payload = {
            "month": form_value(fields, f"month_{index}"),
            "first_name": form_value(fields, f"first_name_{index}"),
            "last_name": form_value(fields, f"last_name_{index}"),
            "vendor": form_value(fields, f"vendor_{index}"),
            "client": form_value(fields, f"client_{index}"),
            "job_start": form_value(fields, f"job_start_{index}"),
            "job_end": form_value(fields, f"job_end_{index}"),
            "vendor_pay": form_value(fields, f"vendor_pay_{index}"),
            "pct": form_value(fields, f"pct_{index}"),
            "hours": form_value(fields, f"hours_{index}"),
            "gross": form_value(fields, f"gross_{index}"),
            "commission": form_value(fields, f"commission_{index}"),
            "employee_pay": form_value(fields, f"employee_pay_{index}"),
            "credit_date": form_value(fields, f"credit_date_{index}"),
            "attachment_path": form_value(fields, f"attachment_path_{index}"),
            "paystub_sent": form_value(fields, f"paystub_sent_{index}"),
        }
        if not any(payload.get(key) for key in ("month", "first_name", "gross")):
            continue
        db.add_payroll_entry(conn, payload)
        saved_count += 1
    if saved_count == 0:
        raise ValueError("Select at least one payroll entry to save")
    return saved_count


def add_invoice_batch(conn: sqlite3.Connection, fields: dict[str, list[str]]) -> int:
    try:
        row_count = int(form_value(fields, "row_count") or "0")
    except ValueError:
        row_count = 0
    saved_count = 0
    for index in range(row_count):
        if form_value(fields, f"include_{index}").lower() not in {"on", "1", "yes"}:
            continue
        payload = {
            "date": form_value(fields, f"date_{index}"),
            "invoice_number": form_value(fields, f"invoice_number_{index}"),
            "customer": form_value(fields, f"customer_{index}"),
            "amount": form_value(fields, f"amount_{index}"),
            "due_date": form_value(fields, f"due_date_{index}"),
            "status": form_value(fields, f"status_{index}"),
            "balance_due": form_value(fields, f"balance_due_{index}"),
            "source_pdf": form_value(fields, f"source_pdf_{index}"),
        }
        if not any(payload.get(key) for key in ("invoice_number", "customer", "amount")):
            continue
        db.add_invoice(conn, payload)
        saved_count += 1
    if saved_count == 0:
        raise ValueError("Select at least one invoice to save")
    return saved_count


def form_value(fields: dict[str, list[str]], key: str) -> str:
    values = fields.get(key)
    if not values:
        return ""
    return values[0].strip()


def save_uploaded_file(file_info: FileInfo | None) -> Path:
    if not file_info or not file_info.get("content"):
        raise ValueError("Choose an attachment to import")
    content = file_info["content"]
    if not isinstance(content, bytes):
        raise ValueError("Attachment content was not readable")
    if len(content) > 25 * 1024 * 1024:
        raise ValueError("Attachment is too large. Keep uploads under 25 MB.")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    original_name = safe_filename(str(file_info.get("filename") or "attachment"))
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = UPLOAD_DIR / f"{stamp}-{original_name}"
    counter = 1
    while target.exists():
        target = UPLOAD_DIR / f"{stamp}-{counter}-{original_name}"
        counter += 1
    target.write_bytes(content)
    return target


def save_optional_uploaded_file(file_info: FileInfo | None) -> Path | None:
    if not file_info or not file_info.get("content"):
        return None
    return save_uploaded_file(file_info)


def save_optional_uploaded_files(files: list[FileInfo] | None) -> list[Path]:
    return [save_uploaded_file(file_info) for file_info in uploaded_file_list(files)]


def join_attachment_paths(paths: list[Path]) -> str:
    return "\n".join(str(path) for path in paths)


def split_attachment_paths(path_value: str | None) -> list[str]:
    if not path_value:
        return []
    values = re.split(r"[\n|]+", str(path_value))
    return [value.strip() for value in values if value.strip()]


def safe_filename(filename: str) -> str:
    name = Path(filename).name.strip() or "attachment"
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip(".-") or "attachment"


def content_type_for(path: Path) -> str:
    suffix = path.suffix.lower()
    return {
        ".pdf": "application/pdf",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
        ".xls": "application/vnd.ms-excel",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".doc": "application/msword",
        ".txt": "text/plain; charset=utf-8",
        ".csv": "text/csv; charset=utf-8",
        ".tsv": "text/tab-separated-values; charset=utf-8",
    }.get(suffix, "application/octet-stream")


def attachment_link(path_value: str | None) -> str:
    paths = split_attachment_paths(path_value)
    if not paths:
        return ""
    links = []
    for index, path_text in enumerate(paths, start=1):
        path = Path(path_text)
        name = path.name
        label = "View" if len(paths) == 1 else f"View {index}"
        try:
            resolved = path.resolve()
            upload_root = UPLOAD_DIR.resolve()
        except OSError:
            links.append(esc(name))
            continue
        if not str(resolved).startswith(str(upload_root)) or not resolved.exists():
            links.append(esc(name))
            continue
        links.append(f'<a class="table-link" href="/attachments/{quote(name)}" target="_blank">{esc(label)}</a>')
    return '<span class="attachment-links">' + "".join(links) + "</span>"


def flatten_form(fields: dict[str, list[str]]) -> dict[str, str]:
    return {key: values[0].strip() for key, values in fields.items() if values and values[0].strip() != ""}


def esc(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def money(value) -> str:
    return f"${db.amount_value(value):,.2f}"


def signed_money(value) -> str:
    numeric = db.amount_value(value)
    css = "positive" if numeric >= 0 else "negative"
    sign = "" if numeric >= 0 else "-"
    return f'<span class="{css}">{sign}${abs(numeric):,.2f}</span>'


def nav_link(path: str, label: str, current: str) -> str:
    active = "active" if path == current else ""
    return f'<a class="{active}" href="{path}"><span>{esc(label)}</span></a>'


def layout(
    conn,
    title: str,
    subtitle: str,
    current: str,
    content: str,
    flash: str | None = None,
    page_actions: str | None = None,
) -> str:
    flash_html = f'<div class="flash">{esc(flash)}</div>' if flash else ""
    metrics = db.dashboard_metrics(conn)
    title_kicker = subtitle.upper() if subtitle else "OPERATIONS"
    actions_html = page_actions if page_actions is not None else """
          <a class="button muted" href="/">New view</a>
          <a class="button" href="/reports">Create report</a>
    """
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{esc(title)} · DataFold IT</title>
  <link rel="stylesheet" href="/static/styles.css?v={static_version("styles.css")}">
  <script src="/static/app.js?v={static_version("app.js")}" defer></script>
</head>
<body>
  <div class="app-shell">
    <header class="app-header">
      <div class="header-main">
        <a class="brand" href="/">
          <div class="brand-mark">DF</div>
          <div class="brand-name">
            <strong>DataFoldIT</strong>
            <span>Your Vision. Our Expertise</span>
          </div>
        </a>
        <div class="header-actions">
          <span class="status-pill">Balance {money(metrics["current_balance"])}</span>
          <a class="button ghost" href="/reports">Export</a>
          <a class="button ghost" href="/logout">Sign out</a>
        </div>
      </div>
      <nav class="nav">
        {nav_link("/", "Dashboard", current)}
        {nav_link("/bank", "Bank", current)}
        {nav_link("/expenses", "Expenses", current)}
        {nav_link("/payroll", "Payroll", current)}
        {nav_link("/invoices", "Invoices", current)}
        {nav_link("/reports", "Reports", current)}
      </nav>
    </header>
    <main class="main">
      <section class="page-head">
        <div class="page-title">
          <span>{esc(title_kicker)}</span>
          <h1>{esc(title)}</h1>
        </div>
        <div class="page-actions">
          {actions_html}
        </div>
      </section>
      <section class="content">
        {flash_html}
        {content}
      </section>
    </main>
  </div>
</body>
</html>"""


def static_version(filename: str) -> str:
    path = STATIC_DIR / filename
    try:
        return str(int(path.stat().st_mtime))
    except OSError:
        return "1"


def login_page(error: str | None = None) -> str:
    error_html = f'<div class="flash">{esc(error)}</div>' if error else ""
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Sign in · DataFold IT</title>
  <link rel="stylesheet" href="/static/styles.css?v={static_version("styles.css")}">
</head>
<body class="login-page">
  <form class="login-card" method="post" action="/login">
    <div class="login-brand">
      <div class="brand-mark">DF</div>
      <div class="brand-name">
        <strong>DataFoldIT</strong>
        <span>Your Vision. Our Expertise</span>
      </div>
    </div>
    <p>Operations workspace</p>
    {error_html}
    <label>Password
      <input type="password" name="password" autocomplete="current-password" required autofocus>
    </label>
    <div style="height:14px"></div>
    <button class="button" type="submit">Sign in</button>
  </form>
</body>
</html>"""


def render_dashboard(conn, flash: str | None = None) -> str:
    metrics = db.dashboard_metrics(conn)
    monthly_rows = db.monthly_bank_summary(conn)
    recent_rows = db.recent_activity(conn)
    action_bar = """
    <div class="action-bar">
      <a class="button" href="/bank">New bank entry</a>
      <a class="button secondary" href="/invoices">New invoice</a>
      <a class="button secondary" href="/reports">Reports</a>
    </div>
    """
    cards = f"""
    <div class="grid cols-4">
      {metric_card("Bank Balance", money(metrics["current_balance"]), "Current account", "good")}
      {metric_card("Expenses", money(metrics["total_expenses"]), f'{metrics["active_month"]}: {money(metrics["month_expenses"])}', "warn")}
      {metric_card("Invoices Open", money(metrics["invoice_outstanding"]), f'Total invoiced {money(metrics["invoice_total"])}', "info")}
      {metric_card("Commission", money(metrics["payroll_commission"]), f'Gross payroll {money(metrics["payroll_gross"])}', "good")}
    </div>
    """
    hero = f"""
    <section class="hero-card">
      <div>
        <span class="eyebrow">Company Account</span>
        <h2>{money(metrics["current_balance"])}</h2>
        <p>DataFold IT operating balance</p>
        <div class="hero-stats">
          <div><span>Receivable</span><strong>{money(metrics["invoice_outstanding"])}</strong></div>
          <div><span>Monthly spend</span><strong>{money(metrics["month_expenses"])}</strong></div>
        </div>
      </div>
      <div class="gauge" style="--pct: 78"><span>78%</span></div>
    </section>
    """
    trend_card = f"""
    <section class="panel chart-panel">
      <div class="panel-header"><h2>Cash Movement</h2><span>Last periods</span></div>
      <div class="sparkline">
        <i style="height:32%"></i><i style="height:44%"></i><i style="height:40%"></i><i style="height:64%"></i>
        <i style="height:52%"></i><i style="height:74%"></i><i style="height:46%"></i><i style="height:88%"></i>
        <i style="height:62%"></i><i style="height:70%"></i><i style="height:56%"></i><i style="height:92%"></i>
      </div>
    </section>
    """
    monthly_table = render_table(
        ["Month", "Opening", "Deposits", "Expenses", "Net", "Closing", "# Txns"],
        [
            [
                row["month"],
                money(row["opening"]),
                money(row["deposits"]),
                money(row["expenses"]),
                signed_money(row["net"]),
                money(row["closing"]),
                row["transactions"],
            ]
            for row in monthly_rows
        ],
        raw_columns={4},
        money_columns={1, 2, 3, 5},
    )
    recent_table = render_table(
        ["Date", "Section", "Description", "Amount"],
        [[row["date"], row["section"], row["label"], signed_money(row["amount"])] for row in recent_rows],
        raw_columns={3},
        money_columns={3},
    )
    content = f"""
    <div class="dashboard-grid">
      {hero}
      {trend_card}
    </div>
    <div style="height:22px"></div>
    {cards}
    <div style="height:22px"></div>
    {panel("Monthly Bank Summary", monthly_table)}
    <div style="height:22px"></div>
    {panel("Recent Activity", recent_table)}
    """
    return layout(conn, "Dashboard", "Overview", "/", content, flash)


def metric_card(label: str, value: str, note: str, tone: str = "info") -> str:
    return f'<div class="metric {esc(tone)}"><span>{esc(label)}</span><strong>{value}</strong><small>{esc(note)}</small><b></b></div>'


def panel(title: str, body: str) -> str:
    if body.lstrip().startswith('<div class="panel-body'):
        panel_body = body
    else:
        panel_body = f'<div class="table-wrap">{body}</div>'
    return f'<section class="panel"><div class="panel-header"><h2>{esc(title)}</h2></div>{panel_body}</section>'


def section_stack(*sections: str) -> str:
    return '<div class="section-stack">' + "".join(sections) + "</div>"


TRANSACTION_TYPE_OPTIONS = ["Deposit", "Expense", "Opening", "Transfer In", "Transfer Out", "Adjustment In", "Adjustment Out"]


def transaction_type_options_html(selected: str | None = None) -> str:
    return "".join(
        f'<option value="{esc(option)}"{" selected" if selected == option else ""}>{esc(option)}</option>'
        for option in TRANSACTION_TYPE_OPTIONS
    )


def bank_source_spend_summary(rows: list[sqlite3.Row]) -> list[tuple[str, float]]:
    totals: dict[str, float] = {}
    for row in rows:
        source = str(row["source"] or "").strip()
        if not source:
            continue
        totals[source] = totals.get(source, 0.0) + abs(db.amount_value(row["amount"]))
    return sorted(totals.items(), key=lambda item: item[0].lower())


def bank_ledger_filter_form(filters: dict[str, str], source_summary: list[tuple[str, float]]) -> str:
    selected_source = filters.get("source", "")
    source_options = ['<option value="">All paid by / sources</option>']
    source_options.extend(
        f'<option value="{esc(source)}"{" selected" if selected_source == source else ""}>{esc(source)} - {money(total)}</option>'
        for source, total in source_summary
    )
    hidden_fields = ""
    if filters.get("year"):
        hidden_fields += f'<input type="hidden" name="year" value="{esc(filters["year"])}">'
    if filters.get("month"):
        hidden_fields += f'<input type="hidden" name="month" value="{esc(filters["month"])}">'
    clear_href = "/bank"
    query_parts = []
    if filters.get("year"):
        query_parts.append(("year", filters["year"]))
    if filters.get("month"):
        query_parts.append(("month", filters["month"]))
    if query_parts:
        clear_href = f"/bank?{urlencode(query_parts)}"
    clear_button = f'<a class="button muted compact" href="{esc(clear_href)}">Clear source</a>' if selected_source else ""
    return f"""
    <form class="filter-form ledger-filter-form" method="get" action="/bank">
      {hidden_fields}
      <label class="wide">Filter source
        <select name="source">{''.join(source_options)}</select>
      </label>
      <button class="button compact" type="submit">Apply</button>
      {clear_button}
    </form>
    """


def render_bank(conn, flash: str | None = None, filters: dict[str, str] | None = None) -> str:
    filters = {**{"year": "", "month": "", "source": "", "sort": "date", "direction": "desc"}, **(filters or {})}
    all_rows = db.rows_for_table(conn, "bank_transactions")
    period_rows = filter_rows_by_period(all_rows, "date", filters)
    rows = sort_bank_rows(filter_bank_rows(all_rows, filters), filters)
    metrics = db.dashboard_metrics(conn)
    signed_values = [db.bank_signed_amount(row) for row in rows]
    deposits = sum(max(value, 0) for value in signed_values)
    outflows = sum(abs(min(value, 0)) for value in signed_values)
    scope = period_label(filters)
    kpis = f"""
    <div class="grid cols-4">
      {metric_card("Balance", money(metrics["current_balance"]), "Primary account")}
      {metric_card("Transactions", str(len(rows)), scope)}
      {metric_card("Deposits", money(deposits), "Visible ledger")}
      {metric_card("Outflows", money(outflows), "Visible ledger")}
    </div>
    """
    smart_form = """
    <form method="post" action="/bank/extract" enctype="multipart/form-data" class="form-grid">
      <label class="span-4">Source files
        <input type="file" name="attachment" multiple required>
      </label>
      <div class="span-4 actions"><button class="button secondary" type="submit">Read transaction file(s) and review</button></div>
    </form>
    """
    form = f"""
    <form method="post" action="/bank/create" enctype="multipart/form-data" class="form-grid">
      {input_field("date", "Date", "date", required=True)}
      {select_field("type", "Type", TRANSACTION_TYPE_OPTIONS, required=True)}
      {input_field("category", "Category", "text")}
      {input_field("amount", "Amount", "number", step="0.01", required=True)}
      {input_field("detail", "Vendor / Detail", "text", css="span-2")}
      {datalist_field("source", "Paid By / Source", db.distinct_values(conn, "bank_transactions", "source"))}
      {input_field("notes", "Notes", "text")}
      <label class="span-4">Attachments
        <input type="file" name="attachment" multiple>
      </label>
      <div class="span-4 actions"><button class="button" type="submit">Save transaction</button></div>
    </form>
    """
    table_rows = []
    row_attrs = []
    for row in rows:
        form_id = f"bank-row-form-{row['id']}"
        row_attrs.append(ledger_row_attr(f"bank-row-{row['id']}"))
        table_rows.append(
            [
                editable_input(form_id, "date", row["date"], row["date"], "date", required=True),
                editable_select(form_id, "type", row["type"], TRANSACTION_TYPE_OPTIONS, row["type"], required=True),
                editable_input(form_id, "category", row["category"], row["category"]),
                editable_input(form_id, "detail", row["detail"], row["detail"]),
                editable_input(form_id, "source", row["source"], row["source"]),
                editable_input(form_id, "amount", money(row["amount"]), currency_input(row["amount"]), "number", step="0.01", required=True),
                signed_money(db.bank_signed_amount(row)),
                attachment_link(row["attachment_path"] if "attachment_path" in row.keys() else None),
                action_controls(
                    form_id,
                    "/bank/update",
                    "transaction_id",
                    row["id"],
                    delete_control("/bank/delete", "transaction_id", row["id"], row["detail"] or row["date"], "transaction"),
                    row["detail"] or row["date"],
                    "transaction",
                    hidden_fields=[("notes", row["notes"])],
                ),
            ]
        )
    table = render_table(
        [
            sort_header("Date", "date", filters, "/bank", ["year", "month", "source"]),
            sort_header("Type", "type", filters, "/bank", ["year", "month", "source"]),
            sort_header("Category", "category", filters, "/bank", ["year", "month", "source"]),
            sort_header("Detail", "detail", filters, "/bank", ["year", "month", "source"]),
            sort_header("Source", "source", filters, "/bank", ["year", "month", "source"]),
            sort_header("Amount", "amount", filters, "/bank", ["year", "month", "source"]),
            sort_header("Signed", "signed", filters, "/bank", ["year", "month", "source"]),
            sort_header("Attachment", "attachment", filters, "/bank", ["year", "month", "source"]),
            "Action",
        ],
        table_rows,
        raw_columns=set(range(9)),
        money_columns={5, 6},
        raw_headers=set(range(8)),
        row_attrs=row_attrs,
    )
    smart_panel = f'<div class="panel-body">{smart_form}</div>'
    form_panel = f'<div class="panel-body">{form}</div>'
    ledger_panel = f'<div class="panel-body ledger-filter-body">{bank_ledger_filter_form(filters, bank_source_spend_summary(period_rows))}</div><div class="table-wrap">{table}</div>'
    content = section_stack(kpis, panel("Smart Transaction Import", smart_panel), panel("New Bank Transaction", form_panel), panel("Bank Ledger", ledger_panel))
    return layout(conn, "Bank", "Ledger", "/bank", content, flash, bank_filter_form(conn, filters))


def render_bank_edit(conn, transaction_id: int, flash: str | None = None) -> str:
    row = conn.execute("SELECT * FROM bank_transactions WHERE id = ?", (transaction_id,)).fetchone()
    if row is None:
        return layout(conn, "Edit Transaction", "Bank", "/bank", panel("Transaction Not Found", '<div class="panel-body">That transaction was not found.</div>'), flash, '<a class="button muted" href="/bank">Back to bank</a>')
    current_attachment = attachment_link(row["attachment_path"] if "attachment_path" in row.keys() else None)
    form = f"""
    <form method="post" action="/bank/update" enctype="multipart/form-data" class="form-grid">
      <input type="hidden" name="transaction_id" value="{esc(row["id"])}">
      {input_field("date", "Date", "date", value=row["date"], required=True)}
      {select_field("type", "Type", TRANSACTION_TYPE_OPTIONS, selected=row["type"], required=True)}
      {input_field("category", "Category", "text", value=row["category"])}
      {input_field("amount", "Amount", "number", value=currency_input(row["amount"]), step="0.01", required=True)}
      {input_field("detail", "Vendor / Detail", "text", value=row["detail"], css="span-2")}
      {datalist_field("source", "Paid By / Source", db.distinct_values(conn, "bank_transactions", "source"), value=row["source"])}
      {input_field("notes", "Notes", "text", value=row["notes"])}
      <label class="span-4">Attachments
        <input type="file" name="attachment" multiple>
      </label>
      <div class="span-4 current-attachment">{current_attachment}</div>
      <div class="span-4 actions">
        <button class="button" type="submit">Update transaction</button>
        <a class="button secondary" href="/bank">Cancel</a>
      </div>
    </form>
    """
    return layout(conn, "Edit Transaction", "Bank", "/bank", section_stack(panel("Edit Bank Transaction", f'<div class="panel-body">{form}</div>')), flash, '<a class="button muted" href="/bank">Back to bank</a>')


def render_transaction_review(conn, extracted: dict, flash: str | None = None) -> str:
    raw_excerpt = esc(extracted.get("raw_text_excerpt") or "No readable text captured.")
    attachment_path = extracted.get("attachment_path") or ""
    attachment_html = attachment_link(attachment_path)
    confidence = extracted.get("confidence") or 0
    form = f"""
    <form method="post" action="/bank/create" class="form-grid">
      {input_field("date", "Date", "date", value=extracted.get("date"), required=True)}
      {select_field("type", "Type", TRANSACTION_TYPE_OPTIONS, selected=extracted.get("type") or "Expense", required=True)}
      {input_field("category", "Category", "text", value=extracted.get("category"))}
      {input_field("amount", "Amount", "number", value=currency_input(extracted.get("amount")), step="0.01", required=True)}
      {input_field("detail", "Vendor / Detail", "text", value=extracted.get("detail"), css="span-2")}
      {input_field("source", "Paid By / Source", "text", value=extracted.get("source"))}
      {input_field("notes", "Notes", "text", value=extracted.get("notes"))}
      <input type="hidden" name="attachment_path" value="{esc(attachment_path)}">
      <div class="span-4 actions">
        <button class="button" type="submit">Save imported transaction</button>
        <a class="button secondary" href="/bank">Cancel</a>
      </div>
    </form>
    """
    meta = f"""
    <div class="review-meta">
      <span>Confidence {esc(confidence)}%</span>
      {attachment_html}
    </div>
    """
    form_panel = f'<div class="panel-body">{meta}{form}</div>'
    excerpt_panel = f'<div class="panel-body"><pre class="ocr-box">{raw_excerpt}</pre></div>'
    content = section_stack(panel("Review Imported Transaction", form_panel), panel("Extracted Text", excerpt_panel))
    return layout(conn, "Transaction Review", "Bank", "/bank", content, flash, '<a class="button muted" href="/bank">Back to bank</a>')


def render_transaction_bulk_review(conn, extracted_rows: list[dict], flash: str | None = None) -> str:
    form_index = 0
    rows_html = []
    for extracted in extracted_rows:
        attachment_path = extracted.get("attachment_path") or ""
        source_name = extracted.get("_source_name") or Path(attachment_path).name or "Uploaded file"
        attachment_html = attachment_link(attachment_path) or esc(source_name)
        if not extracted.get("_ok"):
            rows_html.append(
                f"""
                <tr class="bulk-error-row">
                  <td></td>
                  <td>{attachment_html}</td>
                  <td colspan="7"><strong>Could not read this file.</strong> {esc(extracted.get("error") or "")}</td>
                </tr>
                """
            )
            continue
        tx_type = extracted.get("type") or "Expense"
        rows_html.append(
            f"""
            <tr>
              <td><input class="bulk-check" type="checkbox" name="include_{form_index}" aria-label="Save {esc(source_name)}" checked></td>
              <td>{attachment_html}<input type="hidden" name="attachment_path_{form_index}" value="{esc(attachment_path)}"></td>
              <td>{bulk_input(f"date_{form_index}", "Date", "date", extracted.get("date"), required=True)}</td>
              <td><select name="type_{form_index}" aria-label="Type">{transaction_type_options_html(tx_type)}</select></td>
              <td>{bulk_input(f"category_{form_index}", "Category", "text", extracted.get("category"))}</td>
              <td>{bulk_input(f"detail_{form_index}", "Vendor / Detail", "text", extracted.get("detail"), required=True)}</td>
              <td>{bulk_input(f"source_{form_index}", "Paid by / Source", "text", extracted.get("source"))}</td>
              <td>{bulk_input(f"amount_{form_index}", "Amount", "number", currency_input(extracted.get("amount")), step="0.01", required=True)}</td>
              <td>{bulk_input(f"notes_{form_index}", "Notes", "text", extracted.get("notes"))}</td>
            </tr>
            """
        )
        form_index += 1
    disabled = " disabled" if form_index == 0 else ""
    table = f"""
    <form method="post" action="/bank/create-bulk" class="bulk-review-form">
      <input type="hidden" name="row_count" value="{form_index}">
      <div class="table-wrap">
        <table class="bulk-review-table">
          <thead>
            <tr>
              <th>Save</th>
              <th>File</th>
              <th>Date</th>
              <th>Type</th>
              <th>Category</th>
              <th>Detail</th>
              <th>Source</th>
              <th>Amount</th>
              <th>Notes</th>
            </tr>
          </thead>
          <tbody>{''.join(rows_html)}</tbody>
        </table>
      </div>
      <div class="actions">
        <button class="button" type="submit"{disabled}>Save selected transactions</button>
        <a class="button secondary" href="/bank">Cancel</a>
      </div>
    </form>
    """
    content = section_stack(panel("Bulk Transaction Review", f'<div class="panel-body">{table}</div>'))
    return layout(conn, "Bulk Transaction Review", "Bank", "/bank", content, flash, '<a class="button muted" href="/bank">Back to bank</a>')


def expense_paid_by_summary(rows: list[sqlite3.Row]) -> list[tuple[str, float]]:
    totals: dict[str, float] = {}
    for row in rows:
        paid_by = str(row["paid_by"] or "").strip()
        if not paid_by:
            continue
        totals[paid_by] = totals.get(paid_by, 0.0) + abs(db.amount_value(row["amount"]))
    return sorted(totals.items(), key=lambda item: item[0].lower())


def expense_ledger_filter_form(filters: dict[str, str], paid_by_summary: list[tuple[str, float]]) -> str:
    selected_paid_by = filters.get("paid_by", "")
    paid_by_options = ['<option value="">All paid by</option>']
    paid_by_options.extend(
        f'<option value="{esc(paid_by)}"{" selected" if selected_paid_by == paid_by else ""}>{esc(paid_by)} - {money(total)}</option>'
        for paid_by, total in paid_by_summary
    )
    hidden_fields = ""
    if filters.get("year"):
        hidden_fields += f'<input type="hidden" name="year" value="{esc(filters["year"])}">'
    if filters.get("month"):
        hidden_fields += f'<input type="hidden" name="month" value="{esc(filters["month"])}">'
    clear_href = "/expenses"
    query_parts = []
    if filters.get("year"):
        query_parts.append(("year", filters["year"]))
    if filters.get("month"):
        query_parts.append(("month", filters["month"]))
    if query_parts:
        clear_href = f"/expenses?{urlencode(query_parts)}"
    clear_button = f'<a class="button muted compact" href="{esc(clear_href)}">Clear paid by</a>' if selected_paid_by else ""
    return f"""
    <form class="filter-form ledger-filter-form" method="get" action="/expenses">
      {hidden_fields}
      <label class="wide">Filter paid by
        <select name="paid_by">{''.join(paid_by_options)}</select>
      </label>
      <button class="button compact" type="submit">Apply</button>
      {clear_button}
    </form>
    """


def render_expenses(conn, flash: str | None = None, filters: dict[str, str] | None = None) -> str:
    filters = {**{"year": "", "month": "", "paid_by": "", "sort": "date", "direction": "desc"}, **(filters or {})}
    all_rows = db.rows_for_table(conn, "expenses")
    period_rows = filter_rows_by_period(all_rows, "date", filters)
    rows = sort_expense_rows(filter_expense_rows(all_rows, filters), filters)
    total_spend = sum(db.amount_value(row["amount"]) for row in rows)
    scope = period_label(filters)
    kpis = f"""
    <div class="grid cols-4">
      {metric_card("Total Spend", money(total_spend), scope)}
      {metric_card("Expense Rows", str(len(rows)), "Visible log")}
      {metric_card("Average", money(total_spend / max(len(rows), 1)), "Per expense")}
      {metric_card("Period", esc(scope), "Filter")}
    </div>
    """
    form = f"""
    <form method="post" action="/expenses/create" enctype="multipart/form-data" class="form-grid">
      {input_field("date", "Date", "date", required=True)}
      {input_field("category", "Category", "text")}
      {input_field("vendor", "Vendor", "text")}
      {input_field("amount", "Amount", "number", step="0.01", required=True)}
      {input_field("description", "Description", "text", css="span-2")}
      {datalist_field("paid_by", "Paid By", db.distinct_values(conn, "expenses", "paid_by"))}
      {select_field("frequency", "Frequency", ["One-time", "Monthly", "Yearly", "Recurring"])}
      <label class="span-4">Attachments
        <input type="file" name="attachment" multiple>
      </label>
      {textarea_field("notes", "Notes", "span-4")}
      <div class="span-4 actions"><button class="button" type="submit">Save expense</button></div>
    </form>
    """
    table_rows = []
    row_attrs = []
    for row in rows:
        form_id = f"expense-row-form-{row['id']}"
        row_attrs.append(ledger_row_attr(f"expense-row-{row['id']}"))
        table_rows.append(
            [
                editable_input(form_id, "date", row["date"], row["date"], "date", required=True),
                editable_input(form_id, "category", row["category"], row["category"]),
                editable_input(form_id, "vendor", row["vendor"], row["vendor"]),
                editable_input(form_id, "description", row["description"], row["description"]),
                editable_input(form_id, "amount", money(row["amount"]), currency_input(row["amount"]), "number", step="0.01", required=True),
                editable_input(form_id, "paid_by", row["paid_by"], row["paid_by"]),
                editable_select(form_id, "frequency", row["frequency"], ["One-time", "Monthly", "Yearly", "Recurring"], row["frequency"]),
                editable_input(form_id, "notes", row["notes"], row["notes"]),
                attachment_link(row["attachment_path"] if "attachment_path" in row.keys() else None),
                action_controls(
                    form_id,
                    "/expenses/update",
                    "expense_id",
                    row["id"],
                    delete_control("/expenses/delete", "expense_id", row["id"], row["vendor"] or row["description"] or row["date"], "expense"),
                    row["vendor"] or row["description"] or row["date"],
                    "expense",
                ),
            ]
        )
    table = render_table(
        [
            sort_header("Date", "date", filters, "/expenses", ["year", "month", "paid_by"]),
            sort_header("Category", "category", filters, "/expenses", ["year", "month", "paid_by"]),
            sort_header("Vendor", "vendor", filters, "/expenses", ["year", "month", "paid_by"]),
            sort_header("Description", "description", filters, "/expenses", ["year", "month", "paid_by"]),
            sort_header("Amount", "amount", filters, "/expenses", ["year", "month", "paid_by"]),
            sort_header("Paid By", "paid_by", filters, "/expenses", ["year", "month", "paid_by"]),
            sort_header("Frequency", "frequency", filters, "/expenses", ["year", "month", "paid_by"]),
            sort_header("Notes", "notes", filters, "/expenses", ["year", "month", "paid_by"]),
            sort_header("Attachment", "attachment", filters, "/expenses", ["year", "month", "paid_by"]),
            "Action",
        ],
        table_rows,
        raw_columns=set(range(10)),
        money_columns={4},
        raw_headers=set(range(9)),
        row_attrs=row_attrs,
    )
    form_panel = f'<div class="panel-body">{form}</div>'
    expense_log_body = f'<div class="panel-body ledger-filter-body">{expense_ledger_filter_form(filters, expense_paid_by_summary(period_rows))}</div><div class="table-wrap">{table}</div>'
    content = section_stack(kpis, panel("New Business Expense", form_panel), panel("Expense Log", expense_log_body))
    return layout(conn, "Expenses", "Spend", "/expenses", content, flash, expense_filter_form(conn, filters))


def render_expense_edit(conn, expense_id: int, flash: str | None = None) -> str:
    row = conn.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,)).fetchone()
    if row is None:
        return layout(conn, "Edit Expense", "Expenses", "/expenses", panel("Expense Not Found", '<div class="panel-body">That expense was not found.</div>'), flash, '<a class="button muted" href="/expenses">Back to expenses</a>')
    current_attachment = attachment_link(row["attachment_path"] if "attachment_path" in row.keys() else None)
    form = f"""
    <form method="post" action="/expenses/update" enctype="multipart/form-data" class="form-grid">
      <input type="hidden" name="expense_id" value="{esc(row["id"])}">
      {input_field("date", "Date", "date", value=row["date"], required=True)}
      {input_field("category", "Category", "text", value=row["category"])}
      {input_field("vendor", "Vendor", "text", value=row["vendor"])}
      {input_field("amount", "Amount", "number", value=currency_input(row["amount"]), step="0.01", required=True)}
      {input_field("description", "Description", "text", value=row["description"], css="span-2")}
      {datalist_field("paid_by", "Paid By", db.distinct_values(conn, "expenses", "paid_by"), value=row["paid_by"])}
      {select_field("frequency", "Frequency", ["One-time", "Monthly", "Yearly", "Recurring"], selected=row["frequency"])}
      <label class="span-4">Attachments
        <input type="file" name="attachment" multiple>
      </label>
      {textarea_field("notes", "Notes", "span-4", value=row["notes"])}
      <div class="span-4 current-attachment">{current_attachment}</div>
      <div class="span-4 actions">
        <button class="button" type="submit">Update expense</button>
        <a class="button secondary" href="/expenses">Cancel</a>
      </div>
    </form>
    """
    return layout(conn, "Edit Expense", "Expenses", "/expenses", section_stack(panel("Edit Business Expense", f'<div class="panel-body">{form}</div>')), flash, '<a class="button muted" href="/expenses">Back to expenses</a>')


def render_payroll(conn, flash: str | None = None, filters: dict[str, str] | None = None) -> str:
    filters = {**{"year": "", "month": "", "candidate": "", "sort": "month", "direction": "desc"}, **(filters or {})}
    rows = sort_payroll_rows(filter_payroll_rows(db.rows_for_table(conn, "payroll_entries"), filters), filters)
    gross = sum(db.amount_value(row["gross"]) for row in rows)
    commission = sum(db.amount_value(row["commission"]) for row in rows)
    employee_pay = sum(db.amount_value(row["employee_pay"]) for row in rows)
    scope = payroll_label(filters)
    kpis = f"""
    <div class="grid cols-4">
      {metric_card("Gross", money(gross), scope)}
      {metric_card("Commission", money(commission), "Company share")}
      {metric_card("Employee Pay", money(employee_pay), "Credits")}
      {metric_card("Entries", str(len(rows)), "Visible rows")}
    </div>
    """
    paystub_form = """
    <form method="post" action="/payroll/extract" enctype="multipart/form-data" class="form-grid">
      <label class="span-4">Paystub files
        <input type="file" name="attachment" multiple required>
      </label>
      <div class="span-4 actions"><button class="button secondary" type="submit">Read paystub file(s) and review</button></div>
    </form>
    """
    form = f"""
    <form method="post" action="/payroll/create" enctype="multipart/form-data" class="form-grid" data-payroll-form>
      {input_field("month", "Month", "month", required=True)}
      {datalist_field("first_name", "First Name", db.distinct_values(conn, "payroll_entries", "first_name"))}
      {datalist_field("last_name", "Last Name", db.distinct_values(conn, "payroll_entries", "last_name"))}
      {datalist_field("client", "Client", db.distinct_values(conn, "payroll_entries", "client"))}
      {input_field("vendor", "Vendor", "text", css="span-2")}
      {input_field("job_start", "Job Start", "date")}
      {input_field("job_end", "Job End", "date")}
      {input_field("vendor_pay", "Pay Rate / Hour", "number", step="0.01")}
      {input_field("pct", "Commission %", "number", value="30", step="0.01")}
      {input_field("hours", "Hours", "number", step="0.01")}
      {input_field("gross", "Gross", "number", step="0.01")}
      {input_field("commission", "Commission", "number", step="0.01")}
      {input_field("employee_pay", "Payroll After Commission", "number", step="0.01")}
      {input_field("credit_date", "Credit Date", "date")}
      {select_field("paystub_sent", "Paystub Sent", ["No", "Yes"], selected="No")}
      <label class="span-4">Attachments
        <input type="file" name="attachment" multiple>
      </label>
      <div class="span-4 actions"><button class="button" type="submit">Save payroll</button></div>
    </form>
    """
    table_rows = []
    row_attrs = []
    for row in rows:
        form_id = f"payroll-row-form-{row['id']}"
        employee_label = payroll_employee_name(row) or row["month"]
        paystub_sent_label = "Yes" if str(row["paystub_sent"] or "").upper() == "Y" else "No"
        row_attrs.append(ledger_row_attr(f"payroll-row-{row['id']}"))
        table_rows.append(
            [
                editable_input(form_id, "month", row["month"], row["month"], "month", required=True),
                editable_name(form_id, row),
                editable_input(form_id, "vendor", row["vendor"], row["vendor"]),
                editable_input(form_id, "client", row["client"], row["client"]),
                editable_input(form_id, "hours", row["hours"], currency_input(row["hours"]), "number", step="0.01"),
                editable_input(form_id, "gross", money(row["gross"]), currency_input(row["gross"]), "number", step="0.01"),
                editable_input(form_id, "commission", money(row["commission"]), currency_input(row["commission"]), "number", step="0.01"),
                editable_input(form_id, "employee_pay", money(row["employee_pay"]), currency_input(row["employee_pay"]), "number", step="0.01"),
                editable_input(form_id, "credit_date", row["credit_date"], row["credit_date"], "date"),
                editable_select(form_id, "paystub_sent", paystub_sent_label, ["No", "Yes"], paystub_sent_label),
                attachment_link(row["attachment_path"] if "attachment_path" in row.keys() else None),
                action_controls(
                    form_id,
                    "/payroll/update",
                    "payroll_id",
                    row["id"],
                    delete_control(
                        "/payroll/delete",
                        "payroll_id",
                        row["id"],
                        employee_label,
                        "payroll entry",
                    ),
                    employee_label,
                    "payroll entry",
                    hidden_fields=[
                        ("job_start", row["job_start"]),
                        ("job_end", row["job_end"]),
                        ("vendor_pay", currency_input(row["vendor_pay"])),
                        ("pct", currency_input(row["pct"])),
                    ],
                ),
            ]
        )
    table = render_table(
        [
            sort_header("Month", "month", filters, "/payroll", ["year", "month", "candidate"]),
            sort_header("Name", "name", filters, "/payroll", ["year", "month", "candidate"]),
            sort_header("Vendor", "vendor", filters, "/payroll", ["year", "month", "candidate"]),
            sort_header("Client", "client", filters, "/payroll", ["year", "month", "candidate"]),
            sort_header("Hours", "hours", filters, "/payroll", ["year", "month", "candidate"]),
            sort_header("Gross", "gross", filters, "/payroll", ["year", "month", "candidate"]),
            sort_header("Commission", "commission", filters, "/payroll", ["year", "month", "candidate"]),
            sort_header("Employee Pay", "employee_pay", filters, "/payroll", ["year", "month", "candidate"]),
            sort_header("Credit Date", "credit_date", filters, "/payroll", ["year", "month", "candidate"]),
            sort_header("Paystub Sent", "paystub_sent", filters, "/payroll", ["year", "month", "candidate"]),
            sort_header("Attachment", "attachment", filters, "/payroll", ["year", "month", "candidate"]),
            "Action",
        ],
        table_rows,
        raw_columns=set(range(12)),
        money_columns={5, 6, 7},
        raw_headers=set(range(11)),
        row_attrs=row_attrs,
    )
    paystub_panel = f'<div class="panel-body">{paystub_form}</div>'
    form_panel = f'<div class="panel-body">{form}</div>'
    content = section_stack(kpis, panel("Read Paystub Files", paystub_panel), panel("New Payroll Entry", form_panel), panel("Payroll Ledger", table))
    return layout(conn, "Payroll", "Payroll", "/payroll", content, flash, payroll_filter_form(conn, filters))


def render_paystub_review(conn, extracted: dict, flash: str | None = None) -> str:
    raw_excerpt = esc(extracted.get("raw_text_excerpt") or "No readable text captured.")
    attachment_path = extracted.get("attachment_path") or ""
    attachment_html = attachment_link(attachment_path)
    form = f"""
    <form method="post" action="/payroll/create" class="form-grid" data-payroll-form>
      {input_field("month", "Month", "month", value=extracted.get("month"), required=True)}
      {datalist_field("first_name", "First Name", db.distinct_values(conn, "payroll_entries", "first_name"), value=extracted.get("first_name"))}
      {datalist_field("last_name", "Last Name", db.distinct_values(conn, "payroll_entries", "last_name"), value=extracted.get("last_name"))}
      {datalist_field("client", "Client", db.distinct_values(conn, "payroll_entries", "client"), value=extracted.get("client"))}
      {input_field("vendor", "Vendor", "text", value=extracted.get("vendor"), css="span-2")}
      {input_field("job_start", "Job Start", "date", value=extracted.get("job_start"))}
      {input_field("job_end", "Job End", "date", value=extracted.get("job_end"))}
      {input_field("vendor_pay", "Pay Rate / Hour", "number", value=currency_input(extracted.get("vendor_pay")), step="0.01")}
      {input_field("pct", "Commission %", "number", value=currency_input(extracted.get("pct")), step="0.01")}
      {input_field("hours", "Hours", "number", value=currency_input(extracted.get("hours")), step="0.01")}
      {input_field("gross", "Gross", "number", value=currency_input(extracted.get("gross")), step="0.01")}
      {input_field("commission", "Commission", "number", value=currency_input(extracted.get("commission")), step="0.01")}
      {input_field("employee_pay", "Payroll After Commission", "number", value=currency_input(extracted.get("employee_pay")), step="0.01")}
      {input_field("credit_date", "Credit Date", "date", value=extracted.get("credit_date"))}
      {select_field("paystub_sent", "Paystub Sent", ["No", "Yes"], selected="Yes" if extracted.get("paystub_sent") == "Y" else "No")}
      <input type="hidden" name="attachment_path" value="{esc(attachment_path)}">
      <div class="span-4 actions">
        <button class="button" type="submit">Save imported paystub</button>
        <a class="button secondary" href="/payroll">Cancel</a>
      </div>
    </form>
    """
    meta = f'<div class="review-meta">{attachment_html}</div>'
    content = section_stack(
        panel("Review Imported Paystub", f'<div class="panel-body">{meta}{form}</div>'),
        panel("Extracted Text", f'<div class="panel-body"><pre class="ocr-box">{raw_excerpt}</pre></div>'),
    )
    return layout(conn, "Paystub Review", "Payroll", "/payroll", content, flash, '<a class="button muted" href="/payroll">Back to payroll</a>')


def render_paystub_bulk_review(conn, extracted_rows: list[dict], flash: str | None = None) -> str:
    form_index = 0
    rows_html = []
    for extracted in extracted_rows:
        attachment_path = extracted.get("attachment_path") or ""
        source_name = extracted.get("_source_name") or Path(attachment_path).name or "Uploaded file"
        attachment_html = attachment_link(attachment_path) or esc(source_name)
        if not extracted.get("_ok"):
            rows_html.append(
                f"""
                <tr class="bulk-error-row">
                  <td></td>
                  <td>{attachment_html}</td>
                  <td colspan="9"><strong>Could not read this paystub.</strong> {esc(extracted.get("error") or "")}</td>
                </tr>
                """
            )
            continue
        rows_html.append(
            f"""
            <tr>
              <td><input class="bulk-check" type="checkbox" name="include_{form_index}" aria-label="Save {esc(source_name)}" checked></td>
              <td>{attachment_html}<input type="hidden" name="attachment_path_{form_index}" value="{esc(attachment_path)}"></td>
              <td>{bulk_input(f"month_{form_index}", "Month", "month", extracted.get("month"), required=True)}</td>
              <td>{bulk_input(f"first_name_{form_index}", "First name", "text", extracted.get("first_name"))}</td>
              <td>{bulk_input(f"last_name_{form_index}", "Last name", "text", extracted.get("last_name"))}</td>
              <td>{bulk_input(f"vendor_pay_{form_index}", "Rate", "number", currency_input(extracted.get("vendor_pay")), step="0.01")}</td>
              <td>{bulk_input(f"hours_{form_index}", "Hours", "number", currency_input(extracted.get("hours")), step="0.01")}</td>
              <td>{bulk_input(f"gross_{form_index}", "Gross", "number", currency_input(extracted.get("gross")), step="0.01")}</td>
              <td>{bulk_input(f"commission_{form_index}", "Deductions", "number", currency_input(extracted.get("commission")), step="0.01")}</td>
              <td>{bulk_input(f"employee_pay_{form_index}", "Net pay", "number", currency_input(extracted.get("employee_pay")), step="0.01")}</td>
              <td>{bulk_input(f"credit_date_{form_index}", "Payment date", "date", extracted.get("credit_date"))}</td>
              <td><select name="paystub_sent_{form_index}" aria-label="Paystub sent"><option value="Y" selected>Yes</option><option value="N">No</option></select></td>
              <input type="hidden" name="vendor_{form_index}" value="{esc(extracted.get("vendor") or "")}">
              <input type="hidden" name="client_{form_index}" value="{esc(extracted.get("client") or "")}">
              <input type="hidden" name="job_start_{form_index}" value="{esc(extracted.get("job_start") or "")}">
              <input type="hidden" name="job_end_{form_index}" value="{esc(extracted.get("job_end") or "")}">
              <input type="hidden" name="pct_{form_index}" value="{esc(extracted.get("pct") or "")}">
            </tr>
            """
        )
        form_index += 1
    disabled = " disabled" if form_index == 0 else ""
    table = f"""
    <form method="post" action="/payroll/create-bulk" class="bulk-review-form">
      <input type="hidden" name="row_count" value="{form_index}">
      <div class="table-wrap">
        <table class="bulk-review-table">
          <thead>
            <tr>
              <th>Save</th>
              <th>File</th>
              <th>Month</th>
              <th>First</th>
              <th>Last</th>
              <th>Rate</th>
              <th>Hours</th>
              <th>Gross</th>
              <th>Deductions</th>
              <th>Net</th>
              <th>Payment Date</th>
              <th>Paystub Sent</th>
            </tr>
          </thead>
          <tbody>{''.join(rows_html)}</tbody>
        </table>
      </div>
      <div class="actions">
        <button class="button" type="submit"{disabled}>Save selected paystubs</button>
        <a class="button secondary" href="/payroll">Cancel</a>
      </div>
    </form>
    """
    content = section_stack(panel("Bulk Paystub Review", f'<div class="panel-body">{table}</div>'))
    return layout(conn, "Bulk Paystub Review", "Payroll", "/payroll", content, flash, '<a class="button muted" href="/payroll">Back to payroll</a>')


INVOICE_STATUS_OPTIONS = ["Received", "Not Received", "Void"]
INVOICE_SORT_KEYS = {"date", "invoice_number", "customer", "status", "due_status", "due_date", "amount", "balance_due", "attachment"}


def invoice_status_options_html(selected: str | None = None) -> str:
    selected = selected or "Not Received"
    return "".join(
        f'<option value="{esc(option)}"{" selected" if selected == option else ""}>{esc(option)}</option>'
        for option in INVOICE_STATUS_OPTIONS
    )


def invoice_status_select(selected: str | None = None) -> str:
    return f'<label>Status<select name="status" required>{invoice_status_options_html(selected)}</select></label>'


def invoice_status_choice(status: str | None, received: str | None = None, is_void: str | int | bool | None = None) -> str:
    status_value = str(status or "").strip().lower()
    received_value = str(received or "").strip().upper()
    if db.bool_value(is_void) or status_value in {"void", "voided"}:
        return "Void"
    if status_value in {"received", "paid"} or received_value == "Y":
        return "Received"
    return "Not Received"


def invoice_status_label(row) -> str:
    return invoice_status_choice(row["status"], row["received"], row["is_void"])


def invoice_is_open(row) -> bool:
    return invoice_status_label(row) == "Not Received" and not db.bool_value(row["is_void"])


def invoice_due_days(row, today: date | None = None) -> int | None:
    due_date = db.normalize_date(row["due_date"])
    if not due_date:
        return None
    today = today or date.today()
    try:
        due = date.fromisoformat(due_date)
    except ValueError:
        return None
    return (due - today).days


def invoice_due_status(row, today: date | None = None) -> str:
    if db.bool_value(row["is_void"]):
        return "Void"
    if invoice_status_label(row) == "Received":
        return "Paid"
    days = invoice_due_days(row, today)
    if days is None:
        return "No due date"
    if days < 0:
        return "Overdue"
    if days == 0:
        return "Due today"
    return f"Due in {days}d"


def invoice_due_status_badge(row) -> str:
    status = invoice_due_status(row)
    css = "overdue" if status == "Overdue" else "paid" if status in {"Paid", "Void"} else "due"
    return f'<span class="status-badge {css}">{esc(status)}</span>'


def sort_invoice_rows(rows: list[sqlite3.Row], filters: dict[str, str]) -> list[sqlite3.Row]:
    sort_key = filters.get("sort", "date")
    reverse = filters.get("direction", "desc") == "desc"

    def value(row: sqlite3.Row):
        if sort_key == "status":
            return invoice_status_label(row).lower()
        if sort_key == "due_status":
            days = invoice_due_days(row)
            if days is None:
                return 999999
            return days
        if sort_key in {"amount", "balance_due"}:
            return db.amount_value(row[sort_key])
        if sort_key == "attachment":
            return str(row["source_pdf"] or "").lower()
        return str(row[sort_key] or "").lower()

    return sorted(rows, key=value, reverse=reverse)


def sort_header(label: str, key: str, filters: dict[str, str], path: str, preserve_keys: list[str]) -> str:
    current_key = filters.get("sort", "")
    current_direction = filters.get("direction", "desc")
    next_direction = "asc" if current_key != key or current_direction == "desc" else "desc"
    query = {name: filters.get(name, "") for name in preserve_keys if filters.get(name)}
    query.update({"sort": key, "direction": next_direction})
    active = current_key == key
    suffix = f" {current_direction}" if active else ""
    return f'<a class="sort-link{" active" if active else ""}" href="{esc(path)}?{urlencode(query)}">{esc(label)}<span>{esc(suffix)}</span></a>'


def invoice_sort_header(label: str, key: str, filters: dict[str, str]) -> str:
    return sort_header(label, key, filters, "/invoices", ["year", "month", "customer", "status"])


def invoice_status_inline_control(row) -> str:
    selected = invoice_status_label(row)
    return f"""
    <form class="inline-status-form" method="post" action="/invoices/status" data-inline-status-form>
      <input type="hidden" name="invoice_id" value="{esc(row["id"])}">
      <select name="status" aria-label="Invoice status">{invoice_status_options_html(selected)}</select>
    </form>
    """


def delete_control(action: str, id_name: str, id_value, label: str, entity: str) -> str:
    return f"""
    <form class="inline-delete-form" method="post" action="{esc(action)}" data-inline-delete-form data-confirm-message="Are you sure you want to delete {esc(entity)} {esc(label)}? This cannot be undone.">
      <input type="hidden" name="{esc(id_name)}" value="{esc(id_value)}">
      <button class="button danger compact icon-button" type="submit" aria-label="Delete {esc(entity)} {esc(label)}" title="Delete">
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M3 6h18"></path>
          <path d="M8 6V4h8v2"></path>
          <path d="M6 6l1 14h10l1-14"></path>
          <path d="M10 11v5"></path>
          <path d="M14 11v5"></path>
        </svg>
      </button>
    </form>
    """


def edit_control(target_id: str, label: str, entity: str) -> str:
    return f"""
    <button class="button muted compact icon-button edit-icon-button" type="button" data-inline-edit-toggle aria-label="Edit {esc(entity)} {esc(label)}" title="Edit">
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M4 20h4l11-11a2.8 2.8 0 0 0-4-4L4 16v4z"></path>
        <path d="M13 6l5 5"></path>
      </svg>
    </button>
    """


def save_control(form_id: str, label: str, entity: str) -> str:
    return f"""
    <button class="button compact icon-button inline-save-button" type="submit" form="{esc(form_id)}" aria-label="Save {esc(entity)} {esc(label)}" title="Save" hidden>
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M20 6L9 17l-5-5"></path>
      </svg>
    </button>
    """


def cancel_control(label: str, entity: str) -> str:
    return f"""
    <button class="button secondary compact icon-button inline-cancel-button" type="button" data-inline-edit-cancel aria-label="Cancel {esc(entity)} {esc(label)}" title="Cancel" hidden>
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M18 6L6 18"></path>
        <path d="M6 6l12 12"></path>
      </svg>
    </button>
    """


def row_form(form_id: str, action: str, id_name: str, id_value, hidden_fields: list[tuple[str, str | None]] | None = None) -> str:
    hidden_html = "".join(
        f'<input type="hidden" name="{esc(name)}" value="{esc("" if value is None else value)}">'
        for name, value in (hidden_fields or [])
    )
    return f"""
    <form id="{esc(form_id)}" class="inline-row-form" method="post" action="{esc(action)}">
      <input type="hidden" name="{esc(id_name)}" value="{esc(id_value)}">
      {hidden_html}
    </form>
    """


def action_controls(
    form_id: str,
    action: str,
    id_name: str,
    id_value,
    delete_html: str,
    label: str,
    entity: str,
    hidden_fields: list[tuple[str, str | None]] | None = None,
) -> str:
    return (
        '<div class="row-actions">'
        + row_form(form_id, action, id_name, id_value, hidden_fields)
        + edit_control(form_id, label, entity)
        + save_control(form_id, label, entity)
        + cancel_control(label, entity)
        + delete_html
        + "</div>"
    )


def ledger_row_attr(row_id: str) -> str:
    return f'data-ledger-row="{esc(row_id)}" data-inline-edit-row'


def editable_input(
    form_id: str,
    name: str,
    display,
    value: str | None = None,
    input_type: str = "text",
    step: str | None = None,
    required: bool = False,
) -> str:
    editor_value = "" if value is None else str(value)
    display_value = display if display not in {None, ""} else editor_value
    step_attr = f' step="{esc(step)}"' if step else ""
    required_attr = " required" if required else ""
    return (
        f'<span class="cell-view">{esc(display_value)}</span>'
        f'<input class="cell-editor" type="{esc(input_type)}" name="{esc(name)}" '
        f'value="{esc(editor_value)}" form="{esc(form_id)}" data-original="{esc(editor_value)}"'
        f'{step_attr}{required_attr} disabled>'
    )


def editable_select(form_id: str, name: str, display, options: list[str], selected: str | None = None, required: bool = False) -> str:
    selected_value = selected or ""
    required_attr = " required" if required else ""
    option_html = '<option value=""></option>' + "".join(
        f'<option value="{esc(option)}"{" selected" if selected_value == option else ""}>{esc(option)}</option>'
        for option in options
    )
    return (
        f'<span class="cell-view">{esc(display or selected_value)}</span>'
        f'<select class="cell-editor" name="{esc(name)}" form="{esc(form_id)}" data-original="{esc(selected_value)}"{required_attr} disabled>'
        f'{option_html}</select>'
    )


def editable_name(form_id: str, row) -> str:
    first_name = row["first_name"] or ""
    last_name = row["last_name"] or ""
    display_name = payroll_employee_name(row)
    return (
        f'<span class="cell-view">{esc(display_name)}</span>'
        f'<input class="cell-editor" type="text" name="first_name" value="{esc(first_name)}" '
        f'form="{esc(form_id)}" data-original="{esc(first_name)}" placeholder="First name" disabled>'
        f'<input class="cell-editor" type="text" name="last_name" value="{esc(last_name)}" '
        f'form="{esc(form_id)}" data-original="{esc(last_name)}" placeholder="Last name" disabled>'
    )


def invoice_delete_control(row) -> str:
    label = row["invoice_number"] or "this invoice"
    return delete_control("/invoices/delete", "invoice_id", row["id"], label, "invoice")


def render_invoice_edit(conn, invoice_id: int, flash: str | None = None) -> str:
    row = conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
    if row is None:
        return layout(conn, "Edit Invoice", "Invoices", "/invoices", panel("Invoice Not Found", '<div class="panel-body">That invoice was not found.</div>'), flash, '<a class="button muted" href="/invoices">Back to invoices</a>')
    current_attachment = attachment_link(row["source_pdf"] if "source_pdf" in row.keys() else None)
    form = f"""
    <form method="post" action="/invoices/update" enctype="multipart/form-data" class="form-grid">
      <input type="hidden" name="invoice_id" value="{esc(row["id"])}">
      {input_field("date", "Date", "date", value=row["date"], required=True)}
      {input_field("invoice_number", "Invoice #", "text", value=row["invoice_number"], required=True)}
      {datalist_field("customer", "Customer / Client", db.distinct_values(conn, "invoices", "customer"), value=row["customer"], required=True)}
      {input_field("amount", "Amount", "number", value=currency_input(row["amount"]), step="0.01", required=True)}
      {input_field("due_date", "Due Date", "date", value=row["due_date"])}
      {invoice_status_select(invoice_status_label(row))}
      {input_field("balance_due", "Balance Due", "number", value=currency_input(row["balance_due"]), step="0.01")}
      <label class="span-4">Attachments
        <input type="file" name="attachment" multiple>
      </label>
      <div class="span-4 current-attachment">{current_attachment}</div>
      <div class="span-4 actions">
        <button class="button" type="submit">Update invoice</button>
        <a class="button secondary" href="/invoices">Cancel</a>
      </div>
    </form>
    """
    return layout(conn, "Edit Invoice", "Invoices", "/invoices", section_stack(panel("Edit Invoice", f'<div class="panel-body">{form}</div>')), flash, '<a class="button muted" href="/invoices">Back to invoices</a>')


def render_invoices(conn, flash: str | None = None, filters: dict[str, str] | None = None) -> str:
    filters = filters or {"year": "", "month": "", "customer": "", "status": "", "sort": "date", "direction": "desc"}
    rows = sort_invoice_rows(filter_invoice_rows(db.rows_for_table(conn, "invoices"), filters), filters)
    next_number = db.next_invoice_number(conn)
    customer_options = db.distinct_values(conn, "invoices", "customer")
    active_rows = [row for row in rows if not row["is_void"]]
    invoice_total = sum(db.amount_value(row["amount"]) for row in active_rows)
    invoice_paid = sum(db.amount_value(row["amount"]) for row in active_rows if invoice_status_label(row) == "Received")
    invoice_outstanding = sum(db.amount_value(row["balance_due"]) for row in active_rows)
    overdue_rows = [row for row in active_rows if invoice_is_open(row) and invoice_due_status(row) == "Overdue"]
    invoice_overdue = sum(db.amount_value(row["balance_due"]) for row in overdue_rows)
    scope = period_label(filters)
    kpis = f"""
    <div class="grid cols-4">
      {metric_card("Total Invoice", money(invoice_total), scope)}
      {metric_card("Received", money(invoice_paid), "Closed")}
      {metric_card("Outstanding", money(invoice_outstanding), "Receivable")}
      {metric_card("Overdue", money(invoice_overdue), f"{len(overdue_rows)} past due", "warn")}
    </div>
    """
    extract_form = """
    <form method="post" action="/invoices/extract" enctype="multipart/form-data" class="form-grid" data-auto-upload-form>
      <label class="span-4">Invoice files
        <input type="file" name="attachment" multiple data-auto-submit-file>
      </label>
      <label class="span-4">File path
        <input type="text" name="pdf_path" placeholder="/Users/vamsikrishnabhashyam/Downloads/invoice.pdf">
      </label>
      <div class="span-4 actions"><button class="button secondary" type="submit">Read invoice file(s) and review</button></div>
    </form>
    """
    form = f"""
    <form method="post" action="/invoices/create" enctype="multipart/form-data" class="form-grid">
      {input_field("date", "Date", "date", required=True)}
      {input_field("invoice_number", "Invoice #", "text", value=next_number, required=True)}
      {datalist_field("customer", "Customer / Client", customer_options, required=True)}
      {input_field("amount", "Amount", "number", step="0.01", required=True)}
      {input_field("due_date", "Due Date", "date")}
      {invoice_status_select()}
      {input_field("balance_due", "Balance Due", "number", step="0.01")}
      <label class="span-4">Attachments
        <input type="file" name="attachment" multiple>
      </label>
      <div class="span-4 actions"><button class="button" type="submit">Save invoice</button></div>
    </form>
    """
    table_rows = []
    row_attrs = []
    for row in rows:
        form_id = f"invoice-row-form-{row['id']}"
        row_attrs.append(ledger_row_attr(f"invoice-row-{row['id']}"))
        table_rows.append(
            [
                editable_input(form_id, "date", row["date"], row["date"], "date", required=True),
                editable_input(form_id, "invoice_number", row["invoice_number"], row["invoice_number"], required=True),
                editable_input(form_id, "customer", row["customer"], row["customer"], required=True),
                editable_select(form_id, "status", invoice_status_label(row), INVOICE_STATUS_OPTIONS, invoice_status_label(row), required=True),
                invoice_due_status_badge(row),
                editable_input(form_id, "due_date", row["due_date"], row["due_date"], "date"),
                editable_input(form_id, "amount", money(row["amount"]), currency_input(row["amount"]), "number", step="0.01", required=True),
                editable_input(form_id, "balance_due", money(row["balance_due"]), currency_input(row["balance_due"]), "number", step="0.01"),
                attachment_link(row["source_pdf"] if "source_pdf" in row.keys() else None),
                action_controls(
                    form_id,
                    "/invoices/update",
                    "invoice_id",
                    row["id"],
                    invoice_delete_control(row),
                    row["invoice_number"] or "this invoice",
                    "invoice",
                ),
            ]
        )
    table = render_table(
        [
            invoice_sort_header("Date", "date", filters),
            invoice_sort_header("Invoice #", "invoice_number", filters),
            invoice_sort_header("Customer", "customer", filters),
            invoice_sort_header("Status", "status", filters),
            invoice_sort_header("Due Status", "due_status", filters),
            invoice_sort_header("Due Date", "due_date", filters),
            invoice_sort_header("Amount", "amount", filters),
            invoice_sort_header("Balance Due", "balance_due", filters),
            invoice_sort_header("Attachment", "attachment", filters),
            "Action",
        ],
        table_rows,
        raw_columns=set(range(10)),
        money_columns={6, 7},
        raw_headers=set(range(9)),
        row_attrs=row_attrs,
    )
    extract_panel = f'<div class="panel-body">{extract_form}</div>'
    form_panel = f'<div class="panel-body">{form}</div>'
    content = section_stack(kpis, panel("Read Invoice Files", extract_panel), panel("New Invoice", form_panel), panel("Invoice Ledger", table))
    return layout(conn, "Invoices", "Receivables", "/invoices", content, flash, invoice_filter_form(conn, filters))


def render_invoice_review(conn, extracted: dict, flash: str | None = None) -> str:
    raw_excerpt = esc(extracted.get("raw_text_excerpt") or "No OCR text captured.")
    source_pdf = extracted.get("source_pdf") or ""
    attachment_html = attachment_link(source_pdf)
    form = f"""
    <form method="post" action="/invoices/create" class="form-grid">
      {input_field("date", "Date", "date", value=extracted.get("date"), required=True)}
      {input_field("invoice_number", "Invoice #", "text", value=extracted.get("invoice_number") or db.next_invoice_number(conn), required=True)}
      {datalist_field("customer", "Customer / Client", db.distinct_values(conn, "invoices", "customer"), value=extracted.get("customer"), required=True)}
      {input_field("amount", "Amount", "number", value=currency_input(extracted.get("amount")), step="0.01", required=True)}
      {input_field("due_date", "Due Date", "date", value=extracted.get("due_date"))}
      {invoice_status_select(invoice_status_choice(extracted.get("status"), extracted.get("received"), extracted.get("is_void")))}
      {input_field("balance_due", "Balance Due", "number", value=currency_input(extracted.get("balance_due")), step="0.01")}
      <input type="hidden" name="source_pdf" value="{esc(source_pdf)}">
      <div class="span-4 actions">
        <button class="button" type="submit">Save reviewed invoice</button>
        <a class="button secondary" href="/invoices">Cancel</a>
      </div>
    </form>
    """
    meta = f"""
    <div class="review-meta">
      <span>Source invoice</span>
      {attachment_html}
    </div>
    """
    excerpt = f'<pre class="ocr-box">{raw_excerpt}</pre>'
    form_panel = f'<div class="panel-body">{meta}{form}</div>'
    excerpt_panel = f'<div class="panel-body">{excerpt}</div>'
    content = section_stack(panel("Review Extracted Invoice", form_panel), panel("OCR Text Excerpt", excerpt_panel))
    return layout(conn, "Invoice PDF Review", "Review", "/invoices", content, flash)


def render_invoice_bulk_review(conn, extracted_rows: list[dict], flash: str | None = None) -> str:
    fallback_numbers = invoice_number_sequence(db.next_invoice_number(conn), len(extracted_rows))
    fallback_index = 0
    form_index = 0
    rows_html = []
    for extracted in extracted_rows:
        source_pdf = extracted.get("source_pdf") or ""
        source_name = extracted.get("_source_name") or Path(source_pdf).name or "Uploaded file"
        source_html = attachment_link(source_pdf) or esc(source_name)
        if not extracted.get("_ok"):
            rows_html.append(
                f"""
                <tr class="bulk-error-row">
                  <td></td>
                  <td>{source_html}</td>
                  <td colspan="7"><strong>Could not read this file.</strong> {esc(extracted.get("error") or "")}</td>
                </tr>
                """
            )
            continue
        invoice_number = extracted.get("invoice_number") or fallback_numbers[fallback_index]
        fallback_index += 1
        status = invoice_status_choice(extracted.get("status"), extracted.get("received"), extracted.get("is_void"))
        rows_html.append(
            f"""
            <tr>
              <td><input class="bulk-check" type="checkbox" name="include_{form_index}" aria-label="Save {esc(source_name)}" checked></td>
              <td>{source_html}<input type="hidden" name="source_pdf_{form_index}" value="{esc(source_pdf)}"></td>
              <td>{bulk_input(f"date_{form_index}", "Date", "date", extracted.get("date"), required=True)}</td>
              <td>{bulk_input(f"invoice_number_{form_index}", "Invoice number", "text", invoice_number, required=True)}</td>
              <td>{bulk_input(f"customer_{form_index}", "Customer", "text", extracted.get("customer"), required=True)}</td>
              <td><select name="status_{form_index}" aria-label="Status">{invoice_status_options_html(status)}</select></td>
              <td>{bulk_input(f"due_date_{form_index}", "Due date", "date", extracted.get("due_date"))}</td>
              <td>{bulk_input(f"amount_{form_index}", "Amount", "number", currency_input(extracted.get("amount")), step="0.01", required=True)}</td>
              <td>{bulk_input(f"balance_due_{form_index}", "Balance due", "number", currency_input(extracted.get("balance_due")), step="0.01")}</td>
            </tr>
            """
        )
        form_index += 1
    disabled = " disabled" if form_index == 0 else ""
    table = f"""
    <form method="post" action="/invoices/create-bulk" class="bulk-review-form">
      <input type="hidden" name="row_count" value="{form_index}">
      <div class="table-wrap">
        <table class="bulk-review-table">
          <thead>
            <tr>
              <th>Save</th>
              <th>File</th>
              <th>Date</th>
              <th>Invoice #</th>
              <th>Customer</th>
              <th>Status</th>
              <th>Due Date</th>
              <th>Amount</th>
              <th>Balance Due</th>
            </tr>
          </thead>
          <tbody>{''.join(rows_html)}</tbody>
        </table>
      </div>
      <div class="actions">
        <button class="button" type="submit"{disabled}>Save selected invoices</button>
        <a class="button secondary" href="/invoices">Cancel</a>
      </div>
    </form>
    """
    content = section_stack(panel("Bulk Invoice Review", f'<div class="panel-body">{table}</div>'))
    return layout(conn, "Bulk Invoice Review", "Review", "/invoices", content, flash)


def bulk_input(
    name: str,
    label: str,
    input_type: str,
    value: str | None = None,
    required: bool = False,
    step: str | None = None,
) -> str:
    value_attr = f' value="{esc(value)}"' if value is not None else ""
    required_attr = " required" if required else ""
    step_attr = f' step="{esc(step)}"' if step else ""
    return f'<input type="{esc(input_type)}" name="{esc(name)}" aria-label="{esc(label)}"{value_attr}{step_attr}{required_attr}>'


def invoice_number_sequence(first_number: str, count: int) -> list[str]:
    match = re.match(r"^(.*?)(\d+)$", first_number or "")
    if not match:
        return [first_number or f"INV-{index + 1:06d}" for index in range(count)]
    prefix, digits = match.groups()
    start = int(digits)
    width = len(digits)
    return [f"{prefix}{start + index:0{width}d}" for index in range(count)]


def render_reports(conn, flash: str | None = None, filters: dict[str, str] | None = None) -> str:
    filters = filters or {"year": "", "month": ""}
    month = f"{filters['year']}-{filters['month']}" if filters.get("year") and filters.get("month") else db.active_month(conn)
    today = time.strftime("%Y-%m-%d")
    import_path = esc(str(DEFAULT_SOURCE_XLSX))
    metrics = db.dashboard_metrics(conn)
    scope = period_label(filters)
    selected_period = "monthly" if filters.get("year") and filters.get("month") else "all"
    kpis = f"""
    <div class="grid cols-4">
      {metric_card("Balance", money(metrics["current_balance"]), "Current")}
      {metric_card("Filter", esc(scope), "Selected view")}
      {metric_card("Exports", "Excel", "Workbook")}
      {metric_card("Backup", "SQLite", "Database")}
    </div>
    """
    controls = f"""
    <div class="grid cols-2">
      <section class="panel">
        <div class="panel-header"><h2>Excel Reports</h2></div>
        <div class="panel-body">
          <form class="form-grid" method="get" action="/export.xlsx">
            <label>Period
              <select name="period">
                <option value="all"{" selected" if selected_period == "all" else ""}>All time</option>
                <option value="monthly"{" selected" if selected_period == "monthly" else ""}>Monthly</option>
                <option value="daily">Daily</option>
              </select>
            </label>
            {input_field("month", "Month", "month", value=month)}
            {input_field("day", "Day", "date", value=today)}
            <div class="actions"><button class="button" type="submit">Download Excel</button></div>
          </form>
          <div style="height:14px"></div>
          <a class="button secondary" href="/backup.db">Download database backup</a>
        </div>
      </section>
      <section class="panel">
        <div class="panel-header"><h2>Import Workbook</h2></div>
        <div class="panel-body">
          <form class="form-grid" method="post" action="/import">
            {input_field("workbook_path", "Workbook Path", "text", value=import_path, css="span-4")}
            <label><span>Replace existing records</span><select name="replace"><option value="">No</option><option value="Y">Yes</option></select></label>
            <div class="span-4 actions"><button class="button warning" type="submit">Import workbook</button></div>
          </form>
        </div>
      </section>
    </div>
    """
    body = section_stack(kpis, controls, panel("Audit Log", render_audit_table(conn, filters)))
    return layout(conn, "Reports", "Exports", "/reports", body, flash, period_filter_form(conn, "/reports", "reports", filters))


def render_audit_table(conn, filters: dict[str, str] | None = None) -> str:
    filters = filters or {"year": "", "month": ""}
    rows = filter_rows_by_period(db.rows_for_table(conn, "audit_log", limit=80), "created_at", filters)
    return render_table(
        ["When", "Action", "Entity", "ID", "Details"],
        [[row["created_at"], row["action"], row["entity"], row["entity_id"], row["details"]] for row in rows],
    )


def render_table(
    headers: list[str],
    rows: list[list],
    raw_columns: set[int] | None = None,
    money_columns: set[int] | None = None,
    raw_headers: set[int] | None = None,
    row_attrs: list[str] | None = None,
    detail_rows: list[str] | None = None,
) -> str:
    raw_columns = raw_columns or set()
    money_columns = money_columns or set()
    raw_headers = raw_headers or set()
    if not rows:
        return '<div class="empty">No records yet.</div>'
    header_html = "".join(
        f'<th class="{"money" if index in money_columns else ""}">{header if index in raw_headers else esc(header)}</th>'
        for index, header in enumerate(headers)
    )
    row_html = []
    for row_index, row in enumerate(rows):
        cells = []
        for index, value in enumerate(row):
            content = str(value) if index in raw_columns else esc(value)
            css = "money" if index in money_columns else ""
            cells.append(f'<td class="{css}">{content}</td>')
        attrs = f" {row_attrs[row_index]}" if row_attrs and row_index < len(row_attrs) else ""
        row_html.append(f"<tr{attrs}>" + "".join(cells) + "</tr>")
        if detail_rows and row_index < len(detail_rows) and detail_rows[row_index]:
            row_html.append(detail_rows[row_index])
    return f"<table><thead><tr>{header_html}</tr></thead><tbody>{''.join(row_html)}</tbody></table>"


def input_field(
    name: str,
    label: str,
    input_type: str,
    value: str | None = None,
    css: str = "",
    required: bool = False,
    step: str | None = None,
) -> str:
    required_attr = " required" if required else ""
    step_attr = f' step="{esc(step)}"' if step else ""
    value_attr = f' value="{esc(value)}"' if value is not None else ""
    return f'<label class="{esc(css)}">{esc(label)}<input type="{esc(input_type)}" name="{esc(name)}"{value_attr}{step_attr}{required_attr}></label>'


def datalist_field(
    name: str,
    label: str,
    options: list[str],
    value: str | None = None,
    css: str = "",
    required: bool = False,
) -> str:
    required_attr = " required" if required else ""
    value_attr = f' value="{esc(value)}"' if value is not None else ""
    list_id = f"{safe_filename(name)}-options"
    option_html = "".join(f'<option value="{esc(option)}"></option>' for option in options)
    return (
        f'<label class="{esc(css)}">{esc(label)}'
        f'<input type="text" name="{esc(name)}" list="{esc(list_id)}"{value_attr}{required_attr}>'
        f'<datalist id="{esc(list_id)}">{option_html}</datalist></label>'
    )


def textarea_field(name: str, label: str, css: str = "", value: str | None = None) -> str:
    return f'<label class="{esc(css)}">{esc(label)}<textarea name="{esc(name)}">{esc(value or "")}</textarea></label>'


def select_field(name: str, label: str, options: list[str], selected: str | None = None, required: bool = False) -> str:
    required_attr = " required" if required else ""
    option_html = '<option value=""></option>' + "".join(
        f'<option value="{esc(option)}"{" selected" if selected == option else ""}>{esc(option)}</option>'
        for option in options
    )
    return f'<label>{esc(label)}<select name="{esc(name)}"{required_attr}>{option_html}</select></label>'


def currency_input(value) -> str | None:
    if value is None:
        return None
    amount = db.amount_value(value)
    return f"{amount:.2f}"
