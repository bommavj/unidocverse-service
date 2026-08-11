-- V19__create_policies_table.sql
-- Creates the policies table linked to clients.
-- Also adds client linking columns to the existing documents table
-- so documents can be auto-suggested and manually confirmed to a client.

-- ── POLICIES TABLE ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS policies (

    -- ── Identity ──────────────────────────────────────────────────────────────
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    policy_number   VARCHAR(100) NOT NULL,              -- carrier-assigned policy number
    client_id       UUID        NOT NULL REFERENCES clients(id) ON DELETE CASCADE,

    -- ── Line of Business ──────────────────────────────────────────────────────
    line_of_business VARCHAR(50) NOT NULL
                        CHECK (line_of_business IN (
                            'homeowners',
                            'auto',
                            'commercial_property',
                            'general_liability',
                            'bop',                      -- Business Owners Policy
                            'workers_comp',
                            'umbrella',
                            'life',
                            'health',
                            'landlord',
                            'coi',                      -- Certificate of Insurance
                            'other'
                        )),

    -- ── Carrier ───────────────────────────────────────────────────────────────
    carrier_name    VARCHAR(100) NOT NULL,              -- e.g. State Farm, Allstate
    carrier_code    VARCHAR(20),                        -- internal carrier code

    -- ── Premium ───────────────────────────────────────────────────────────────
    premium_amount  NUMERIC(12,2),                      -- annual premium
    premium_freq    VARCHAR(20)  DEFAULT 'annual'
                        CHECK (premium_freq IN ('monthly', 'quarterly', 'semi_annual', 'annual')),
    payment_method  VARCHAR(50),

    -- ── Policy Dates ──────────────────────────────────────────────────────────
    effective_date  DATE        NOT NULL,
    expiry_date     DATE        NOT NULL,
    renewal_date    DATE,                               -- may differ from expiry

    -- ── Coverage Summary ──────────────────────────────────────────────────────
    coverage_summary    JSONB   DEFAULT '{}',           -- extracted by Document AI
    -- Example:
    -- {
    --   "dwelling": 480000,
    --   "liability": 300000,
    --   "deductible": 1000,
    --   "additional_coverage": ["flood", "earthquake"]
    -- }

    -- ── Status & Lifecycle ────────────────────────────────────────────────────
    status          VARCHAR(20)  NOT NULL DEFAULT 'active'
                        CHECK (status IN (
                            'quoted',
                            'bound',
                            'active',
                            'renewal_due',
                            'renewed',
                            'cancelled',
                            'lapsed',
                            'non_renewed'
                        )),

    -- ── Renewal Tracking ──────────────────────────────────────────────────────
    renewal_status  VARCHAR(30)  DEFAULT 'not_started'
                        CHECK (renewal_status IN (
                            'not_started',
                            'contacted',
                            'in_progress',
                            'complete',
                            'lost',
                            'non_renewed'
                        )),
    renewal_notes   TEXT,
    last_contacted_at TIMESTAMPTZ,

    -- ── AI Risk Scoring ───────────────────────────────────────────────────────
    risk_score      NUMERIC(4,2),                       -- 0.00 to 1.00, computed by AI
    risk_flags      TEXT[],                             -- e.g. {underinsured, expiring_soon}
    ai_insights     JSONB   DEFAULT '[]',               -- AI-generated insights array

    -- ── Agency Metadata ───────────────────────────────────────────────────────
    assigned_agent  VARCHAR(100),
    source          VARCHAR(20)  NOT NULL DEFAULT 'manual'
                        CHECK (source IN ('manual', 'document_ai', 'imported')),
    notes           TEXT,

    -- ── Audit ─────────────────────────────────────────────────────────────────
    created_by      VARCHAR(100),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ── Indexes ───────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_policies_client_id         ON policies (client_id);
CREATE INDEX IF NOT EXISTS idx_policies_status            ON policies (status);
CREATE INDEX IF NOT EXISTS idx_policies_expiry_date       ON policies (expiry_date ASC);
CREATE INDEX IF NOT EXISTS idx_policies_renewal_date      ON policies (renewal_date ASC);
CREATE INDEX IF NOT EXISTS idx_policies_carrier           ON policies (carrier_name);
CREATE INDEX IF NOT EXISTS idx_policies_line_of_business  ON policies (line_of_business);
CREATE INDEX IF NOT EXISTS idx_policies_renewal_status    ON policies (renewal_status);

-- ── Auto-update updated_at ────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION update_policies_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_policies_updated_at
    BEFORE UPDATE ON policies
    FOR EACH ROW EXECUTE FUNCTION update_policies_updated_at();

-- ── DOCUMENT LINKING ─────────────────────────────────────────────────────────
-- Adds client linking to the existing documents table.
-- auto-suggest + manual confirm flow:
--   unlinked   → document just uploaded, no suggestion yet
--   suggested  → AI found a likely client match (score stored)
--   confirmed  → agent manually confirmed the link
--   rejected   → agent rejected the AI suggestion

ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS client_id          UUID        REFERENCES clients(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS policy_id          UUID        REFERENCES policies(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS client_link_status VARCHAR(20) NOT NULL DEFAULT 'unlinked'
                                                    CHECK (client_link_status IN (
                                                        'unlinked',
                                                        'suggested',
                                                        'confirmed',
                                                        'rejected'
                                                    )),
    ADD COLUMN IF NOT EXISTS client_link_score  NUMERIC(4,2),   -- 0.00 to 1.00 AI confidence
    ADD COLUMN IF NOT EXISTS client_suggested_id UUID REFERENCES clients(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS client_linked_at   TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS client_linked_by   VARCHAR(100);   -- username who confirmed

-- ── Indexes for document linking ─────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_documents_client_id          ON documents (client_id);
CREATE INDEX IF NOT EXISTS idx_documents_policy_id          ON documents (policy_id);
CREATE INDEX IF NOT EXISTS idx_documents_client_link_status ON documents (client_link_status);
CREATE INDEX IF NOT EXISTS idx_documents_client_suggested   ON documents (client_suggested_id);

COMMENT ON TABLE  policies                          IS 'Insurance policies linked to agency clients.';
COMMENT ON COLUMN policies.coverage_summary         IS 'JSON coverage details extracted by Document AI.';
COMMENT ON COLUMN policies.ai_insights              IS 'AI-generated risk insights and recommendations.';
COMMENT ON COLUMN documents.client_link_status      IS 'unlinked→suggested→confirmed or rejected by agent.';
COMMENT ON COLUMN documents.client_link_score       IS 'AI confidence score 0-1 for the client match suggestion.';