-- V48: Add user_id to personal_notes/planner_tasks, created_by to matters
-- These columns were missing, causing all records to be globally shared across users/tenants.

DO $$ BEGIN IF to_regclass('public.personal_notes') IS NOT NULL THEN EXECUTE $q$ALTER TABLE personal_notes
    ADD COLUMN IF NOT EXISTS user_id TEXT$q$; END IF; END $$;

DO $$ BEGIN IF to_regclass('public.planner_tasks') IS NOT NULL THEN EXECUTE $q$ALTER TABLE planner_tasks
    ADD COLUMN IF NOT EXISTS user_id TEXT$q$; END IF; END $$;

DO $$ BEGIN IF to_regclass('public.matters') IS NOT NULL THEN EXECUTE $q$ALTER TABLE matters
    ADD COLUMN IF NOT EXISTS tenant_id TEXT,
    ADD COLUMN IF NOT EXISTS created_by TEXT$q$; END IF; END $$;
