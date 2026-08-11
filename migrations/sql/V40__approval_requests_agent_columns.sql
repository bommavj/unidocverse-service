-- Ensure approval_requests has agent linkage columns (V28 ALTER may not have run)
ALTER TABLE approval_requests
    ADD COLUMN IF NOT EXISTS agent_run_id    UUID,
    ADD COLUMN IF NOT EXISTS agent_reasoning TEXT,
    ADD COLUMN IF NOT EXISTS proposed_action JSONB;

-- Ensure agent_runs has all V28 columns (table may have been created by an earlier migration)
ALTER TABLE agent_runs
    ADD COLUMN IF NOT EXISTS sector          TEXT NOT NULL DEFAULT 'general',
    ADD COLUMN IF NOT EXISTS trigger_type    TEXT NOT NULL DEFAULT 'manual',
    ADD COLUMN IF NOT EXISTS trigger_payload JSONB DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS instruction     TEXT,
    ADD COLUMN IF NOT EXISTS approval_level  TEXT DEFAULT 'external',
    ADD COLUMN IF NOT EXISTS iteration       INT DEFAULT 0,
    ADD COLUMN IF NOT EXISTS max_iterations  INT DEFAULT 10,
    ADD COLUMN IF NOT EXISTS history         JSONB DEFAULT '[]',
    ADD COLUMN IF NOT EXISTS approval_id     UUID,
    ADD COLUMN IF NOT EXISTS outcome         JSONB,
    ADD COLUMN IF NOT EXISTS error_message   TEXT,
    ADD COLUMN IF NOT EXISTS updated_at      TIMESTAMP DEFAULT NOW(),
    ADD COLUMN IF NOT EXISTS ended_at        TIMESTAMP;

CREATE TABLE IF NOT EXISTS agent_tool_calls (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id      UUID NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
    iteration   INT NOT NULL DEFAULT 0,
    tool_name   TEXT NOT NULL,
    tool_args   JSONB DEFAULT '{}',
    tool_result JSONB,
    started_at  TIMESTAMP DEFAULT NOW(),
    ended_at    TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_agent_runs_status  ON agent_runs(status);
CREATE INDEX IF NOT EXISTS idx_agent_runs_sector  ON agent_runs(sector);
CREATE INDEX IF NOT EXISTS idx_agent_tool_run     ON agent_tool_calls(run_id);
