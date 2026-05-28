# DataFold IT Operations Dashboard

Local private dashboard for DataFold IT company operations.

The dashboard uses SQLite as the source of truth and exports Excel workbooks for
weekly/monthly/archive backups.

## Run Locally

```bash
python3 app.py
```

Open:

```text
http://127.0.0.1:8765
```

Default local password:

```text
datafoldit-local
```

To set your own password:

```bash
DATAFOLDIT_PASSWORD='your-password' python3 app.py
```

## Current MVP

- Imports the current `company_expenses.xlsx` workbook when the database is empty.
- Tracks one bank account, with room for more accounts later.
- Tracks bank transactions, business expenses, payroll, and invoices.
- Provides manual entry forms.
- Reads invoice PDFs with local OCR and opens a review form before saving.
- Exports daily, monthly, and all-time Excel reports.
- Keeps the database as the main system and Excel as backup/report output.

## Invoice PDF Reading

The local app can read a PDF invoice from a file path, extract invoice fields,
and prefill a review form. It does not save automatically.

Current local OCR path:

- macOS Quick Look renders the first PDF page.
- `tesseract` reads the rendered image.
- The app guesses invoice number, invoice date, due date, customer, amount, and status.

Use the `Invoices` page, then upload an invoice file or paste a PDF path such as:

```text
/Users/vamsikrishnabhashyam/Downloads/INV288750792.pdf
```

## Useful Commands

Initialize/import only:

```bash
python3 app.py --init-only --import-xlsx /Users/vamsikrishnabhashyam/Downloads/company_expenses.xlsx --replace
```

Run tests:

```bash
python3 -m unittest discover -s tests
```

## Private Website Deployment

The app includes Docker deployment files for a private organization website:

- `Dockerfile`
- `docker-compose.yml`
- `.env.example`
- `DEPLOYMENT.md`

Use `DEPLOYMENT.md` when moving from local testing to a private hosted server.
