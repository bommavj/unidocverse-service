-- V68: Salesforce connector (app/services/salesforce.py). Documents go to the client's Account after approval.
CREATE TABLE IF NOT EXISTS sf_connections (
    tenant_id           TEXT        PRIMARY KEY,
    login_host          TEXT        NOT NULL DEFAULT 'https://login.salesforce.com',
    client_id           TEXT        NOT NULL,
    client_secret_enc   TEXT        NOT NULL,
    instance_url        TEXT,
    org_name            TEXT,
    user_name           TEXT,
    access_token_enc    TEXT,
    refresh_token_enc   TEXT,
    oauth_state         TEXT,
    oauth_state_expires TIMESTAMPTZ,
    connected_at        TIMESTAMPTZ,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sf_pushes (
    id            BIGSERIAL   PRIMARY KEY,
    tenant_id     TEXT        NOT NULL,
    document_id   TEXT        NOT NULL,
    status        TEXT        NOT NULL DEFAULT 'draft',      -- draft | sent | failed | discarded
    draft         JSONB       NOT NULL,
    checks        JSONB       NOT NULL DEFAULT '[]'::jsonb,
    sf_account_id TEXT,
    sf_content_id TEXT,
    sf_task_id    TEXT,
    error         TEXT,
    prepared_by   TEXT,
    approved_by   TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at       TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_sf_pushes_doc ON sf_pushes (tenant_id, document_id, id DESC);
