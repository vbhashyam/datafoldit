ALTER TABLE dashboard_records ADD COLUMN tracking_id TEXT;

CREATE TABLE transaction_sequences (
 kind TEXT PRIMARY KEY CHECK(kind IN ('bank','expenses','payroll')),
 last_value INTEGER NOT NULL CHECK(last_value >= 0)
);

INSERT INTO transaction_sequences(kind,last_value)
SELECT 'bank',COUNT(*) FROM dashboard_records WHERE kind='bank';
INSERT INTO transaction_sequences(kind,last_value)
SELECT 'expenses',COUNT(*) FROM dashboard_records WHERE kind='expenses';
INSERT INTO transaction_sequences(kind,last_value)
SELECT 'payroll',COUNT(*) FROM dashboard_records WHERE kind='payroll';

WITH numbered AS (
 SELECT id,kind,ROW_NUMBER() OVER(PARTITION BY kind ORDER BY updated_at,id) AS seq
 FROM dashboard_records WHERE kind IN ('bank','expenses','payroll')
)
UPDATE dashboard_records SET tracking_id=(
 SELECT (CASE numbered.kind WHEN 'bank' THEN 'BNK-' WHEN 'expenses' THEN 'EXP-' ELSE 'PAY-' END)
 ||printf('%06d',seq) FROM numbered WHERE numbered.id=dashboard_records.id
) WHERE kind IN ('bank','expenses','payroll');

CREATE UNIQUE INDEX unique_transaction_tracking ON dashboard_records(tracking_id)
WHERE tracking_id IS NOT NULL;

CREATE TRIGGER assign_transaction_tracking AFTER INSERT ON dashboard_records
WHEN NEW.kind IN ('bank','expenses','payroll')
BEGIN
 UPDATE transaction_sequences SET last_value=last_value+1 WHERE kind=NEW.kind;
 UPDATE dashboard_records SET tracking_id=(
  SELECT (CASE NEW.kind WHEN 'bank' THEN 'BNK-' WHEN 'expenses' THEN 'EXP-' ELSE 'PAY-' END)
  ||printf('%06d',last_value) FROM transaction_sequences WHERE kind=NEW.kind
 ) WHERE id=NEW.id;
END;

CREATE TRIGGER protect_transaction_tracking BEFORE UPDATE OF tracking_id ON dashboard_records
WHEN OLD.tracking_id IS NOT NULL AND NEW.tracking_id IS NOT OLD.tracking_id
BEGIN
 SELECT RAISE(ABORT,'Transaction tracking ID cannot be changed');
END;
