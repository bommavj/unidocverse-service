-- Scheduled agents: one row per feature-flag-driven agent
CREATE TABLE IF NOT EXISTS scheduled_agents (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    flag_name       TEXT NOT NULL UNIQUE,   -- matches settings column, e.g. 'agency_mode'
    agent_type      TEXT NOT NULL,           -- 'insurance' | 'legal' | 'finance' etc.
    task_name       TEXT NOT NULL,
    cron_hour       INT  NOT NULL DEFAULT 7, -- hour to run (24h)
    cron_minute     INT  NOT NULL DEFAULT 0,
    enabled         BOOLEAN NOT NULL DEFAULT FALSE,
    last_run_at     TIMESTAMP,
    last_run_id     UUID,                    -- FK → agent_runs.id of last execution
    created_at      TIMESTAMP DEFAULT NOW(),
    updated_at      TIMESTAMP DEFAULT NOW()
);

-- Seed one row per feature flag — disabled by default, enabled when flag is toggled on
INSERT INTO scheduled_agents (flag_name, agent_type, task_name, cron_hour, cron_minute, enabled)
VALUES
    ('agency_mode',       'insurance',    'Insurance Agency Daily Report',    4, 0, FALSE),
    ('legal_mode',        'legal',        'Legal & Compliance Daily Report',  4, 0, FALSE),
    ('medical_mode',      'medical',      'Healthcare Admin Daily Report',    4, 0, FALSE),
    ('finance_mode',      'finance',      'Finance & Audit Daily Report',     4, 0, FALSE),
    ('real_estate_mode',  'real_estate',  'Real Estate Daily Report',         4, 0, FALSE),
    ('hr_mode',           'hr',           'HR & People Daily Report',         4, 0, FALSE),
    ('supply_chain_mode', 'supply_chain', 'Supply Chain Daily Report',        4, 0, FALSE)
ON CONFLICT (flag_name) DO NOTHING;

-- Agent-generated reports
CREATE TABLE IF NOT EXISTS agent_reports (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id      UUID REFERENCES agent_runs(id) ON DELETE SET NULL,
    agent_type  TEXT NOT NULL,
    report_date DATE NOT NULL DEFAULT CURRENT_DATE,
    title       TEXT NOT NULL,
    summary     TEXT,
    body        JSONB DEFAULT '{}',  -- structured report sections
    created_at  TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agent_reports_type ON agent_reports(agent_type, report_date DESC);
CREATE INDEX IF NOT EXISTS idx_scheduled_agents_enabled ON scheduled_agents(enabled, flag_name);
