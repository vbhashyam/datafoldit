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
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlparse

from . import db
from .excel_io import DEFAULT_SOURCE_XLSX, export_report_workbook, import_company_workbook
from .invoice_pdf import extract_invoice_from_pdf
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
                self.send_html(render_bank(self.conn, flash, filters))
            elif path == "/expenses":
                self.send_html(render_expenses(self.conn, flash, filters))
            elif path == "/payroll":
                self.send_html(render_payroll(self.conn, flash, payroll_filter_from_query(query)))
            elif path == "/invoices":
                self.send_html(render_invoices(self.conn, flash, invoice_filter_from_query(query)))
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
                    saved_path = save_uploaded_file(first_uploaded_file(files.get("attachment")))
                    extracted = extract_transaction_from_file(saved_path)
                    self.send_html(render_transaction_review(self.conn, extracted, "Review extracted transaction before saving"))
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
                fields = self.read_fields_with_optional_attachment()
                if parsed.path == "/bank/create":
                    db.add_bank_transaction(self.conn, fields)
                    self.conn.commit()
                    self.redirect("/bank?flash=Bank+transaction+saved")
                elif parsed.path == "/expenses/create":
                    db.add_expense(self.conn, fields)
                    self.conn.commit()
                    self.redirect("/expenses?flash=Expense+saved")
                elif parsed.path == "/payroll/create":
                    db.add_payroll_entry(self.conn, fields)
                    self.conn.commit()
                    self.redirect("/payroll?flash=Payroll+entry+saved")
                elif parsed.path == "/invoices/create":
                    db.add_invoice(self.conn, fields)
                    self.conn.commit()
                    self.redirect("/invoices?flash=Invoice+saved")
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
                saved_path = save_optional_uploaded_file(first_uploaded_file(files.get("attachment")))
                if saved_path:
                    fields["attachment_path"] = str(saved_path)
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
            content_type = {".css": "text/css", ".js": "application/javascript"}.get(
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


def payroll_filter_from_query(query: dict[str, list[str]]) -> dict[str, str]:
    filters = date_filter_from_query(query)
    filters["employee"] = (first(query, "employee") or "").strip()
    return filters


def invoice_filter_from_query(query: dict[str, list[str]]) -> dict[str, str]:
    filters = date_filter_from_query(query)
    filters["customer"] = (first(query, "customer") or "").strip()
    status = (first(query, "status") or "").strip()
    filters["status"] = status if status in set(INVOICE_STATUS_OPTIONS) else ""
    return filters


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
    employee = filters.get("employee", "")
    if not employee:
        return rows
    return [row for row in rows if payroll_employee_name(row) == employee]


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
    employee = filters.get("employee", "")
    period = period_label(filters)
    if employee and period != "All data":
        return f"{employee} · {period}"
    return employee or period


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


def payroll_filter_form(conn, filters: dict[str, str]) -> str:
    selected_employee = filters.get("employee", "")
    selected_year = filters.get("year", "")
    selected_month = filters.get("month", "")
    employee_options = ['<option value="">All employees</option>']
    employee_options.extend(
        f'<option value="{esc(employee)}"{" selected" if selected_employee == employee else ""}>{esc(employee)}</option>'
        for employee in payroll_employees(conn)
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
    clear_button = '<a class="button muted compact" href="/payroll">Clear</a>' if selected_employee or selected_year or selected_month else ""
    return f"""
    <form class="filter-form" method="get" action="/payroll">
      <label class="wide">Employee
        <select name="employee">{''.join(employee_options)}</select>
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
    if not path_value:
        return ""
    path = Path(path_value)
    name = path.name
    try:
        resolved = path.resolve()
        upload_root = UPLOAD_DIR.resolve()
    except OSError:
        return esc(name)
    if not str(resolved).startswith(str(upload_root)) or not resolved.exists():
        return esc(name)
    return f'<a class="table-link" href="/attachments/{quote(name)}" target="_blank">View</a>'


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
            <strong>DataFold IT</strong>
            <span>Operations</span>
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
    <div class="brand-mark">DF</div>
    <h1>DataFold IT Operations</h1>
    <p>Local finance workspace</p>
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
    if body.lstrip().startswith('<div class="panel-body"'):
        panel_body = body
    else:
        panel_body = f'<div class="table-wrap">{body}</div>'
    return f'<section class="panel"><div class="panel-header"><h2>{esc(title)}</h2></div>{panel_body}</section>'


def section_stack(*sections: str) -> str:
    return '<div class="section-stack">' + "".join(sections) + "</div>"


def render_bank(conn, flash: str | None = None, filters: dict[str, str] | None = None) -> str:
    filters = filters or {"year": "", "month": ""}
    rows = filter_rows_by_period(db.rows_for_table(conn, "bank_transactions"), "date", filters)
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
      <label class="span-4">Attachment
        <input type="file" name="attachment" accept=".pdf,.png,.jpg,.jpeg,.webp,.tif,.tiff,.bmp,.heic,.xlsx,.xlsm,.xls,.docx,.doc,.txt,.csv" required>
      </label>
      <div class="span-4 actions"><button class="button secondary" type="submit">Read and review transaction</button></div>
    </form>
    """
    form = f"""
    <form method="post" action="/bank/create" class="form-grid">
      {input_field("date", "Date", "date", required=True)}
      {select_field("type", "Type", ["Deposit", "Expense", "Opening", "Transfer In", "Transfer Out", "Adjustment In", "Adjustment Out"], required=True)}
      {input_field("category", "Category", "text")}
      {input_field("amount", "Amount", "number", step="0.01", required=True)}
      {input_field("detail", "Vendor / Detail", "text", css="span-2")}
      {input_field("source", "Paid By / Source", "text")}
      {input_field("notes", "Notes", "text")}
      <div class="span-4 actions"><button class="button" type="submit">Save transaction</button></div>
    </form>
    """
    table = render_table(
        ["Date", "Type", "Category", "Detail", "Source", "Amount", "Signed", "Attachment"],
        [
            [
                row["date"],
                row["type"],
                row["category"],
                row["detail"],
                row["source"],
                money(row["amount"]),
                signed_money(db.bank_signed_amount(row)),
                attachment_link(row["attachment_path"] if "attachment_path" in row.keys() else None),
            ]
            for row in rows
        ],
        raw_columns={6, 7},
        money_columns={5, 6},
    )
    smart_panel = f'<div class="panel-body">{smart_form}</div>'
    form_panel = f'<div class="panel-body">{form}</div>'
    content = section_stack(kpis, panel("Smart Transaction Import", smart_panel), panel("New Bank Transaction", form_panel), panel("Bank Ledger", table))
    return layout(conn, "Bank", "Ledger", "/bank", content, flash, period_filter_form(conn, "/bank", "bank", filters))


def render_transaction_review(conn, extracted: dict, flash: str | None = None) -> str:
    raw_excerpt = esc(extracted.get("raw_text_excerpt") or "No readable text captured.")
    attachment_path = extracted.get("attachment_path") or ""
    attachment_html = attachment_link(attachment_path)
    confidence = extracted.get("confidence") or 0
    form = f"""
    <form method="post" action="/bank/create" class="form-grid">
      {input_field("date", "Date", "date", value=extracted.get("date"), required=True)}
      {select_field("type", "Type", ["Deposit", "Expense", "Opening", "Transfer In", "Transfer Out", "Adjustment In", "Adjustment Out"], selected=extracted.get("type") or "Expense", required=True)}
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


def render_expenses(conn, flash: str | None = None, filters: dict[str, str] | None = None) -> str:
    filters = filters or {"year": "", "month": ""}
    rows = filter_rows_by_period(db.rows_for_table(conn, "expenses"), "date", filters)
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
      {input_field("paid_by", "Paid By", "text")}
      {select_field("frequency", "Frequency", ["One-time", "Monthly", "Yearly", "Recurring"])}
      <label class="span-4">Attachment
        <input type="file" name="attachment" accept=".pdf,.png,.jpg,.jpeg,.webp,.tif,.tiff,.bmp,.heic,.xlsx,.xlsm,.xls,.docx,.doc,.txt,.csv">
      </label>
      {textarea_field("notes", "Notes", "span-4")}
      <div class="span-4 actions"><button class="button" type="submit">Save expense</button></div>
    </form>
    """
    table = render_table(
        ["Date", "Category", "Vendor", "Description", "Amount", "Paid By", "Frequency", "Notes", "Attachment"],
        [
            [
                row["date"],
                row["category"],
                row["vendor"],
                row["description"],
                money(row["amount"]),
                row["paid_by"],
                row["frequency"],
                row["notes"],
                attachment_link(row["attachment_path"] if "attachment_path" in row.keys() else None),
            ]
            for row in rows
        ],
        raw_columns={8},
        money_columns={4},
    )
    form_panel = f'<div class="panel-body">{form}</div>'
    content = section_stack(kpis, panel("New Business Expense", form_panel), panel("Expense Log", table))
    return layout(conn, "Expenses", "Spend", "/expenses", content, flash, period_filter_form(conn, "/expenses", "expenses", filters))


def render_payroll(conn, flash: str | None = None, filters: dict[str, str] | None = None) -> str:
    filters = filters or {"year": "", "month": "", "employee": ""}
    rows = filter_payroll_rows(db.rows_for_table(conn, "payroll_entries"), filters)
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
    form = f"""
    <form method="post" action="/payroll/create" enctype="multipart/form-data" class="form-grid" data-payroll-form>
      {input_field("month", "Month", "month", required=True)}
      {input_field("first_name", "First Name", "text")}
      {input_field("last_name", "Last Name", "text")}
      {input_field("client", "Client", "text")}
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
      <label class="span-4">Attachment
        <input type="file" name="attachment" accept=".pdf,.png,.jpg,.jpeg,.webp,.tif,.tiff,.bmp,.heic,.xlsx,.xlsm,.xls,.docx,.doc,.txt,.csv">
      </label>
      <div class="span-4 actions"><button class="button" type="submit">Save payroll</button></div>
    </form>
    """
    table = render_table(
        ["Month", "Name", "Vendor", "Client", "Hours", "Gross", "Commission", "Employee Pay", "Credit Date", "Attachment"],
        [
            [
                row["month"],
                f"{row['first_name'] or ''} {row['last_name'] or ''}".strip(),
                row["vendor"],
                row["client"],
                row["hours"],
                money(row["gross"]),
                money(row["commission"]),
                money(row["employee_pay"]),
                row["credit_date"],
                attachment_link(row["attachment_path"] if "attachment_path" in row.keys() else None),
            ]
            for row in rows
        ],
        raw_columns={9},
        money_columns={5, 6, 7},
    )
    form_panel = f'<div class="panel-body">{form}</div>'
    content = section_stack(kpis, panel("New Payroll Entry", form_panel), panel("Payroll Ledger", table))
    return layout(conn, "Payroll", "Payroll", "/payroll", content, flash, payroll_filter_form(conn, filters))


INVOICE_STATUS_OPTIONS = ["Received", "Not Received", "Void"]


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


def invoice_status_inline_control(row) -> str:
    selected = invoice_status_label(row)
    return f"""
    <form class="inline-status-form" method="post" action="/invoices/status" data-inline-status-form>
      <input type="hidden" name="invoice_id" value="{esc(row["id"])}">
      <select name="status" aria-label="Invoice status">{invoice_status_options_html(selected)}</select>
    </form>
    """


def invoice_delete_control(row) -> str:
    label = row["invoice_number"] or "this invoice"
    return f"""
    <form class="inline-delete-form" method="post" action="/invoices/delete" data-confirm-message="Are you sure you want to delete invoice {esc(label)}? This cannot be undone.">
      <input type="hidden" name="invoice_id" value="{esc(row["id"])}">
      <button class="button danger compact icon-button" type="submit" aria-label="Delete invoice {esc(label)}" title="Delete invoice">
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


def render_invoices(conn, flash: str | None = None, filters: dict[str, str] | None = None) -> str:
    filters = filters or {"year": "", "month": ""}
    rows = filter_invoice_rows(db.rows_for_table(conn, "invoices"), filters)
    next_number = db.next_invoice_number(conn)
    active_rows = [row for row in rows if not row["is_void"]]
    invoice_total = sum(db.amount_value(row["amount"]) for row in active_rows)
    invoice_paid = sum(db.amount_value(row["amount"]) for row in active_rows if invoice_status_label(row) == "Received")
    invoice_outstanding = sum(db.amount_value(row["balance_due"]) for row in active_rows)
    scope = period_label(filters)
    kpis = f"""
    <div class="grid cols-3">
      {metric_card("Total Invoice", money(invoice_total), scope)}
      {metric_card("Received", money(invoice_paid), "Closed")}
      {metric_card("Outstanding", money(invoice_outstanding), "Receivable")}
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
    <form method="post" action="/invoices/create" class="form-grid">
      {input_field("date", "Date", "date", required=True)}
      {input_field("invoice_number", "Invoice #", "text", value=next_number, required=True)}
      {input_field("customer", "Customer / Client", "text", required=True)}
      {input_field("amount", "Amount", "number", step="0.01", required=True)}
      {input_field("due_date", "Due Date", "date")}
      {invoice_status_select()}
      {input_field("balance_due", "Balance Due", "number", step="0.01")}
      <div class="span-4 actions"><button class="button" type="submit">Save invoice</button></div>
    </form>
    """
    table = render_table(
        ["Date", "Invoice #", "Customer", "Status", "Due Date", "Amount", "Balance Due", "Attachment", "Action"],
        [
            [
                row["date"],
                row["invoice_number"],
                row["customer"],
                invoice_status_inline_control(row),
                row["due_date"],
                money(row["amount"]),
                money(row["balance_due"]),
                attachment_link(row["source_pdf"] if "source_pdf" in row.keys() else None),
                invoice_delete_control(row),
            ]
            for row in rows
        ],
        raw_columns={3, 7, 8},
        money_columns={5, 6},
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
      {input_field("customer", "Customer / Client", "text", value=extracted.get("customer"), required=True)}
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


def render_table(headers: list[str], rows: list[list], raw_columns: set[int] | None = None, money_columns: set[int] | None = None) -> str:
    raw_columns = raw_columns or set()
    money_columns = money_columns or set()
    if not rows:
        return '<div class="empty">No records yet.</div>'
    header_html = "".join(
        f'<th class="{"money" if index in money_columns else ""}">{esc(header)}</th>'
        for index, header in enumerate(headers)
    )
    row_html = []
    for row in rows:
        cells = []
        for index, value in enumerate(row):
            content = str(value) if index in raw_columns else esc(value)
            css = "money" if index in money_columns else ""
            cells.append(f'<td class="{css}">{content}</td>')
        row_html.append("<tr>" + "".join(cells) + "</tr>")
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


def textarea_field(name: str, label: str, css: str = "") -> str:
    return f'<label class="{esc(css)}">{esc(label)}<textarea name="{esc(name)}"></textarea></label>'


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
