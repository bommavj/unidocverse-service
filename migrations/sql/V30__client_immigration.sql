-- V30: client_immigration — tracks all immigration documents per client
-- Covers: H1B, I-94, EAD, Passport, I-20, Green Card, L1, O1, TN, B1/B2, etc.

CREATE TABLE IF NOT EXISTS client_immigration (
    id                  VARCHAR PRIMARY KEY DEFAULT gen_random_uuid()::text,
    client_id           UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    linked_document_id  VARCHAR(36) REFERENCES documents(id) ON DELETE SET NULL,

    -- Document classification
    doc_type            VARCHAR(50)  NOT NULL,  -- h1b | i94 | ead | passport | i20 | green_card | l1 | o1 | tn | b1b2 | other
    doc_label           VARCHAR(100),           -- Human-readable: "H-1B Approval Notice"
    document_number     VARCHAR(100),           -- Receipt number / document number
    visa_class          VARCHAR(30),            -- H-1B, L-1A, O-1B, EB-2, etc.

    -- Parties
    beneficiary_name    VARCHAR(200),
    employer_sponsor    VARCHAR(200),
    issuing_authority   VARCHAR(100),           -- USCIS, DHS, DOS, CBP, etc.

    -- Dates
    issue_date          DATE,
    valid_from          DATE,
    valid_until         DATE,                   -- Primary expiry date → drives alerts
    authorized_stay_until DATE,                 -- I-94 "admit until" — can differ from visa expiry
    priority_date       DATE,                   -- For green card / preference category tracking

    -- Status
    status              VARCHAR(30) NOT NULL DEFAULT 'active',
    -- active | expired | pending | approved | denied | withdrawn | transferred

    notes               TEXT,
    raw_extracted       JSONB,                  -- Full output from ImmigrationReceiptAnalyzer

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_ci_client          ON client_immigration(client_id);
CREATE INDEX idx_ci_valid_until     ON client_immigration(valid_until) WHERE valid_until IS NOT NULL;
CREATE INDEX idx_ci_auth_stay       ON client_immigration(authorized_stay_until) WHERE authorized_stay_until IS NOT NULL;
CREATE INDEX idx_ci_status          ON client_immigration(status);
CREATE INDEX idx_ci_doc_type        ON client_immigration(doc_type);

-- Partial index for expiry alert scheduler (only active docs with future expiries)
CREATE INDEX idx_ci_expiry_alerts
    ON client_immigration(valid_until, client_id)
    WHERE status = 'active' AND valid_until IS NOT NULL;

-- Auto-update updated_at
CREATE OR REPLACE FUNCTION update_ci_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
$$;

CREATE TRIGGER trg_ci_updated_at
    BEFORE UPDATE ON client_immigration
    FOR EACH ROW EXECUTE FUNCTION update_ci_updated_at();
