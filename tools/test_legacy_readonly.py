import sqlite3
import sys
from pathlib import Path

sys.path.insert(0,'/Users/vamsikrishnabhashyam/Documents/datafoldit-prod')
from datafoldit import web
assert web.READ_ONLY_COMPARISON is True
handler=web.make_handler(Path('/Users/vamsikrishnabhashyam/Documents/datafoldit-prod-data/datafoldit.db'))
instance=handler.__new__(handler)
assert instance.conn.execute('PRAGMA query_only').fetchone()[0]==1
try:
    instance.conn.execute('UPDATE bank_transactions SET amount=amount WHERE 0')
except sqlite3.OperationalError as error:
    assert 'readonly' in str(error).lower() or 'read-only' in str(error).lower()
else:
    raise AssertionError('Comparison DB accepted a write')
instance.conn.close()
for path in ['/bank/add','/expenses/add','/payroll/add','/invoices/add','/bank/extract-inline','/import','/anything']:
    probe=handler.__new__(handler);probe.path=path;result=[];probe.send_json=lambda body,status:result.append(status)
    probe.do_POST();assert result==[403]
probe=handler.__new__(handler);probe.path='/login';probe.read_form=lambda:{'password':['synthetic']};result=[];probe.redirect=lambda *args,**kwargs:result.append(args[0]);web.verify_password=lambda _:True;web.session_cookie=lambda:'synthetic'
probe.do_POST();assert result==['/']
print('PASS: old production read-only database and mutation rejection; login preserved; no business writes performed')
