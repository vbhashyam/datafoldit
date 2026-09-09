"""Compare private remote export with the rehearsed migration, without exposing data."""
import hashlib
import sqlite3
import sys
from pathlib import Path

snapshot = Path(sys.argv[1]).resolve()
local = sqlite3.connect(f'file:{snapshot / "migration-rehearsal.sqlite"}?mode=ro', uri=True)
remote = sqlite3.connect(':memory:')
remote.executescript((snapshot / 'cloud-production-after-stage.sql').read_text())
assert remote.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
assert not remote.execute('PRAGMA foreign_key_check').fetchall()
for table in ['dashboard_records','import_files','import_chunks','record_attachments','legacy_archive','transaction_sequences']:
    columns=[row[1] for row in local.execute('PRAGMA table_info('+table+')')]
    # File creation timestamps are generated during import; all content fields must match.
    if table=='import_files': columns.remove('created_at')
    query='SELECT '+','.join(columns)+' FROM '+table+' ORDER BY '+','.join(columns[:2])
    assert local.execute(query).fetchall()==remote.execute(query).fetchall(),table+' mismatch'
assert remote.execute('SELECT count(*) FROM poc_users').fetchone()[0] == 1
assert remote.execute("SELECT role,active,length(mfa_hex)>0 FROM poc_users WHERE email='vamsi@datafoldit.com'").fetchone()==('admin',1,1)
for file_id,sha in remote.execute('SELECT id,sha FROM import_files'):
    content=b''.join(row[0] for row in remote.execute('SELECT data FROM import_chunks WHERE file_id=? ORDER BY position',(file_id,)))
    assert hashlib.sha256(content).hexdigest()==sha
print('PASS: remote staged transactions, IDs, sequences, file bytes, links and legacy archive match rehearsal; admin and MFA preserved')
local.close()
remote.close()
