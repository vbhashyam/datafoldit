import hashlib
import json
import sqlite3
import tempfile
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve() if len(sys.argv)>1 else Path('/Users/vamsikrishnabhashyam/Documents/datafoldit/private-backups/production-20260909T174353Z')
manifest = json.loads((root / 'manifest.json').read_text())
for name, expected in manifest['files'].items():
    with (root / name).open('rb') as handle:
        assert hashlib.file_digest(handle, 'sha256').hexdigest() == expected, name
source = sqlite3.connect(f'file:{root / "datafoldit.db"}?mode=ro', uri=True)
with tempfile.TemporaryDirectory(prefix='datafoldit-restore-check-') as folder:
    restored = sqlite3.connect(Path(folder) / 'restored.db')
    source.backup(restored)
    assert restored.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    for table, info in manifest['tables'].items():
        assert restored.execute('SELECT count(*) FROM "' + table + '"').fetchone()[0] == info['count']
    restored.close()
missing = 0
referenced = 0
for table, column in [('bank_transactions', 'attachment_path'), ('expenses', 'attachment_path'), ('payroll_entries', 'attachment_path'), ('invoices', 'source_pdf')]:
    for (value,) in source.execute('SELECT "' + column + '" FROM "' + table + '" WHERE "' + column + '" IS NOT NULL'):
        if not value:
            continue
        referenced += 1
        if not (root / 'data' / 'attachments' / Path(value).name).is_file():
            missing += 1
source.close()
print('PASS: all file hashes and isolated database restore/count verification')
print('Attachment references:', referenced, 'not found by basename:', missing)
print('Attachment bytes:', sum(p.stat().st_size for p in (root / 'data/attachments').rglob('*') if p.is_file()))
