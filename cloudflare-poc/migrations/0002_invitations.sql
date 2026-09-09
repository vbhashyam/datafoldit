CREATE TABLE IF NOT EXISTS poc_invitations (
  email TEXT PRIMARY KEY COLLATE NOCASE, token_hash TEXT NOT NULL UNIQUE,
  role TEXT NOT NULL CHECK(role IN ('admin','read','write')),
  expires INTEGER NOT NULL, consumed INTEGER NOT NULL DEFAULT 0,
  attempts INTEGER NOT NULL DEFAULT 0, mfa_hex TEXT NOT NULL
);
