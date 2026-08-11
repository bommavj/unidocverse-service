-- V22__policy_gap_analysis.sql
-- Policy Gap Analysis: per-client coverage gap detection

CREATE TABLE IF NOT EXISTS gap_analyses (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id       UUID        NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    analyzed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    analyzed_by     VARCHAR(100),   -- 'ai' or username
    overall_score   NUMERIC(4,2),   -- 0-100, higher = better covered
    overall_grade   VARCHAR(2),     -- A, B, C, D, F
    total_gaps      INTEGER DEFAULT 0,
    critical_gaps   INTEGER DEFAULT 0,
    total_premium   NUMERIC(12,2) DEFAULT 0,
    summary         TEXT,
    raw_analysis    JSONB DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS gap_recommendations (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    gap_analysis_id UUID        NOT NULL REFERENCES gap_analyses(id) ON DELETE CASCADE,
    client_id       UUID        NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    priority        VARCHAR(10) NOT NULL DEFAULT 'medium'
                        CHECK (priority IN ('critical','high','medium','low')),
    category        VARCHAR(50),    -- coverage_gap, low_limit, missing_coverage, overlap
    title           VARCHAR(200)    NOT NULL,
    description     TEXT,
    affected_policy_id UUID REFERENCES policies(id) ON DELETE SET NULL,
    recommended_coverage VARCHAR(100),
    estimated_premium_impact NUMERIC(10,2),
    status          VARCHAR(20) NOT NULL DEFAULT 'open'
                        CHECK (status IN ('open','acknowledged','actioned','dismissed')),
    actioned_by     VARCHAR(100),
    actioned_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_gap_analyses_client      ON gap_analyses(client_id);
CREATE INDEX IF NOT EXISTS idx_gap_analyses_date        ON gap_analyses(analyzed_at DESC);
CREATE INDEX IF NOT EXISTS idx_gap_recs_analysis        ON gap_recommendations(gap_analysis_id);
CREATE INDEX IF NOT EXISTS idx_gap_recs_client          ON gap_recommendations(client_id);
CREATE INDEX IF NOT EXISTS idx_gap_recs_priority        ON gap_recommendations(priority);
CREATE INDEX IF NOT EXISTS idx_gap_recs_status          ON gap_recommendations(status);