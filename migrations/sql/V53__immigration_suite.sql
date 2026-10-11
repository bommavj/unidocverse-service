-- Immigration suite: cases + tracking, CRM leads, intake questionnaires, e-signature requests.
-- Every table is tenant-scoped.

CREATE TABLE IF NOT EXISTS imm_cases (
    id              TEXT PRIMARY KEY,
    tenant_id       UUID NOT NULL REFERENCES tenants(id),
    client_id       TEXT,
    lead_id         TEXT,
    beneficiary     TEXT NOT NULL,
    petitioner      TEXT,
    case_type       TEXT NOT NULL,              -- key in immigration_rules.yaml (h1b_extension, i485, …)
    visa_class      TEXT,
    status          TEXT NOT NULL DEFAULT 'intake',
    receipt_number  TEXT,
    filed_date      DATE,
    priority_date   DATE,
    decision_date   DATE,
    rfe_due_date    DATE,
    expiry_date     DATE,                       -- current status / document expiry this case extends
    notes           TEXT,
    created_at      TIMESTAMP DEFAULT NOW(),
    updated_at      TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_imm_cases_tenant ON imm_cases (tenant_id, status);

CREATE TABLE IF NOT EXISTS imm_case_events (
    id          TEXT PRIMARY KEY,
    case_id     TEXT NOT NULL REFERENCES imm_cases(id) ON DELETE CASCADE,
    at          TIMESTAMP DEFAULT NOW(),
    kind        TEXT NOT NULL,                  -- status, note, filing, rfe, document, signature, intake
    text        TEXT NOT NULL,
    actor       TEXT
);
CREATE INDEX IF NOT EXISTS idx_imm_events_case ON imm_case_events (case_id, at);

CREATE TABLE IF NOT EXISTS imm_case_checklist (
    id          TEXT PRIMARY KEY,
    case_id     TEXT NOT NULL REFERENCES imm_cases(id) ON DELETE CASCADE,
    item        TEXT NOT NULL,
    required    BOOLEAN DEFAULT TRUE,
    done        BOOLEAN DEFAULT FALSE,
    document_id TEXT,
    position    INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS imm_leads (
    id              TEXT PRIMARY KEY,
    tenant_id       UUID NOT NULL REFERENCES tenants(id),
    name            TEXT NOT NULL,
    email           TEXT,
    phone           TEXT,
    language        TEXT DEFAULT 'en',
    source          TEXT,
    interest        TEXT,                       -- case type they asked about
    stage           TEXT NOT NULL DEFAULT 'lead',   -- lead, consult_scheduled, consulted, retained, lost
    next_follow_up  DATE,
    notes           TEXT,
    case_id         TEXT,
    created_at      TIMESTAMP DEFAULT NOW(),
    updated_at      TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_imm_leads_tenant ON imm_leads (tenant_id, stage);

CREATE TABLE IF NOT EXISTS imm_intakes (
    id          TEXT PRIMARY KEY,
    tenant_id   UUID NOT NULL REFERENCES tenants(id),
    case_id     TEXT REFERENCES imm_cases(id) ON DELETE CASCADE,
    token_hash  TEXT NOT NULL UNIQUE,           -- sha256 of the client link token; the token itself is never stored
    case_type   TEXT NOT NULL,
    language    TEXT DEFAULT 'en',
    answers     JSONB DEFAULT '{}',
    status      TEXT NOT NULL DEFAULT 'sent',   -- sent, in_progress, submitted
    expires_at  TIMESTAMP,
    created_at  TIMESTAMP DEFAULT NOW(),
    submitted_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS imm_signatures (
    id              TEXT PRIMARY KEY,
    tenant_id       UUID NOT NULL REFERENCES tenants(id),
    case_id         TEXT REFERENCES imm_cases(id) ON DELETE CASCADE,
    title           TEXT NOT NULL,
    document_id     TEXT,
    document_sha256 TEXT,                       -- hash of exactly what the signer saw
    signer_name     TEXT NOT NULL,
    signer_email    TEXT,
    token_hash      TEXT NOT NULL UNIQUE,
    status          TEXT NOT NULL DEFAULT 'sent',   -- sent, viewed, signed, declined, voided
    signed_name     TEXT,
    signed_at       TIMESTAMP,
    audit           JSONB DEFAULT '[]',         -- [{at, event, ip, user_agent}]
    expires_at      TIMESTAMP,
    created_at      TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_imm_sig_tenant ON imm_signatures (tenant_id, status);
