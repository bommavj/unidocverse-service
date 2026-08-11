-- V20__commission_reconciliation.sql
-- Commission Reconciliation: carrier statements vs expected commissions

CREATE TABLE IF NOT EXISTS commission_statements (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    carrier_name    VARCHAR(100) NOT NULL,
    statement_date  DATE,
    period_start    DATE,
    period_end      DATE,
    document_id     VARCHAR(255) REFERENCES documents(id) ON DELETE SET NULL,
    total_amount    NUMERIC(12,2) DEFAULT 0,
    status          VARCHAR(20) NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','processing','reconciled','discrepancy')),
    raw_data        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS commission_line_items (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    statement_id        UUID        NOT NULL REFERENCES commission_statements(id) ON DELETE CASCADE,
    policy_id           UUID        REFERENCES policies(id) ON DELETE SET NULL,
    policy_number       VARCHAR(100),
    insured_name        VARCHAR(200),
    line_of_business    VARCHAR(50),
    effective_date      DATE,
    expiry_date         DATE,
    premium_amount      NUMERIC(12,2),
    commission_rate     NUMERIC(5,2),
    commission_amount   NUMERIC(12,2),
    -- Reconciliation
    expected_commission NUMERIC(12,2),
    variance            NUMERIC(12,2) GENERATED ALWAYS AS (
                            commission_amount - COALESCE(expected_commission, commission_amount)
                        ) STORED,
    match_status        VARCHAR(20) NOT NULL DEFAULT 'unmatched'
                            CHECK (match_status IN ('matched','unmatched','discrepancy','manual_override')),
    match_confidence    NUMERIC(4,2),
    notes               TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS commission_reconciliation_runs (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    statement_id    UUID        NOT NULL REFERENCES commission_statements(id) ON DELETE CASCADE,
    run_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    total_lines     INTEGER DEFAULT 0,
    matched_lines   INTEGER DEFAULT 0,
    unmatched_lines INTEGER DEFAULT 0,
    discrepancy_lines INTEGER DEFAULT 0,
    total_expected  NUMERIC(12,2) DEFAULT 0,
    total_actual    NUMERIC(12,2) DEFAULT 0,
    total_variance  NUMERIC(12,2) GENERATED ALWAYS AS (
                        total_actual - total_expected
                    ) STORED,
    summary         JSONB DEFAULT '{}',
    created_by      VARCHAR(100)
);

CREATE INDEX IF NOT EXISTS idx_comm_statements_carrier   ON commission_statements(carrier_name);
CREATE INDEX IF NOT EXISTS idx_comm_statements_status    ON commission_statements(status);
CREATE INDEX IF NOT EXISTS idx_comm_lines_statement      ON commission_line_items(statement_id);
CREATE INDEX IF NOT EXISTS idx_comm_lines_policy         ON commission_line_items(policy_id);
CREATE INDEX IF NOT EXISTS idx_comm_lines_policy_number  ON commission_line_items(policy_number);
CREATE INDEX IF NOT EXISTS idx_comm_lines_match_status   ON commission_line_items(match_status);