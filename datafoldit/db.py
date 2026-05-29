from __future__ import annotations

import json
import sqlite3
from collections import OrderedDict
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = ROOT / "data" / "datafoldit.db"

POSITIVE_BANK_TYPES = {"Opening", "Deposit", "Transfer In", "Adjustment In"}
NEGATIVE_BANK_TYPES = {"Expense", "Withdrawal", "Transfer Out", "Adjustment Out"}


def connect(db_path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS bank_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            opening_balance REAL NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS bank_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL REFERENCES bank_accounts(id),
            date TEXT NOT NULL,
            month TEXT,
            type TEXT NOT NULL,
            category TEXT,
            detail TEXT,
            source TEXT,
            amount REAL NOT NULL,
            attachment_path TEXT,
            notes TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            category TEXT,
            vendor TEXT,
            description TEXT,
            amount REAL NOT NULL,
            paid_by TEXT,
            frequency TEXT,
            attachment_path TEXT,
            notes TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS payroll_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            month TEXT NOT NULL,
            first_name TEXT,
            last_name TEXT,
            vendor TEXT,
            client TEXT,
            job_start TEXT,
            job_end TEXT,
            vendor_pay REAL DEFAULT 0,
            pct REAL DEFAULT 0,
            hours REAL DEFAULT 0,
            gross REAL DEFAULT 0,
            commission REAL DEFAULT 0,
            employee_pay REAL DEFAULT 0,
            credit_date TEXT,
            attachment_path TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS invoices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            invoice_number TEXT NOT NULL,
            customer TEXT,
            is_void INTEGER NOT NULL DEFAULT 0,
            received TEXT,
            due_date TEXT,
            amount REAL NOT NULL,
            status TEXT,
            balance_due REAL DEFAULT 0,
            source_pdf TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL,
            entity TEXT NOT NULL,
            entity_id INTEGER,
            details TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_bank_transactions_date ON bank_transactions(date);
        CREATE INDEX IF NOT EXISTS idx_expenses_date ON expenses(date);
        CREATE INDEX IF NOT EXISTS idx_payroll_month ON payroll_entries(month);
        CREATE INDEX IF NOT EXISTS idx_invoices_date ON invoices(date);
        CREATE INDEX IF NOT EXISTS idx_invoices_status ON invoices(status);
        """
    )
    ensure_column(conn, "invoices", "source_pdf", "TEXT")
    ensure_column(conn, "bank_transactions", "attachment_path", "TEXT")
    ensure_column(conn, "expenses", "attachment_path", "TEXT")
    ensure_column(conn, "payroll_entries", "attachment_path", "TEXT")
    ensure_primary_account(conn)
    conn.commit()


def ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def ensure_primary_account(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT id FROM bank_accounts ORDER BY id LIMIT 1").fetchone()
    if row:
        return int(row["id"])
    cur = conn.execute(
        "INSERT INTO bank_accounts (name, opening_balance) VALUES (?, ?)",
        ("Primary Company Account", 0.0),
    )
    return int(cur.lastrowid)


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def get_setting(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return None if row is None else str(row["value"])


def table_count(conn: sqlite3.Connection, table: str) -> int:
    if table not in {"bank_transactions", "expenses", "payroll_entries", "invoices"}:
        raise ValueError(f"Unsupported table: {table}")
    return int(conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()["count"])


def is_empty(conn: sqlite3.Connection) -> bool:
    return all(
        table_count(conn, name) == 0
        for name in ("bank_transactions", "expenses", "payroll_entries", "invoices")
    )


def clear_operational_data(conn: sqlite3.Connection) -> None:
    for table in ("audit_log", "invoices", "payroll_entries", "expenses", "bank_transactions"):
        conn.execute(f"DELETE FROM {table}")


def audit(
    conn: sqlite3.Connection,
    action: str,
    entity: str,
    entity_id: int | None,
    details: dict[str, Any] | str | None = None,
) -> None:
    if isinstance(details, dict):
        details_value = json.dumps(details, default=str, sort_keys=True)
    else:
        details_value = details
    conn.execute(
        "INSERT INTO audit_log (action, entity, entity_id, details) VALUES (?, ?, ?, ?)",
        (action, entity, entity_id, details_value),
    )


def add_bank_transaction(conn: sqlite3.Connection, payload: dict[str, Any]) -> int:
    account_id = int(payload.get("account_id") or ensure_primary_account(conn))
    tx_date = normalize_date(payload.get("date")) or date.today().isoformat()
    tx_month = payload.get("month") or tx_date[:7]
    cur = conn.execute(
        """
        INSERT INTO bank_transactions
            (account_id, date, month, type, category, detail, source, amount, attachment_path, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            account_id,
            tx_date,
            tx_month,
            clean_text(payload.get("type")) or "Expense",
            clean_text(payload.get("category")),
            clean_text(payload.get("detail")),
            clean_text(payload.get("source")),
            amount_value(payload.get("amount")),
            clean_text(payload.get("attachment_path")),
            clean_text(payload.get("notes")),
        ),
    )
    entity_id = int(cur.lastrowid)
    audit(conn, "create", "bank_transaction", entity_id, payload)
    return entity_id


def add_expense(conn: sqlite3.Connection, payload: dict[str, Any]) -> int:
    expense_date = normalize_date(payload.get("date")) or date.today().isoformat()
    cur = conn.execute(
        """
        INSERT INTO expenses
            (date, category, vendor, description, amount, paid_by, frequency, attachment_path, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            expense_date,
            clean_text(payload.get("category")),
            clean_text(payload.get("vendor")),
            clean_text(payload.get("description")),
            amount_value(payload.get("amount")),
            clean_text(payload.get("paid_by")),
            clean_text(payload.get("frequency")),
            clean_text(payload.get("attachment_path")),
            clean_text(payload.get("notes")),
        ),
    )
    entity_id = int(cur.lastrowid)
    audit(conn, "create", "expense", entity_id, payload)
    return entity_id


def add_payroll_entry(conn: sqlite3.Connection, payload: dict[str, Any]) -> int:
    month = normalize_month(payload.get("month")) or (normalize_date(payload.get("month")) or date.today().isoformat())[:7]
    vendor_pay, pct, hours, gross, commission, employee_pay = payroll_amounts(payload)
    cur = conn.execute(
        """
        INSERT INTO payroll_entries
            (month, first_name, last_name, vendor, client, job_start, job_end,
             vendor_pay, pct, hours, gross, commission, employee_pay, credit_date, attachment_path)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            month,
            clean_text(payload.get("first_name")),
            clean_text(payload.get("last_name")),
            clean_text(payload.get("vendor")),
            clean_text(payload.get("client")),
            normalize_date(payload.get("job_start")),
            normalize_date(payload.get("job_end")),
            vendor_pay,
            pct,
            hours,
            gross,
            commission,
            employee_pay,
            normalize_date(payload.get("credit_date")),
            clean_text(payload.get("attachment_path")),
        ),
    )
    entity_id = int(cur.lastrowid)
    audit(conn, "create", "payroll_entry", entity_id, payload)
    return entity_id


def payroll_amounts(payload: dict[str, Any]) -> tuple[float, float, float, float, float, float]:
    vendor_pay = amount_value(payload.get("vendor_pay"))
    hours = amount_value(payload.get("hours"))
    pct = amount_value(payload.get("pct")) if clean_text(payload.get("pct")) is not None else 30.0
    gross = amount_value(payload.get("gross"))
    if gross == 0 and vendor_pay and hours:
        gross = vendor_pay * hours
    commission = amount_value(payload.get("commission"))
    if commission == 0 and gross:
        commission = gross * commission_fraction(pct)
    employee_pay = amount_value(payload.get("employee_pay"))
    if employee_pay == 0 and gross:
        employee_pay = gross - commission
    return vendor_pay, pct, hours, round(gross, 2), round(commission, 2), round(employee_pay, 2)


def commission_fraction(pct: float) -> float:
    if pct <= 0:
        return 0.0
    return pct if pct <= 1 else pct / 100


def add_invoice(conn: sqlite3.Connection, payload: dict[str, Any]) -> int:
    invoice_date = normalize_date(payload.get("date")) or date.today().isoformat()
    amount = amount_value(payload.get("amount"))
    status, received, is_void = normalize_invoice_status(payload)
    balance_due = amount_value(payload.get("balance_due"))
    if status.lower() == "paid" or str(received).upper() == "Y" or is_void:
        balance_due = 0.0
    elif balance_due == 0 and amount:
        balance_due = amount
    cur = conn.execute(
        """
        INSERT INTO invoices
            (date, invoice_number, customer, is_void, received, due_date, amount, status, balance_due, source_pdf)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            invoice_date,
            clean_text(payload.get("invoice_number")) or next_invoice_number(conn),
            clean_text(payload.get("customer")),
            1 if is_void else 0,
            received,
            normalize_date(payload.get("due_date")),
            amount,
            status,
            balance_due,
            clean_text(payload.get("source_pdf")),
        ),
    )
    entity_id = int(cur.lastrowid)
    audit(conn, "create", "invoice", entity_id, payload)
    return entity_id


def update_invoice_status(conn: sqlite3.Connection, invoice_id: int, status_value: str) -> None:
    row = conn.execute("SELECT amount FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
    if row is None:
        raise ValueError("Invoice was not found")
    status, received, is_void = normalize_invoice_status({"status": status_value})
    balance_due = 0.0 if received == "Y" or is_void else amount_value(row["amount"])
    conn.execute(
        """
        UPDATE invoices
        SET status = ?, received = ?, is_void = ?, balance_due = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (status, received, 1 if is_void else 0, balance_due, invoice_id),
    )
    audit(conn, "update_status", "invoice", invoice_id, {"status": status_value})


def normalize_invoice_status(payload: dict[str, Any]) -> tuple[str, str, bool]:
    raw_status = clean_text(payload.get("status")) or ""
    received = clean_text(payload.get("received")) or ""
    is_void = bool_value(payload.get("is_void"))
    normalized = raw_status.strip().lower().replace("_", " ")

    if normalized in {"void", "voided"}:
        return "VOID", "N", True

    if normalized in {"received", "paid", "yes", "y"}:
        return "Paid", "Y", False

    if normalized in {"not received", "notreceived", "unpaid", "no", "n"}:
        return "Open", "N", False

    if raw_status.upper() == "VOID":
        return "VOID", "N", True

    if is_void:
        return "VOID", "N", True

    if received.upper() == "Y":
        return "Paid", "Y", is_void

    status = raw_status or "Open"
    if not received:
        received = "N"
    return status, received.upper(), is_void


def next_invoice_number(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT invoice_number FROM invoices WHERE invoice_number LIKE 'INV-%' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not row:
        return "INV-000001"
    try:
        number = int(str(row["invoice_number"]).split("-")[-1]) + 1
    except ValueError:
        number = table_count(conn, "invoices") + 1
    return f"INV-{number:06d}"


def normalize_date(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text or text.upper() in {"N/A", "NA", "-", "NONE"}:
        return None
    if len(text) == 7 and text[4] == "-":
        return f"{text}-01"
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text[:10], fmt).date().isoformat()
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(text).date().isoformat()
    except ValueError:
        return None


def normalize_month(value: Any) -> str | None:
    normalized = normalize_date(value)
    if normalized:
        return normalized[:7]
    if value is None:
        return None
    text = str(value).strip()
    if len(text) == 7 and text[4] == "-":
        return text
    return None


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def amount_value(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text or text.upper() in {"N/A", "NA", "-"}:
        return 0.0
    text = text.replace("$", "").replace(",", "").replace("(", "-").replace(")", "")
    try:
        return float(text)
    except ValueError:
        return 0.0


def bool_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    return str(value).strip().upper() in {"Y", "YES", "TRUE", "1", "VOID"}


def bank_signed_amount(row: sqlite3.Row | dict[str, Any]) -> float:
    amount = amount_value(row["amount"])
    tx_type = clean_text(row["type"]) or ""
    if tx_type in NEGATIVE_BANK_TYPES:
        return -abs(amount)
    return abs(amount)


def active_month(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        """
        SELECT MAX(month_value) AS month_value
        FROM (
            SELECT substr(date, 1, 7) AS month_value FROM bank_transactions
            UNION ALL SELECT substr(date, 1, 7) FROM expenses
            UNION ALL SELECT month FROM payroll_entries
            UNION ALL SELECT substr(date, 1, 7) FROM invoices
        )
        """
    ).fetchone()
    return row["month_value"] or date.today().isoformat()[:7]


def current_balance(conn: sqlite3.Connection) -> float:
    account = conn.execute("SELECT opening_balance FROM bank_accounts ORDER BY id LIMIT 1").fetchone()
    balance = amount_value(account["opening_balance"] if account else 0)
    rows = conn.execute("SELECT type, amount FROM bank_transactions ORDER BY date, id").fetchall()
    return balance + sum(bank_signed_amount(row) for row in rows)


def dashboard_metrics(conn: sqlite3.Connection) -> dict[str, Any]:
    month = active_month(conn)
    balance = current_balance(conn)
    total_expenses = amount_value(
        conn.execute("SELECT COALESCE(SUM(amount), 0) AS total FROM expenses").fetchone()["total"]
    )
    month_expenses = amount_value(
        conn.execute(
            "SELECT COALESCE(SUM(amount), 0) AS total FROM expenses WHERE substr(date, 1, 7) = ?",
            (month,),
        ).fetchone()["total"]
    )
    invoice_row = conn.execute(
        """
        SELECT
            COALESCE(SUM(CASE WHEN is_void = 0 THEN amount ELSE 0 END), 0) AS invoiced,
            COALESCE(SUM(CASE WHEN is_void = 0 THEN balance_due ELSE 0 END), 0) AS outstanding,
            COALESCE(SUM(CASE WHEN is_void = 0 AND lower(status) = 'paid' THEN amount ELSE 0 END), 0) AS paid
        FROM invoices
        """
    ).fetchone()
    payroll_row = conn.execute(
        """
        SELECT
            COALESCE(SUM(gross), 0) AS gross,
            COALESCE(SUM(commission), 0) AS commission,
            COALESCE(SUM(employee_pay), 0) AS employee_pay
        FROM payroll_entries
        """
    ).fetchone()
    return {
        "active_month": month,
        "current_balance": balance,
        "total_expenses": total_expenses,
        "month_expenses": month_expenses,
        "invoice_total": amount_value(invoice_row["invoiced"]),
        "invoice_paid": amount_value(invoice_row["paid"]),
        "invoice_outstanding": amount_value(invoice_row["outstanding"]),
        "payroll_gross": amount_value(payroll_row["gross"]),
        "payroll_commission": amount_value(payroll_row["commission"]),
        "payroll_employee_pay": amount_value(payroll_row["employee_pay"]),
    }


def monthly_bank_summary(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT date, type, amount FROM bank_transactions ORDER BY date, id"
    ).fetchall()
    groups: OrderedDict[str, dict[str, Any]] = OrderedDict()
    balance = amount_value(
        (conn.execute("SELECT opening_balance FROM bank_accounts ORDER BY id LIMIT 1").fetchone() or {"opening_balance": 0})[
            "opening_balance"
        ]
    )
    opening_rows = [row for row in rows if (clean_text(row["type"]) or "") == "Opening"]
    balance += sum(abs(amount_value(row["amount"])) for row in opening_rows)
    for row in rows:
        if (clean_text(row["type"]) or "") == "Opening":
            continue
        month = str(row["date"])[:7]
        if month not in groups:
            groups[month] = {
                "month": month,
                "opening": balance,
                "deposits": 0.0,
                "expenses": 0.0,
                "net": 0.0,
                "closing": balance,
                "transactions": 0,
            }
        signed = bank_signed_amount(row)
        if signed >= 0:
            groups[month]["deposits"] += signed
        else:
            groups[month]["expenses"] += abs(signed)
        groups[month]["net"] += signed
        groups[month]["transactions"] += 1
        balance += signed
        groups[month]["closing"] = balance
    return list(groups.values())


def recent_activity(conn: sqlite3.Connection, limit: int = 10) -> list[dict[str, Any]]:
    bank = [
        {
            "date": row["date"],
            "section": "Bank",
            "label": row["detail"] or row["category"] or row["type"],
            "amount": bank_signed_amount(row),
        }
        for row in conn.execute(
            "SELECT date, type, category, detail, amount FROM bank_transactions ORDER BY date DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    ]
    expenses = [
        {
            "date": row["date"],
            "section": "Expense",
            "label": row["description"] or row["vendor"] or row["category"],
            "amount": -abs(amount_value(row["amount"])),
        }
        for row in conn.execute(
            "SELECT date, category, vendor, description, amount FROM expenses ORDER BY date DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    ]
    invoices = [
        {
            "date": row["date"],
            "section": "Invoice",
            "label": f"{row['invoice_number']} · {row['customer'] or ''}",
            "amount": amount_value(row["amount"]),
        }
        for row in conn.execute(
            "SELECT date, invoice_number, customer, amount FROM invoices ORDER BY date DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    ]
    return sorted(bank + expenses + invoices, key=lambda item: item["date"], reverse=True)[:limit]


def rows_for_table(conn: sqlite3.Connection, table: str, limit: int = 200) -> list[sqlite3.Row]:
    allowed = {
        "bank_transactions": "SELECT * FROM bank_transactions ORDER BY date DESC, id DESC LIMIT ?",
        "expenses": "SELECT * FROM expenses ORDER BY date DESC, id DESC LIMIT ?",
        "payroll_entries": "SELECT * FROM payroll_entries ORDER BY month DESC, id DESC LIMIT ?",
        "invoices": "SELECT * FROM invoices ORDER BY date DESC, id DESC LIMIT ?",
        "audit_log": "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?",
    }
    if table not in allowed:
        raise ValueError(f"Unsupported table: {table}")
    return conn.execute(allowed[table], (limit,)).fetchall()


def filter_clause(period: str, month: str | None, day: str | None, column: str = "date") -> tuple[str, list[Any]]:
    if period == "daily" and day:
        return f" WHERE {column} = ?", [day]
    if period == "monthly" and month:
        return f" WHERE substr({column}, 1, 7) = ?", [month]
    return "", []


def fetch_report_data(
    conn: sqlite3.Connection,
    period: str = "all",
    month: str | None = None,
    day: str | None = None,
) -> dict[str, list[sqlite3.Row] | dict[str, Any]]:
    bank_where, bank_params = filter_clause(period, month, day)
    expense_where, expense_params = filter_clause(period, month, day)
    invoice_where, invoice_params = filter_clause(period, month, day)
    payroll_where = ""
    payroll_params: list[Any] = []
    if period == "monthly" and month:
        payroll_where = " WHERE month = ?"
        payroll_params = [month]
    elif period == "daily" and day:
        payroll_where = " WHERE credit_date = ?"
        payroll_params = [day]
    return {
        "metrics": dashboard_metrics(conn),
        "bank": conn.execute(
            f"SELECT * FROM bank_transactions{bank_where} ORDER BY date, id", bank_params
        ).fetchall(),
        "expenses": conn.execute(
            f"SELECT * FROM expenses{expense_where} ORDER BY date, id", expense_params
        ).fetchall(),
        "payroll": conn.execute(
            f"SELECT * FROM payroll_entries{payroll_where} ORDER BY month, id", payroll_params
        ).fetchall(),
        "invoices": conn.execute(
            f"SELECT * FROM invoices{invoice_where} ORDER BY date, id", invoice_params
        ).fetchall(),
    }


def distinct_values(conn: sqlite3.Connection, table: str, column: str) -> list[str]:
    allowed = {
        ("expenses", "category"),
        ("expenses", "paid_by"),
        ("expenses", "frequency"),
        ("bank_transactions", "type"),
        ("bank_transactions", "category"),
        ("invoices", "customer"),
        ("payroll_entries", "vendor"),
        ("payroll_entries", "client"),
    }
    if (table, column) not in allowed:
        raise ValueError("Unsupported distinct lookup")
    rows = conn.execute(
        f"SELECT DISTINCT {column} AS value FROM {table} WHERE {column} IS NOT NULL AND {column} != '' ORDER BY {column}"
    ).fetchall()
    return [str(row["value"]) for row in rows]


def bulk_insert(conn: sqlite3.Connection, table: str, rows: Iterable[dict[str, Any]]) -> int:
    count = 0
    for row in rows:
        if table == "bank_transactions":
            add_bank_transaction(conn, row)
        elif table == "expenses":
            add_expense(conn, row)
        elif table == "payroll_entries":
            add_payroll_entry(conn, row)
        elif table == "invoices":
            add_invoice(conn, row)
        else:
            raise ValueError(f"Unsupported bulk insert: {table}")
        count += 1
    return count
