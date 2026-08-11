-- V39__add_compliance_operations_flags.sql
-- Adds compliance_mode and operations_mode feature flags to the settings table.
-- These control the Compliance & Risk and Operations sidebar sections independently
-- from agency_mode, which previously controlled all three.
ALTER TABLE settings
    ADD COLUMN IF NOT EXISTS compliance_mode BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE settings
    ADD COLUMN IF NOT EXISTS operations_mode BOOLEAN NOT NULL DEFAULT FALSE;
