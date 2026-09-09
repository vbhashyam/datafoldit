-- Isolated prototype only. Never apply to the existing dashboard database.
CREATE TABLE IF NOT EXISTS poc_users (
  id TEXT PRIMARY KEY, email TEXT UNIQUE COLLATE NOCASE,
  role TEXT NOT NULL CHECK(role IN ('admin','write','read')),
  salt TEXT NOT NULL, password_hash TEXT NOT NULL, mfa_hex TEXT NOT NULL,
  last_step INTEGER NOT NULL DEFAULT -1, active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS poc_sessions (
  hash TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES poc_users(id) ON DELETE CASCADE,
  csrf TEXT NOT NULL, expires INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS poc_sessions_expiry ON poc_sessions(expires);
CREATE TABLE IF NOT EXISTS poc_login_limits (
  bucket TEXT PRIMARY KEY, started INTEGER NOT NULL, count INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS poc_transactions (
  id INTEGER PRIMARY KEY, description TEXT NOT NULL,
  amount_cents INTEGER NOT NULL, updated_by TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS test_files (
  id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, name TEXT NOT NULL,
  size INTEGER NOT NULL CHECK(size>0 AND size<=5242880)
);
CREATE TABLE IF NOT EXISTS test_file_chunks (
  file_id TEXT NOT NULL REFERENCES test_files(id) ON DELETE CASCADE,
  position INTEGER NOT NULL, data BLOB NOT NULL, PRIMARY KEY(file_id,position)
);
CREATE INDEX IF NOT EXISTS test_files_owner ON test_files(owner_id);
CREATE TRIGGER IF NOT EXISTS test_file_quota BEFORE INSERT ON test_files WHEN (SELECT COALESCE(SUM(size),0) FROM test_files)+NEW.size>104857600 OR (SELECT COUNT(*) FROM test_files)>=100 BEGIN SELECT RAISE(ABORT,'Test attachment quota reached'); END;
