"""Prepare and rehearse the approved one-time Bank/Expenses ID reassignment."""
import csv
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

folder=Path(sys.argv[1]).resolve()
db=sqlite3.connect(':memory:')
db.executescript((folder/'before.sql').read_text())
assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='transaction_id_reassignments'").fetchone(), 'Already applied'
before=db.execute('SELECT * FROM dashboard_records ORDER BY id').fetchall()
sequences=dict(db.execute('SELECT kind,last_value FROM transaction_sequences'))
protect=db.execute("SELECT sql FROM sqlite_master WHERE name='protect_transaction_tracking'").fetchone()[0]
mapping=[]
for kind,prefix in [('bank','BNK'),('expenses','EXP')]:
    rows=db.execute("SELECT id,tracking_id,json_extract(data,'$.date'),version FROM dashboard_records WHERE kind=? ORDER BY json_extract(data,'$.date'),tracking_id,id",(kind,)).fetchall()
    for n,(identity,old,day,version) in enumerate(rows,1):
        assert date.fromisoformat(day).isoformat()==day
        mapping.append((identity,kind,day,old,f'{prefix}-{n:06d}',version))
def q(value): return "'"+str(value).replace("'","''")+"'"
sql=["CREATE TABLE transaction_id_reassignments(record_id TEXT PRIMARY KEY,kind TEXT NOT NULL,date_at_reassignment TEXT NOT NULL,old_tracking_id TEXT NOT NULL,new_tracking_id TEXT NOT NULL,old_version INTEGER NOT NULL,applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);",
     'CREATE TABLE chronological_id_guard(n INTEGER CHECK(n=0));',
     f"INSERT INTO chronological_id_guard SELECT count(*)-{len(mapping)} FROM dashboard_records WHERE kind IN ('bank','expenses');"]
for identity,kind,day,old,new,version in mapping:
    sql.append(f"INSERT INTO chronological_id_guard SELECT 1-count(*) FROM dashboard_records WHERE id={q(identity)} AND kind={q(kind)} AND tracking_id={q(old)} AND version={version} AND json_extract(data,'$.date')={q(day)};")
    sql.append('INSERT INTO transaction_id_reassignments(record_id,kind,date_at_reassignment,old_tracking_id,new_tracking_id,old_version) VALUES('+','.join([q(identity),q(kind),q(day),q(old),q(new),str(version)])+');')
sql+=['DROP TRIGGER protect_transaction_tracking;',
      "UPDATE dashboard_records SET tracking_id=NULL WHERE id IN (SELECT record_id FROM transaction_id_reassignments WHERE old_tracking_id!=new_tracking_id);",
      "UPDATE dashboard_records SET tracking_id=(SELECT new_tracking_id FROM transaction_id_reassignments WHERE record_id=dashboard_records.id),version=version+1 WHERE id IN (SELECT record_id FROM transaction_id_reassignments WHERE old_tracking_id!=new_tracking_id);",
      "UPDATE transaction_sequences SET last_value=MAX(last_value,(SELECT count(*) FROM dashboard_records WHERE kind=transaction_sequences.kind)) WHERE kind IN ('bank','expenses');",
      protect+';', 'DROP TABLE chronological_id_guard;']
script='\n'.join(sql)+'\n'
db.executescript(script)
assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
columns=[r[1] for r in db.execute('PRAGMA table_info(dashboard_records)')]
after=db.execute('SELECT * FROM dashboard_records ORDER BY id').fetchall()
for old,row in zip(before,after):
    for index,col in enumerate(columns):
        if col not in ['tracking_id','version']: assert old[index]==row[index],col
assert dict(db.execute('SELECT kind,last_value FROM transaction_sequences'))==sequences
for identity,kind,day,old,new,version in mapping:
    assert db.execute('SELECT tracking_id,version FROM dashboard_records WHERE id=?',(identity,)).fetchone()==(new,version+(old!=new))
try: db.execute("UPDATE dashboard_records SET tracking_id='ILLEGAL' WHERE kind='bank'")
except sqlite3.IntegrityError: pass
else: raise AssertionError('Immutable ID guard missing')
for kind,prefix in [('bank','BNK'),('expenses','EXP')]:
    db.execute('INSERT INTO dashboard_records(id,kind,data,updated_by) VALUES(?,?,?,?)',('probe-'+kind,kind,'{"date":"2000-01-01"}','Synthetic'))
    assert db.execute('SELECT tracking_id FROM dashboard_records WHERE id=?',('probe-'+kind,)).fetchone()[0]==f'{prefix}-{sequences[kind]+1:06d}'
    db.execute("UPDATE dashboard_records SET data='{}' WHERE id=?",('probe-'+kind,))
    assert db.execute('SELECT tracking_id FROM dashboard_records WHERE id=?',('probe-'+kind,)).fetchone()[0]==f'{prefix}-{sequences[kind]+1:06d}'
for name,content in [('apply.sql',script),('mapping.json',json.dumps(mapping,indent=2))]:
    path=folder/name
    with path.open('x') as output: output.write(content)
    path.chmod(0o600)
with (folder/'mapping.csv').open('x',newline='') as output:
    writer=csv.writer(output);writer.writerow(['record_id','kind','date','old_trnsc_id','new_trnsc_id','old_version']);writer.writerows(mapping)
(folder/'mapping.csv').chmod(0o600)
rollback=['DROP TRIGGER protect_transaction_tracking;',
 "UPDATE dashboard_records SET tracking_id=NULL WHERE id IN (SELECT record_id FROM transaction_id_reassignments WHERE old_tracking_id!=new_tracking_id);",
 "UPDATE dashboard_records SET tracking_id=(SELECT old_tracking_id FROM transaction_id_reassignments WHERE record_id=dashboard_records.id),version=version+1 WHERE id IN (SELECT record_id FROM transaction_id_reassignments WHERE old_tracking_id!=new_tracking_id);",protect+';']
with (folder/'rollback-review-only.sql').open('x') as output: output.write('\n'.join(rollback)+'\n')
(folder/'rollback-review-only.sql').chmod(0o600)
print('PASS: chronological IDs, stable same-day ordering, intact records, preserved counters and restored immutability; future backdated entries get next ID')
print('Mapped:',len(mapping),'Changed:',sum(old!=new for _,_,_,old,new,_ in mapping))
