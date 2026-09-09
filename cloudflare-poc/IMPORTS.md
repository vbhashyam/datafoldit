# Test document imports

The hosted test dashboard supports PDF, DOCX, PNG/JPG and XLSX uploads in Bank, Expenses, Payroll and Invoices. Old DOC/XLS files must be converted first. The new import route allows 10 MiB files, 100 pages/candidate rows, and 100 MiB total attachment storage. No R2 or paid extraction service is used.

Document extraction runs in the employee browser with self-hosted PDF.js, Mammoth, ExcelJS and English Tesseract OCR assets. It is heuristic, not guaranteed for every layout. Users review and correct all candidate rows before saving. Unreadable pages and unrecognized worksheets remain visible as incomplete candidates, rather than being silently discarded. Continuation pages can require manual deselection. Source documents remain linked to each saved record.

The server enforces authenticated write access, CSRF/origin checks, bounded uploads, filename/magic checks, row validation, file-hash deduplication, invoice duplicate checks and atomic record/file insertion. Downloads require an approved session and are served as sandboxed attachments. Test storage is not a substitute for a production document-security review.

Run `npm run build:imports` before deployment when importer sources change. The checked-in English OCR model is required under `public/static/import/lang`. Run `npm run test:imports` for synthetic file and storage tests. `check-source-pdf.mjs` is a local-only read test of the user's earlier six-invoice PDF; it never uploads that document. `hosted-import-check.mjs` verifies the exact test deployment using disposable synthetic data and removes its fixtures afterward.

Deployment verified September 8, 2026: 10 separate hosted records, 10 attachment links, exact file download, duplicate rejection and permission checks. Production was not modified.
