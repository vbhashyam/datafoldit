# Cloudflare compatibility proof of concept

Isolated prototype, not the full dashboard. No real credentials,
database records, or attachments are used. An empty isolated D1 database was
created with user approval on 2026-09-08; its identifiers are recorded in
cloud-resources.json. The schema and protected sample Worker were deployed on
2026-09-08. No users or business records have been uploaded. No paid services
were activated. Public checks passed: health 200, login page 200, anonymous
transactions 401. Hosted authenticated/CPU checks and account enrollment remain
unfinished; this is not ready for employee testing.

Run: npm install, then npm test.

## Initial results — 2026-09-08

- Existing PBKDF2-SHA256 calculation at 600,000 iterations succeeds in local workerd.
- An RFC test-vector check for the six-digit MFA code succeeds.
- A prepared D1 statement writes and reads a synthetic transaction.
- An R2 binding was checked in the original emulator probe only. R2 is NOT
  part of the deployment plan and must not be activated.
- D1 chunked attachments pass a 5 MiB round-trip, owner isolation, deletion,
  and atomic quota rejection. Limits: 5 MiB/file, 100 files, 100 MiB total.
- Authentication tests cover password/MFA, replay prevention, session expiry
  and revocation, login throttling, and read/write authorization.
- HTTP tests cover secure cookies, origin and CSRF rejection, reader write
  denial, server-side transaction attribution, private downloads, and logout.

These are local regression checks, not a complete security review, durability
test, or hosted free-tier acceptance test. The password operation
took approximately 36 ms of local wall time; this is NOT a measurement of billed
CPU time or proof that it meets Workers Free CPU limits.

## Required gates before migration

1. Verify Cloudflare account and free-plan permissions without activating billing.
2. Run hosted authentication timing tests with disposable users, CSRF protection,
   invitation expiry, MFA replay prevention, session revocation, and role checks.
3. Test browser-based PDF rendering/text extraction, scanned-page OCR, spreadsheet
   reading and exports. Do not silently drop unreadable pages or transactions.
4. Obtain Zoho OAuth authorization before connecting email through HTTPS.
5. Verify private attachment access, D1 atomic writes, quotas, and backup/restore.
6. Configure a dedicated test hostname only after acceptance passes.

Keep existing local test and production entirely unchanged. Never deploy the
inline diagnostic handler from probe.mjs: it is deliberately unauthenticated.
worker.mjs is the protected sample-only HTTP prototype. Admin invitation creation
and fragment-token acceptance with a private QR code are implemented. The link
screen explicitly reports that email was NOT sent. There is no public signup,
email delivery, recovery enrollment, PDF extraction, or full dashboard
modules yet. Existing user/password records have NOT been migrated; the prototype
hash representation is not a drop-in replacement for existing Python hashes.

## Hosting boundaries

Use a dedicated new Free-plan Worker and D1 database, with no production routes,
no R2, no paid plan, and no real records. Validate hosted CPU limits before
inviting employees. Do not weaken password hashing to fit the free allowance.
The initial database migration contains only schema, never seeded credentials.

## Hosted authentication blocker — 2026-09-08

The live synthetic-account check FAILED at password hashing (HTTP 400, fixed
diagnostic category `password_runtime`), despite the complete local test suite
passing. The temporary synthetic accounts, sessions and throttle records were
removed by the check. No real admin or employee has been enrolled.

Do not share this as employee-ready or issue real invitations before delivery
and administrator onboarding are verified.
Cloudflare documents/discusses a hosted PBKDF2 iteration ceiling in workerd issue
1346; the current error classification supports a hashing-runtime failure but
does not expose the original runtime exception or establish its precise limit.

## Password blocker resolved; email authorization pending

The Node PBKDF2 implementation also failed on the hosted Worker. Standard native
scrypt at OWASP's N=32768, r=8, p=3 configuration passed the hosted password/MFA
and role checks. New test credentials use a versioned scrypt hash. No production
credentials were changed or imported.

The approved flow is now implemented in the test Worker: approved-email setup
links delivered only to the email address; required admin MFA; optional employee
MFA; and password-confirmed later enrollment that revokes existing sessions.
Local tests use a mock mail service. Actual Zoho delivery remains unconfigured:
the tool permission reviewer rejected transfer of the local test app password
without explicit approval for Cloudflare encrypted Worker secrets. Do not rerun
configure-test-email.mjs until that approval is given. The rejected command did
not run; no admin bootstrap was performed by it.

The full business dashboard is still NOT migrated. Current signed-in workspace
contains sample transactions/uploads only. Do not represent this as full
expenses/payroll/invoice employee acceptance testing.

## Email connected after explicit approval

The user subsequently explicitly approved transferring the existing test Zoho
app password into the isolated Worker's encrypted secret. Configuration succeeded.
The admin address vamsi@datafoldit.com is approved, and its first hosted setup
email request returned HTTP 200 after SMTP acceptance. The pending invitation
and mail attempt count of one were verified without reading the token or secret.
Inbox receipt and real admin enrollment still require the user to complete setup.
No production settings were modified. Employee signup/login testing can follow
admin enrollment and approval of employee emails; business-dashboard migration
is still unfinished.
