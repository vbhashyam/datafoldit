"""Prepare new synthetic employee-test data and copy existing test identities only."""
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from datafoldit import auth, db

root = Path(__file__).resolve().parents[1]
target = root / "tmp" / "shared-test" / "datafoldit.db"
if target.exists():
    raise SystemExit("Shared test database already exists; refusing to overwrite it.")
target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
(target.parent / "attachments").mkdir(mode=0o700, exist_ok=True)
source = sqlite3.connect((root / "tmp/ui-preview/datafoldit.db").as_uri() + "?mode=ro", uri=True)
source.row_factory = sqlite3.Row
conn = db.connect(target)
db.init_db(conn)
auth.init(conn)
with conn:
    # Do not copy financial records, audit records, attachments or active sessions.
    for table in ("app_users", "app_invites", "app_recovery"):
        for row in source.execute(f"SELECT * FROM {table}"):
            columns = list(row.keys())
            conn.execute(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", tuple(row))
    for month in ("2026-06", "2026-07", "2026-08"):
        db.add_bank_transaction(conn, {"date": month + "-05", "type": "Deposit", "category": "Sample revenue", "detail": "DEMO client payment", "source": "Example Client", "amount": "12000"})
        db.add_bank_transaction(conn, {"date": month + "-15", "type": "Expense", "category": "Sample operations", "detail": "DEMO operating expenses", "source": "Demo company account", "amount": "4200"})
        db.add_expense(conn, {"date": month + "-15", "vendor": "Example Supplier", "category": "Software", "description": "DEMO subscription", "amount": "125", "paid_by": "Demo company account"})
        db.add_payroll_entry(conn, {"month": month, "first_name": "Sample", "last_name": "Employee", "vendor": "Example Staffing", "client": "Example Client", "vendor_pay": "30", "hours": "160", "gross": "4800", "tax": "800", "employee_pay": "4000", "credit_date": month + "-28"})
    for index, status in enumerate(("Received", "Not Received", "Not Received", "Void"), 1):
        db.add_invoice(conn, {"date": "2026-08-01", "invoice_number": f"DEMO-{index:03}", "customer": f"Example Client {index}", "amount": str(1000 * index), "due_date": "2026-08-31", "status": status})
source.close()
conn.close()
target.chmod(0o600)
print("Shared test created: synthetic financial records, no copied attachments, existing test identities preserved.")
