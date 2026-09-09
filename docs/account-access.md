# Individual access — test implementation

The test dashboard uses invitation-only accounts. The designated owner is
`vamsi@datafoldit.com`. Shared-password cookies are no longer accepted.

- Admin: manage users and operational data; database backup and full workbook import.
- Read/write: operational records, uploads, reports and attachments; no user management.
- Read-only: view all operational data and download Excel reports/attachments; no writes.

Create the owner's initial single-use invitation with `tools/bootstrap_admin.py`,
passing the explicit test database path and test base URL. The tool refuses to
overwrite an existing owner. Invitation tokens expire after 24 hours and are stored
hashed. Do not share the owner's link with anyone else.

An invited user chooses a 12–128 character password and enrolls a time-based
authenticator using the displayed setup key. Eight one-time recovery codes are
shown only on activation. Login requires password plus authenticator/recovery code.
Codes cannot be replayed. Passwords use salted PBKDF2-HMAC-SHA256 with 600,000 rounds.
Sessions expire after eight hours. Access changes revoke all of that user's sessions.

The User access page creates invitation links for manual delivery. Reinviting a
non-owner account resets its password/MFA and invalidates sessions. Verify the
recipient's identity before resetting. Owner recovery uses the saved recovery codes;
there is no unauthenticated owner-reset endpoint.

## Pending external integration

Zoho sign-in and outbound invitation email are not connected. They require a
registered Zoho application, its data center, client credentials stored outside
source control, and an approved callback/base URL. The eventual OIDC integration
must validate signatures, issuer, audience, expiry, state and nonce; bind identities
to immutable provider subjects and only admit approved verified email addresses.
Zoho MFA enforcement must be configured in the organization, not assumed from SSO.

This localhost preview is not reachable by other people's computers. Before any
shared deployment, configure HTTPS, `DATAFOLDIT_COOKIE_SECURE=1`, persistent storage,
restricted database/backup access and the correct public base URL. MFA seeds are
stored in the private database; protect it and its backups as credentials. Keep
production separate until acceptance testing and deployment approval.

## Verification

`python3 -m unittest discover -s tests -v` includes account, permission, CSRF,
revocation, MFA/recovery replay and existing operational regression tests.
`python3 tools/check_uploads.py` checks representative actual uploads on a disposable
database and temporary server. Older XLS payroll extraction needs LibreOffice.
