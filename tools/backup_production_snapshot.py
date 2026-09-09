"""Read-only production snapshot; never restores or changes the live application."""
import hashlib
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

base = Path('/Users/vamsikrishnabhashyam/Documents')
source = base / 'datafoldit-prod-data'
code = base / 'datafoldit-prod'
destination = base / 'datafoldit' / 'private-backups' / datetime.now(timezone.utc).strftime('production-%Y%m%dT%H%M%SZ')
destination.mkdir(parents=True, mode=0o700, exist_ok=False)
db = sqlite3.connect(f'file:{source / "datafoldit.db"}?mode=ro', uri=True)
snapshot = sqlite3.connect(destination / 'datafoldit.db')
db.backup(snapshot)
assert snapshot.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
tables = [r[0] for r in snapshot.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
manifest = {'created_utc': datetime.now(timezone.utc).isoformat(), 'source': str(source), 'integrity_check': 'ok', 'tables': {}, 'files': {}}
for name in tables:
    quoted = '"' + name.replace('"', '""') + '"'
    manifest['tables'][name] = {'count': snapshot.execute('SELECT count(*) FROM ' + quoted).fetchone()[0], 'columns': [r[1] for r in snapshot.execute('PRAGMA table_info(' + quoted + ')')]}
snapshot.close()
db.close()
shutil.copytree(code, destination / 'application', ignore=shutil.ignore_patterns('.git', '__pycache__'))
for item in source.iterdir():
    if item.name.startswith('datafoldit.db'):
        continue
    if item.is_dir():
        shutil.copytree(item, destination / 'data' / item.name)
    else:
        (destination / 'data').mkdir(exist_ok=True)
        shutil.copy2(item, destination / 'data' / item.name)
config = destination / 'configuration'
config.mkdir(mode=0o700)
for path in [Path.home() / '.cloudflared/config.yml', Path.home() / 'Library/LaunchAgents/com.datafoldit.dashboard.plist', Path.home() / 'Library/LaunchAgents/com.cloudflare.cloudflared.plist']:
    shutil.copy2(path, config / path.name)
for path in destination.rglob('*'):
    path.chmod(0o700 if path.is_dir() else 0o600)
    if path.is_file():
        manifest['files'][str(path.relative_to(destination))] = hashlib.file_digest(path.open('rb'), 'sha256').hexdigest()
(destination / 'manifest.json').write_text(json.dumps(manifest, indent=2))
(destination / 'manifest.json').chmod(0o600)
print('Backup:', destination)
print('Integrity: ok; file hashes:', len(manifest['files']))
print('Table counts:', {name: value['count'] for name, value in manifest['tables'].items()})
