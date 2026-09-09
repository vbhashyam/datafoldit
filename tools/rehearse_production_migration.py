"""Offline migration rehearsal. No network, production writes, or credential copying."""
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
snapshot = Path(sys.argv[1]).resolve() if len(sys.argv)>1 else root / 'private-backups/production-20260909T174353Z'
if snapshot.parent != root / 'private-backups':
    raise SystemExit('Snapshot must be in the private backup directory')
target = snapshot / 'migration-rehearsal.sqlite'
if target.exists():
    raise SystemExit('Rehearsal already exists; refusing to overwrite')
source = sqlite3.connect(f'file:{snapshot / "datafoldit.db"}?mode=ro', uri=True)
source.row_factory = sqlite3.Row
db = sqlite3.connect(target)
target.chmod(0o600)
db.execute('PRAGMA foreign_keys=ON')
for migration in sorted((root / 'cloudflare-poc/migrations').glob('*.sql')):
    db.executescript(migration.read_text())
db.execute('CREATE TABLE legacy_archive(entity TEXT, legacy_id INTEGER, payload TEXT NOT NULL, PRIMARY KEY(entity,legacy_id))')
mapping = {'bank_transactions': 'bank', 'expenses': 'expenses', 'payroll_entries': 'payroll', 'invoices': 'invoices'}
outgoing = {'Expense', 'Withdrawal', 'Transfer Out', 'Adjustment Out'}
counts = {}
links = 0
for table, kind in mapping.items():
    rows = source.execute('SELECT * FROM ' + table + ' ORDER BY id').fetchall()
    counts[kind] = len(rows)
    for row in rows:
        old = dict(row)
        db.execute('INSERT INTO legacy_archive VALUES(?,?,?)', (table, old['id'], json.dumps(old)))
        data = {key: value for key, value in old.items() if key not in ['id', 'created_at', 'updated_at', 'attachment_path', 'source_pdf']}
        if kind == 'bank':
            data['amount'] = abs(data['amount'])
        if kind == 'invoices':
            data['status'] = 'VOID' if data['is_void'] else 'Paid' if data['received'] == 'Y' else data['status']
            data['received_date'] = ''
        if kind == 'payroll':
            data.update(effective_start='', effective_end='')
        record_id = 'legacy-' + kind + '-' + str(old['id'])
        db.execute('INSERT INTO dashboard_records(id,kind,data,updated_by,updated_at) VALUES(?,?,?,?,?)', (record_id, kind, json.dumps(data), 'Legacy record', old['updated_at']))
        path = old.get('attachment_path') or old.get('source_pdf')
        if path:
            file = snapshot / 'data/attachments' / Path(path).name
            content = file.read_bytes()
            sha = hashlib.sha256(content).hexdigest()
            file_id = kind + '-' + sha
            if not db.execute('SELECT 1 FROM import_files WHERE id=?', (file_id,)).fetchone():
                db.execute('INSERT INTO import_files(id,kind,name,size,sha,uploaded_by) VALUES(?,?,?,?,?,?)', (file_id, kind, file.name, len(content), sha, 'Legacy record'))
                for index, offset in enumerate(range(0, len(content), 32768)):
                    db.execute('INSERT INTO import_chunks VALUES(?,?,?)', (file_id, index, content[offset:offset + 32768]))
            db.execute('INSERT INTO record_attachments VALUES(?,?)', (record_id, file_id))
            links += 1
for table in ['audit_log', 'bank_accounts']:
    for row in source.execute('SELECT * FROM ' + table):
        old = dict(row)
        db.execute('INSERT INTO legacy_archive VALUES(?,?,?)', (table, old['id'], json.dumps(old)))
assert source.execute('SELECT SUM(opening_balance) FROM bank_accounts').fetchone()[0] == 0, 'Nonzero account opening balance requires explicit mapping'
db.commit()
assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
assert not db.execute('PRAGMA foreign_key_check').fetchall()
for kind, count in counts.items():
    assert db.execute('SELECT count(*) FROM dashboard_records WHERE kind=?', (kind,)).fetchone()[0] == count
assert db.execute('SELECT count(*) FROM poc_users').fetchone()[0] == 0
assert db.execute('SELECT count(*) FROM poc_invitations').fetchone()[0] == 0
totals = {}
for table, kind in mapping.items():
    originals = [dict(r) for r in source.execute('SELECT * FROM ' + table)]
    converted = [json.loads(r[0]) for r in db.execute('SELECT data FROM dashboard_records WHERE kind=?', (kind,))]
    metrics = ['gross', 'tax', 'employee_pay'] if kind == 'payroll' else ['amount', 'balance_due', 'commission_amount'] if kind == 'invoices' else ['amount']
    for metric in metrics:
        def value(row):
            amount = row.get(metric) or 0
            return (-1 if row['type'] in outgoing else 1) * abs(amount) if kind == 'bank' else amount
        before = round(sum(map(value, originals)), 2)
        after = round(sum(map(value, converted)), 2)
        assert before == after, (kind, metric, before, after)
        totals[kind + '.' + ('signed_balance' if kind == 'bank' else metric)] = after
for file_id, expected in db.execute('SELECT id,sha FROM import_files'):
    content = b''.join(row[0] for row in db.execute('SELECT data FROM import_chunks WHERE file_id=? ORDER BY position', (file_id,)))
    assert hashlib.sha256(content).hexdigest() == expected
report = {'status': 'OFFLINE REHEARSAL ONLY - NOT DEPLOYED', 'counts': counts, 'attachment_links': links, 'reconciled_totals': totals, 'users_copied': 0, 'invitations_copied': 0, 'legacy_rows_preserved': db.execute('SELECT count(*) FROM legacy_archive').fetchone()[0]}
(snapshot / 'migration-reconciliation.json').write_text(json.dumps(report, indent=2))
(snapshot / 'migration-reconciliation.json').chmod(0o600)
db.close()
source.close()
print(json.dumps(report, indent=2))
