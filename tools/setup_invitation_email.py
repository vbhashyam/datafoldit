"""Run interactively on the owner's Mac; no passwords in chat or shell history."""
import getpass
import certifi
import json
import os
from pathlib import Path
import smtplib
import ssl
import sys
from urllib.parse import urlsplit


def main():
    if not sys.stdin.isatty():
        raise SystemExit("Run this directly in your Mac Terminal, not through chat.")
    target = Path(__file__).resolve().parents[1] / "tmp/shared-test/smtp-invitations.json"
    if not target.parent.is_dir():
        raise SystemExit("The shared test directory is missing.")
    print("DataFoldIT TEST ONLY — verifies Zoho login; sends no email.")
    host = input("Zoho SMTP host from your Mail settings [smtppro.zoho.com]: ").strip() or "smtppro.zoho.com"
    allowed = {prefix + suffix for prefix in ("smtp.zoho.", "smtppro.zoho.") for suffix in ("com", "eu", "in", "com.au", "jp", "ca", "com.cn", "sa")}
    if host not in allowed:
        raise SystemExit("Unrecognized Zoho host. Check with your administrator.")
    base = input("Current HTTPS test URL: ").strip().rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path:
        raise SystemExit("Enter only the HTTPS test origin, without /login or other paths.")
    password = getpass.getpass("Zoho app password (hidden): ").strip()
    if not password:
        raise SystemExit("No password entered; nothing saved.")
    sender = "vamsi@datafoldit.com"
    try:
        with smtplib.SMTP_SSL(host, 465, context=ssl.create_default_context(cafile=certifi.where()), timeout=20) as client:
            client.login(sender, password)
    except ssl.SSLCertVerificationError:
        raise SystemExit("Python could not verify Zoho's TLS certificate. Run the Install Certificates.command supplied with your Python installation, then retry. Nothing saved; password authentication was not reached.")
    except smtplib.SMTPAuthenticationError as error:
        raise SystemExit(f"Zoho rejected authentication (SMTP {error.smtp_code}). Check the mailbox, app password, and SMTP access policy. Nothing saved.")
    except (OSError, smtplib.SMTPException):
        raise SystemExit("Zoho login failed. Nothing saved. Check host and app password.")
    config = dict(sender=sender, host=host, password=password, public_url=base)
    temporary = target.with_suffix(".pending")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(config, stream)
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
    print("Zoho login verified. Settings saved privately for shared test only. No email sent.")


if __name__ == "__main__":
    main()
