-- V23__loss_run_intelligence.sql
-- Loss Run Intelligence: claim history, loss ratios, risk flagging

CREATE TABLE IF NOT EXISTS loss_runs (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id       UUID        NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    policy_id       UUID        REFERENCES policies(id) ON DELETE SET NULL,
    document_id     VARCHAR(255) REFERENCES documents(id) ON DELETE SET NULL,
    carrier_name    VARCHAR(100),
    line_of_business VARCHAR(50),
    period_start    DATE,
    period_end      DATE,
    -- Computed metrics
    total_claims    INTEGER DEFAULT 0,
    total_incurred  NUMERIC(12,2) DEFAULT 0,
    total_paid      NUMERIC(12,2) DEFAULT 0,
    total_reserved  NUMERIC(12,2) DEFAULT 0,
    total_premium   NUMERIC(12,2) DEFAULT 0,
    loss_ratio      NUMERIC(6,2),   -- total_incurred / total_premium * 100
    claim_frequency NUMERIC(6,4),   -- claims per $1000 premium
    -- Risk classification
    risk_level      VARCHAR(10) NOT NULL DEFAULT 'unknown'
                        CHECK (risk_level IN ('low','medium','high','critical','unknown')),
    risk_flags      TEXT[],
    ai_summary      TEXT,
    raw_data        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS loss_run_claims (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    loss_run_id     UUID        NOT NULL REFERENCES loss_runs(id) ON DELETE CASCADE,
    claim_number    VARCHAR(100),
    date_of_loss    DATE,
    date_reported   DATE,
    claim_type      VARCHAR(100),
    description     TEXT,
    status          VARCHAR(30),    -- open, closed, reserved
    amount_paid     NUMERIC(12,2) DEFAULT 0,
    amount_reserved NUMERIC(12,2) DEFAULT 0,
    amount_incurred NUMERIC(12,2) GENERATED ALWAYS AS (
                        amount_paid + amount_reserved
                    ) STORED,
    is_large_loss   BOOLEAN GENERATED ALWAYS AS (
                        (amount_paid + amount_reserved) > 25000
                    ) STORED,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_loss_runs_client         ON loss_runs(client_id);
CREATE INDEX IF NOT EXISTS idx_loss_runs_policy         ON loss_runs(policy_id);
CREATE INDEX IF NOT EXISTS idx_loss_runs_risk_level     ON loss_runs(risk_level);
CREATE INDEX IF NOT EXISTS idx_loss_run_claims_run      ON loss_run_claims(loss_run_id);
CREATE INDEX IF NOT EXISTS idx_loss_run_claims_date     ON loss_run_claims(date_of_loss);
CREATE INDEX IF NOT EXISTS idx_loss_run_claims_large    ON loss_run_claims(is_large_loss);