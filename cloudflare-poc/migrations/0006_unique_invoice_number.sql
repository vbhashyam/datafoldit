CREATE UNIQUE INDEX unique_invoice_number
ON dashboard_records(lower(trim(json_extract(data, '$.invoice_number'))))
WHERE kind = 'invoices';
