CREATE TABLE dashboard_records (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('bank','expenses','payroll','invoices')),
 data TEXT NOT NULL CHECK(json_valid(data)), updated_by TEXT NOT NULL,
 version INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX dashboard_kind ON dashboard_records(kind);
