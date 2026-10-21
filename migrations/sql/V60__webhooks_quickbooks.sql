-- V60: Outbound webhooks and the QuickBooks Online connector.

-- ── Webhooks ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS webhook_endpoints (
    id          BIGSERIAL   PRIMARY KEY,
    tenant_id   TEXT        NOT NULL,
    url         TEXT        NOT NULL,
    description TEXT,
    secret_enc  TEXT        NOT NULL,                 -- signing secret, encrypted (secret_box)
    events      TEXT[]      NOT NULL DEFAULT '{}',    -- empty = all events
    active      BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by  TEXT
);
CREATE INDEX IF NOT EXISTS idx_webhook_endpoints_tenant ON webhook_endpoints (tenant_id);

CREATE TABLE IF NOT EXISTS webhook_deliveries (
    id              BIGSERIAL   PRIMARY KEY,
    endpoint_id     BIGINT      NOT NULL REFERENCES webhook_endpoints(id) ON DELETE CASCADE,
    tenant_id       TEXT        NOT NULL,
    event           TEXT        NOT NULL,
    payload         JSONB       NOT NULL,
    status          TEXT        NOT NULL DEFAULT 'pending',   -- pending | delivered | failed
    attempts        INTEGER     NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    response_code   INTEGER,
    response_body   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    delivered_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_due ON webhook_deliveries (next_attempt_at) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_endpoint ON webhook_deliveries (endpoint_id, created_at DESC);

-- ── QuickBooks Online ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS qb_connections (
    tenant_id          TEXT        PRIMARY KEY,
    environment        TEXT        NOT NULL DEFAULT 'sandbox',   -- sandbox | production
    client_id          TEXT        NOT NULL,
    client_secret_enc  TEXT        NOT NULL,
    realm_id           TEXT,                                     -- QuickBooks company id
    company_name       TEXT,
    access_token_enc   TEXT,
    refresh_token_enc  TEXT,
    access_expires_at  TIMESTAMPTZ,
    expense_account_id TEXT,                                     -- default account for bill lines
    oauth_state        TEXT,
    oauth_state_expires TIMESTAMPTZ,
    connected_at       TIMESTAMPTZ,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per bill prepared from a document: the draft, the approval, and what QuickBooks returned
CREATE TABLE IF NOT EXISTS qb_pushes (
    id            BIGSERIAL   PRIMARY KEY,
    tenant_id     TEXT        NOT NULL,
    document_id   TEXT        NOT NULL,
    status        TEXT        NOT NULL DEFAULT 'draft',   -- draft | posted | failed | discarded
    draft         JSONB       NOT NULL,                   -- vendor, amount, dates, invoice number, lines
    checks        JSONB       NOT NULL DEFAULT '[]'::jsonb,
    qb_bill_id    TEXT,
    qb_vendor_id  TEXT,
    error         TEXT,
    prepared_by   TEXT,
    approved_by   TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    posted_at     TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_qb_pushes_tenant ON qb_pushes (tenant_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_qb_pushes_one_posted ON qb_pushes (tenant_id, document_id) WHERE status = 'posted';
