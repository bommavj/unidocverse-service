-- V26__fix_policies_status_check.sql
-- Add 'deleted' to policies status check constraint

ALTER TABLE policies DROP CONSTRAINT IF EXISTS policies_status_check;

ALTER TABLE policies ADD CONSTRAINT policies_status_check
  CHECK (status IN (
    'quoted',
    'bound',
    'active',
    'renewal_due',
    'renewed',
    'cancelled',
    'lapsed',
    'non_renewed',
    'deleted'
  ));
