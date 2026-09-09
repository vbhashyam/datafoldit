"""Explicitly configured, test-only invitation delivery. Never log credentials."""
import json
import certifi
import os
from pathlib import Path
import smtplib
import ssl
from email.message import EmailMessage
from urllib.parse import urlsplit, quote


def send_invitation(email, token, role):
    if os.environ.get("DATAFOLDIT_SHARED_TEST") != "1" and os.environ.get("DATAFOLDIT_INVITATION_EMAIL") != "1":
        return "Email delivery is not configured for this instance."
    path = Path(os.environ["DATAFOLDIT_DATA_DIR"]) / "smtp-invitations.json"
    if not path.exists():
        return "Email delivery is not configured yet."
    try:
        if path.stat().st_mode & 0o077:
            return "Email configuration permissions must be restricted to the owner."
        config = json.loads(path.read_text())
        base = config["public_url"].rstrip("/")
        parsed = urlsplit(base)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path:
            return "A valid HTTPS test address is required for invitations."
        message = EmailMessage()
        message["From"] = config["sender"]
        message["To"] = email
        message["Subject"] = "Your DataFoldIT test workspace invitation"
        permission = "read/write" if role == "write" else "read-only"
        message.set_content(
            "You have been invited to the DataFoldIT TEST workspace with "
            + permission + " access.\n\nSet your password and enroll your authenticator here:\n"
            + base + "/accept?token=" + quote(token, safe="")
            + "\n\nThis private, single-use link expires in 24 hours. Do not forward it."
            + "\nIf you did not expect this invitation, contact vamsi@datafoldit.com."
        )
        with smtplib.SMTP_SSL(config["host"], 465, context=ssl.create_default_context(cafile=certifi.where()), timeout=20) as client:
            client.login(config["sender"], config["password"])
            client.send_message(message)
        return None
    except (OSError, ValueError, KeyError, smtplib.SMTPException):
        return "Email delivery could not be confirmed. Check Zoho settings before retrying."
