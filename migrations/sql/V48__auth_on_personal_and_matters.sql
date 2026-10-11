-- V48: Add user_id to personal_notes/planner_tasks, created_by to matters
-- These columns were missing, causing all records to be globally shared across users/tenants.

ALTER TABLE personal_notes
    ADD COLUMN IF NOT EXISTS user_id TEXT;

ALTER TABLE planner_tasks
    ADD COLUMN IF NOT EXISTS user_id TEXT;

ALTER TABLE matters
    ADD COLUMN IF NOT EXISTS tenant_id TEXT,
    ADD COLUMN IF NOT EXISTS created_by TEXT;
