"""Fail closed if frozen legacy data differs from the reviewed cloud copy."""
import json
import sqlite3
import sys
from pathlib import Path

folder=Path(sys.argv[1]).resolve()
expected=sqlite3.connect(f'file:{folder / "migration-rehearsal.sqlite"}?mode=ro',uri=True)
cloud=sqlite3.connect(':memory:')
cloud.executescript((folder/'cloud-before-cutover.sql').read_text())
assert cloud.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
assert not cloud.execute('PRAGMA foreign_key_check').fetchall()
maps={r[0]:r[1:] for r in cloud.execute('SELECT record_id,old_tracking_id,new_tracking_id,old_version FROM transaction_id_reassignments')}
columns=[r[1] for r in expected.execute('PRAGMA table_info(dashboard_records)')]
rows=expected.execute('SELECT * FROM dashboard_records ORDER BY id').fetchall()
actual=cloud.execute('SELECT * FROM dashboard_records ORDER BY id').fetchall()
assert len(rows)==len(actual),'Record count changed; final delta migration required'
for source,target in zip(rows,actual):
    data=dict(zip(columns,source));found=dict(zip(columns,target))
    if data['id'] in maps:
        old,new,version=maps[data['id']]
        data['tracking_id']=new
        data['version']=version+(old!=new)
    assert data==found,'Record changed; final delta migration required: '+data['id']
for table in ['import_files','import_chunks','record_attachments','legacy_archive','transaction_sequences']:
    cols=[r[1] for r in expected.execute('PRAGMA table_info('+table+')')]
    if table=='import_files':cols.remove('created_at')
    query='SELECT '+','.join(cols)+' FROM '+table+' ORDER BY '+','.join(cols[:2])
    assert expected.execute(query).fetchall()==cloud.execute(query).fetchall(),table+' differs; final delta required'
assert cloud.execute("SELECT active,length(mfa_hex)>0 FROM poc_users WHERE email='vamsi@datafoldit.com' AND role='admin'").fetchone()==(1,1)
report={'verified':True,'records':len(rows),'attachment_links':cloud.execute('SELECT count(*) FROM record_attachments').fetchone()[0],'assigned_id_mappings_preserved':len(maps),'user_accounts_preserved':cloud.execute('SELECT count(*) FROM poc_users').fetchone()[0],'legacy_deltas_required':0,'financial_payloads_and_attachment_bytes_match':True}
(folder/'final-reconciliation.json').write_text(json.dumps(report,indent=2));(folder/'final-reconciliation.json').chmod(0o600)
print(json.dumps(report))
