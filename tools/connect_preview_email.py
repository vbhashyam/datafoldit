"""Copy verified mail settings to the explicitly authorized test preview only."""
import json
import os
from pathlib import Path
import sys
import tempfile
from urllib.parse import urlsplit

root = Path(__file__).resolve().parents[1]
source = root / "tmp/shared-test/smtp-invitations.json"
target = root / "tmp/ui-preview/smtp-invitations.json"
base = sys.argv[1].rstrip("/")
parsed = urlsplit(base)
if parsed.scheme != "https" or not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password:
    raise SystemExit("An HTTPS origin is required.")
if target.exists():
    source = target
if source.stat().st_mode & 0o077:
    raise SystemExit("Source credentials must be owner-only.")
config = json.loads(source.read_text())
config["public_url"] = base
descriptor, temporary = tempfile.mkstemp(prefix=".smtp-", dir=target.parent)
try:
    with os.fdopen(descriptor, "w") as stream:
        json.dump(config, stream)
    os.replace(temporary, target)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
print("Test preview email configuration saved with owner-only permissions.")
