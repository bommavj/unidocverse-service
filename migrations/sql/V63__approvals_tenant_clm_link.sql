-- V63: Approvals belong to an organisation, record who asked, expire when stale,
-- and connect to contract lifecycle entries.

CREATE TABLE IF NOT EXISTS approval_requests (
    id TEXT PRIMARY KEY, doc_id TEXT NOT NULL, title TEXT NOT NULL, description TEXT,
    approvers JSONB DEFAULT '[]', decisions JSONB DEFAULT '[]', comments JSONB DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'pending', priority TEXT DEFAULT 'normal', due_date TEXT,
    created_at TIMESTAMP DEFAULT NOW(), updated_at TIMESTAMP DEFAULT NOW()
);

ALTER TABLE approval_requests ADD COLUMN IF NOT EXISTS tenant_id    TEXT;
ALTER TABLE approval_requests ADD COLUMN IF NOT EXISTS requested_by TEXT;
ALTER TABLE approval_requests ADD COLUMN IF NOT EXISTS source       TEXT NOT NULL DEFAULT 'manual'; -- manual | agent | clarification | clm
ALTER TABLE approval_requests ADD COLUMN IF NOT EXISTS clm_id       TEXT;
ALTER TABLE approval_requests ADD COLUMN IF NOT EXISTS expires_at   TIMESTAMP;

-- Existing rows: organisation from the agent run or the document, else the default organisation
UPDATE approval_requests a SET tenant_id = coalesce(
        (SELECT r.tenant_id::text FROM agent_runs r WHERE r.id::text = a.agent_run_id::text),
        (SELECT d.tenant_id::text FROM documents d WHERE d.id::text = a.doc_id),
        '00000000-0000-0000-0000-000000000001')
WHERE a.tenant_id IS NULL;

UPDATE approval_requests SET source = CASE
        WHEN title LIKE 'Clarification needed:%' THEN 'clarification'
        WHEN agent_run_id IS NOT NULL OR title LIKE 'Research Agent:%' THEN 'agent'
        ELSE 'manual' END
WHERE source = 'manual';

-- Agent actions and clarifications go stale; a person's own request doesn't expire
UPDATE approval_requests SET expires_at = created_at + interval '30 days'
WHERE expires_at IS NULL AND source IN ('agent', 'clarification');

CREATE INDEX IF NOT EXISTS idx_approval_requests_tenant_status ON approval_requests (tenant_id, status, created_at DESC);

-- Contract lifecycle ↔ approval
CREATE TABLE IF NOT EXISTS clm_entries (
    id TEXT PRIMARY KEY, doc_id TEXT NOT NULL, stage TEXT NOT NULL DEFAULT 'draft', contract_value NUMERIC,
    counterparty TEXT, governing_law TEXT, notes TEXT, created_at TIMESTAMP DEFAULT NOW(), updated_at TIMESTAMP DEFAULT NOW()
);
ALTER TABLE clm_entries ADD COLUMN IF NOT EXISTS approval_id TEXT;
ALTER TABLE clm_entries ADD COLUMN IF NOT EXISTS end_date    DATE;    -- contract end; drives "expiring"
