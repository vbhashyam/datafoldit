import json
import sqlite3
import sys
from pathlib import Path

folder=Path(sys.argv[1]).resolve()
before=sqlite3.connect(':memory:');after=sqlite3.connect(':memory:')
before.executescript((folder/'before.sql').read_text());after.executescript((folder/'after.sql').read_text())
mapping=json.loads((folder/'mapping.json').read_text())
before.executescript((folder/'apply.sql').read_text())
for table in ['dashboard_records','transaction_sequences','transaction_id_reassignments','import_files','import_chunks','record_attachments','legacy_archive']:
    columns=[r[1] for r in before.execute('PRAGMA table_info('+table+')')]
    if table=='transaction_id_reassignments':columns.remove('applied_at')
    query='SELECT '+','.join(columns)+' FROM '+table+' ORDER BY '+','.join(columns[:2])
    assert before.execute(query).fetchall()==after.execute(query).fetchall(),table
assert after.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
assert not after.execute('PRAGMA foreign_key_check').fetchall()
for kind in ['bank','expenses']:
    dates=[r[0] for r in after.execute("SELECT json_extract(data,'$.date') FROM dashboard_records WHERE kind=? ORDER BY tracking_id",(kind,))]
    assert dates==sorted(dates)
try: after.execute("UPDATE dashboard_records SET tracking_id='ILLEGAL' WHERE kind='bank'")
except sqlite3.IntegrityError: pass
else: raise AssertionError('ID protection missing')
report={'verified':True,'records_mapped':len(mapping),'ids_changed':sum(old!=new for _,_,_,old,new,_ in mapping),'other_business_data_unchanged':True,'immutable_id_protection_restored':True}
(folder/'verification.json').write_text(json.dumps(report,indent=2));(folder/'verification.json').chmod(0o600)
print(json.dumps(report))
