import tempfile
import unittest
from pathlib import Path

from openpyxl import load_workbook

from datafoldit import db
from datafoldit.excel_io import DEFAULT_SOURCE_XLSX, export_report_workbook, import_company_workbook
from datafoldit.invoice_pdf import parse_invoice_text
from datafoldit.transaction_import import parse_transaction_text
from datafoldit.web import filter_rows_by_period


class DataFoldCoreTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test.sqlite"
        self.conn = db.connect(self.db_path)
        db.init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmpdir.cleanup()

    def test_import_current_workbook(self):
        if not DEFAULT_SOURCE_XLSX.exists():
            self.skipTest(f"Missing source workbook: {DEFAULT_SOURCE_XLSX}")
        counts = import_company_workbook(self.conn, DEFAULT_SOURCE_XLSX, replace=True)
        self.assertEqual(counts["expenses"], 21)
        self.assertEqual(counts["bank_transactions"], 19)
        self.assertEqual(counts["payroll_entries"], 4)
        self.assertEqual(counts["invoices"], 11)
        self.assertAlmostEqual(db.current_balance(self.conn), 19254.58, places=2)
        monthly = db.monthly_bank_summary(self.conn)
        self.assertEqual(monthly[0]["month"], "2025-12")
        self.assertAlmostEqual(monthly[0]["opening"], 6400.00, places=2)
        self.assertAlmostEqual(monthly[0]["closing"], 6200.00, places=2)

    def test_report_export_is_valid_xlsx(self):
        if not DEFAULT_SOURCE_XLSX.exists():
            self.skipTest(f"Missing source workbook: {DEFAULT_SOURCE_XLSX}")
        import_company_workbook(self.conn, DEFAULT_SOURCE_XLSX, replace=True)
        output = Path(self.tmpdir.name) / "report.xlsx"
        export_report_workbook(self.conn, output, period="all")
        workbook = load_workbook(output, data_only=True)
        self.assertEqual(
            workbook.sheetnames,
            ["Summary", "Bank Transactions", "Expenses", "Payroll", "Invoices"],
        )
        self.assertEqual(workbook["Summary"]["A1"].value, "DataFold IT Operations Report")
        self.assertAlmostEqual(float(workbook["Summary"]["B5"].value), 19254.58, places=2)

    def test_period_filter_supports_year_and_blank_month(self):
        if not DEFAULT_SOURCE_XLSX.exists():
            self.skipTest(f"Missing source workbook: {DEFAULT_SOURCE_XLSX}")
        import_company_workbook(self.conn, DEFAULT_SOURCE_XLSX, replace=True)
        rows = db.rows_for_table(self.conn, "bank_transactions")
        may_rows = filter_rows_by_period(rows, "date", {"year": "2026", "month": "05"})
        year_rows = filter_rows_by_period(rows, "date", {"year": "2026", "month": ""})
        all_rows = filter_rows_by_period(rows, "date", {"year": "", "month": ""})
        self.assertEqual(len(may_rows), 6)
        self.assertEqual(len(year_rows), 18)
        self.assertEqual(len(all_rows), 19)

    def test_smart_transaction_text_parser(self):
        text = """
        ACH CREDIT
        Payment received
        From: Acme Client LLC
        Transaction Date: May 28, 2026
        Amount: $2,500.00
        """
        parsed = parse_transaction_text(text, "payment.pdf")
        self.assertEqual(parsed["date"], "2026-05-28")
        self.assertEqual(parsed["type"], "Deposit")
        self.assertEqual(parsed["detail"], "Acme Client LLC")
        self.assertEqual(parsed["category"], "Client Payment")
        self.assertEqual(parsed["source"], "ACH")
        self.assertAlmostEqual(parsed["amount"], 2500.00, places=2)

    def test_expense_and_payroll_attachment_paths_are_saved(self):
        db.add_expense(
            self.conn,
            {
                "date": "2026-05-28",
                "amount": "42.50",
                "vendor": "Zoom",
                "attachment_path": "/tmp/zoom-receipt.pdf",
            },
        )
        db.add_payroll_entry(
            self.conn,
            {
                "month": "2026-05",
                "first_name": "Vamsi",
                "gross": "1000",
                "attachment_path": "/tmp/payroll.pdf",
            },
        )
        expense = db.rows_for_table(self.conn, "expenses")[0]
        payroll = db.rows_for_table(self.conn, "payroll_entries")[0]
        self.assertEqual(expense["attachment_path"], "/tmp/zoom-receipt.pdf")
        self.assertEqual(payroll["attachment_path"], "/tmp/payroll.pdf")

    def test_payroll_calculates_from_rate_hours_and_commission_percent(self):
        db.add_payroll_entry(
            self.conn,
            {
                "month": "2026-05",
                "first_name": "Alex",
                "last_name": "Rao",
                "vendor_pay": "60",
                "pct": "30",
                "hours": "10",
            },
        )
        row = db.rows_for_table(self.conn, "payroll_entries")[0]
        self.assertAlmostEqual(row["gross"], 600.00, places=2)
        self.assertAlmostEqual(row["commission"], 180.00, places=2)
        self.assertAlmostEqual(row["employee_pay"], 420.00, places=2)

    def test_invoice_ocr_text_parser(self):
        text = """
        Invoice
        Zoom Communications, Inc.
        Invoice Date:
        Invoice #:
        Due Date:
        Jan 13, 2025
        INV288750792
        Jan 13, 2025
        Bill To Address:
        A2systemsLLC
        Subtotal $15.00
        Total (Including Taxes, Fees & Surcharges) $17.21
        """
        parsed = parse_invoice_text(text)
        self.assertEqual(parsed["invoice_number"], "INV288750792")
        self.assertEqual(parsed["date"], "2025-01-13")
        self.assertEqual(parsed["due_date"], "2025-01-13")
        self.assertEqual(parsed["customer"], "A2systemsLLC")
        self.assertAlmostEqual(parsed["amount"], 17.21, places=2)

    def test_invoice_parser_handles_day_month_dates_and_paid_balance(self):
        text = """
        Invoice
        # INV-000001
        Bill To
        The Quantum Core Technologies LLC
        Invoice Date: 12 Feb 2026
        Terms : Net 60
        Due Date : 13 Apr 2026
        Total $6,160.00
        Payment Made (-) 6,160.00
        Balance Due $0.00
        """
        parsed = parse_invoice_text(text)
        self.assertEqual(parsed["invoice_number"], "INV-000001")
        self.assertEqual(parsed["date"], "2026-02-12")
        self.assertEqual(parsed["due_date"], "2026-04-13")
        self.assertEqual(parsed["customer"], "The Quantum Core Technologies LLC")
        self.assertEqual(parsed["status"], "Paid")
        self.assertEqual(parsed["received"], "Y")
        self.assertAlmostEqual(parsed["amount"], 6160.00, places=2)
        self.assertAlmostEqual(parsed["balance_due"], 0.00, places=2)


if __name__ == "__main__":
    unittest.main()
