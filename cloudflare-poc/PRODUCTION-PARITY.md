# Production parity and requested enhancements

Reference inspected read-only: `../datafoldit-prod/datafoldit/web.py` and `excel_io.py`, with the previously approved local test enhancements in `../datafoldit/`. No production files or database have been modified.

## Restored and tested in the hosted-test code

- Bank, Expenses, Payroll and Invoices: period filters, source/paid-by/employee/customer filters, numeric/text/date sorting, attachment sorting, column filters, matching-row totals and filtered exports. Filter/sort state remains in memory when switching tabs and saving.
- Bank signed amounts and invoice due status.
- Payroll job dates, commission percentage, tax breakdown and calculation defaults; invoice commission percentage/defaults and suggested invoice numbers.
- Inline add/edit/delete, inline file selection, add/cancel toggle, attachment download and the new review-first bulk import.
- Separate Dashboard and Reports. Excel reports have Summary, Bank Transactions, Expenses, Payroll and Invoices sheets, with all-time/monthly/daily selection. Filtered ledger Excel/CSV exports are available.
- Admin read/write versus read-only, enable/disable, employee deletion, session revocation and protection of the administrator account. Transaction history is retained when user access is deleted.
- Admin-only operational SQL backup with transactions and linked file bytes. SQLite restore has been tested; accounts, MFA and email secrets are intentionally excluded. Large backups exceeding free-test query limits are rejected with an explicit message.
- Existing employee invitations, required admin MFA, optional employee MFA, attribution and muted amount colors remain.

## Intentional differences / remaining verification

- Database backup is a SQLite-compatible SQL export, not the production `.sqlite` binary. It is not a Cloudflare-account disaster recovery backup.
- Audit Log and Import Workbook sections remain absent from Reports, as explicitly requested. Document imports are performed from each ledger.
- Vendor reminder email/mapping workflow is not yet implemented in hosted test; no vendor messages are being sent automatically.
- Full visual parity, including the optional theme switch and requested background watermark, is not complete. Do not call this a fully equivalent production migration yet.
- Extraction is heuristic and requires review. Legacy DOC/XLS formats require conversion to DOCX/XLSX. User's real PDF was checked locally only.
- Real production records and account balances have not been copied. All hosted figures are test records.

## Regression checks

`ledger-controls.test.mjs`, `ledger-dom.test.mjs`, `export-ui.test.mjs`, `user-controls.test.mjs`, `backup.test.mjs`, existing authentication/HTTP tests, and import/inline-row tests cover the restored functionality. `HOSTED_CHECK=1 node ledger-dom.test.mjs` executes the published script against synthetic DOM fixtures without altering shared data.
