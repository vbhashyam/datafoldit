"""Create a single-use owner setup link in an explicitly selected database."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from datafoldit import auth, db

parser = argparse.ArgumentParser()
parser.add_argument("--db", required=True)
parser.add_argument("--base-url", required=True)
args = parser.parse_args()
path = Path(args.db).resolve(strict=True)
with db.connect(path) as conn:
    auth.init(conn)
    token = auth.invite(conn, auth.ADMIN_EMAIL, "admin", bootstrap=True)
print(f"Admin: {auth.ADMIN_EMAIL}")
print(f"Private setup link (expires in 24 hours): {args.base_url.rstrip('/')}/accept?token={token}")
