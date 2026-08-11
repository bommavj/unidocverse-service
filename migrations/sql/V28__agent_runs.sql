-- Agent runs: tracks each ReAct agent execution
CREATE TABLE IF NOT EXISTS agent_runs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sector          TEXT NOT NULL,          -- insurance | hr | finance | general
    trigger_type    TEXT NOT NULL,          -- manual | scheduled | upload | alert
    trigger_payload JSONB DEFAULT '{}',     -- original event context
    task_name       TEXT,                   -- e.g. "Policy Renewal Watch"
    instruction     TEXT,                   -- optional user instruction
    approval_level  TEXT DEFAULT 'external',-- external | all | irreversible | none
    status          TEXT NOT NULL DEFAULT 'idle',
                                            -- idle | thinking | acting | waiting_approval | completed | failed | rejected
    iteration       INT DEFAULT 0,          -- current loop count
    max_iterations  INT DEFAULT 10,
    history         JSONB DEFAULT '[]',     -- full thought+tool+observation log
    approval_id     UUID,                   -- FK → approval_requests.id when escalated
    outcome         JSONB,                  -- final_answer payload
    error_message   TEXT,
    started_at      TIMESTAMP DEFAULT NOW(),
    updated_at      TIMESTAMP DEFAULT NOW(),
    ended_at        TIMESTAMP
);

-- Tool calls: one row per tool invocation inside a run
CREATE TABLE IF NOT EXISTS agent_tool_calls (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id      UUID NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
    iteration   INT  NOT NULL,
    tool_name   TEXT NOT NULL,
    args        JSONB DEFAULT '{}',
    result      JSONB,
    error       TEXT,
    latency_ms  INT,
    called_at   TIMESTAMP DEFAULT NOW()
);

-- Ensure approval_requests exists (may be created at runtime by approvals_router)
CREATE TABLE IF NOT EXISTS approval_requests (
    id          TEXT PRIMARY KEY,
    doc_id      TEXT NOT NULL DEFAULT '',
    title       TEXT NOT NULL,
    description TEXT,
    approvers   JSONB DEFAULT '[]',
    decisions   JSONB DEFAULT '[]',
    comments    JSONB DEFAULT '[]',
    status      TEXT NOT NULL DEFAULT 'pending',
    priority    TEXT DEFAULT 'normal',
    due_date    TEXT,
    created_at  TIMESTAMP DEFAULT NOW(),
    updated_at  TIMESTAMP DEFAULT NOW()
);

-- Extend approval_requests with agent linkage
ALTER TABLE approval_requests
    ADD COLUMN IF NOT EXISTS agent_run_id    UUID,
    ADD COLUMN IF NOT EXISTS agent_reasoning TEXT,
    ADD COLUMN IF NOT EXISTS proposed_action JSONB;

-- Indexes
CREATE INDEX IF NOT EXISTS idx_agent_runs_status  ON agent_runs(status);
CREATE INDEX IF NOT EXISTS idx_agent_runs_sector  ON agent_runs(sector);
CREATE INDEX IF NOT EXISTS idx_agent_tool_run     ON agent_tool_calls(run_id);
