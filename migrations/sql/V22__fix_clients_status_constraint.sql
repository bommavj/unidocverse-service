-- V25__fix_clients_status_constraint.sql
-- Add 'deleted' to clients status check constraint
-- Required for soft-delete functionality

ALTER TABLE clients DROP CONSTRAINT IF EXISTS clients_status_check;

ALTER TABLE clients ADD CONSTRAINT clients_status_check
    CHECK (status IN ('active', 'inactive', 'prospect', 'deleted'));

-- Also exclude deleted clients from normal queries by default
-- Create a view that filters out deleted clients
CREATE OR REPLACE VIEW active_clients AS
    SELECT * FROM clients WHERE status != 'deleted';

COMMENT ON CONSTRAINT clients_status_check ON clients IS
    'Valid client statuses: active, inactive, prospect, deleted (soft-delete)';