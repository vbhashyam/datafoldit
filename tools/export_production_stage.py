"""Prepare a guarded, data-only D1 staging import. Does not access the network."""
import hashlib
import sqlite3
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
snapshot = Path(sys.argv[1]).resolve()
assert snapshot.parent == root / 'private-backups'
db = sqlite3.connect(f'file:{snapshot / "migration-rehearsal.sqlite"}?mode=ro', uri=True)
db.row_factory = sqlite3.Row
target = snapshot / 'production-stage.sql'
def literal(value):
    if value is None: return 'NULL'
    if isinstance(value, bytes): return "X'" + value.hex() + "'"
    if isinstance(value, (float,int)): return str(value)
    return "'" + str(value).replace("'", "''") + "'"
with target.open('x') as output:
    target.chmod(0o600)
    output.write('CREATE TABLE production_migration_guard (n INTEGER CHECK(n=0));\n')
    output.write('INSERT INTO production_migration_guard SELECT count(*) FROM dashboard_records;\n')
    output.write("INSERT INTO production_migration_guard SELECT count(*) FROM poc_users WHERE email!='vamsi@datafoldit.com' OR role!='admin';\n")
    output.write('CREATE TABLE legacy_archive(entity TEXT,legacy_id INTEGER,payload TEXT NOT NULL,PRIMARY KEY(entity,legacy_id));\n')
    for table in ['dashboard_records','import_files','import_chunks','record_attachments','legacy_archive']:
        for row in db.execute('SELECT * FROM ' + table + ' ORDER BY rowid'):
            keys = [key for key in row.keys() if key != 'tracking_id']
            statement = 'INSERT INTO '+table+'('+','.join(keys)+') VALUES('+','.join(literal(row[key]) for key in keys)+');\n'
            assert len(statement.encode()) < 100000, 'D1 query limit exceeded'
            output.write(statement)
    output.write('DROP TABLE production_migration_guard;\n')
db.close()
print('Prepared guarded production-only import:', target)
print('Bytes:',target.stat().st_size)
with target.open('rb') as handle: print('SHA256:',hashlib.file_digest(handle,'sha256').hexdigest())
