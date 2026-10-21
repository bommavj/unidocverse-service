-- Cloud model providers per organisation (Anthropic, OpenAI, Gemini, xAI, Mistral, …). API keys are stored
-- encrypted (app/core/secret_box.py); at most one provider is active per tenant,
-- and while one is active it answers questions instead of the local Ollama model.
CREATE TABLE IF NOT EXISTS tenant_llm_providers (
    tenant_id   TEXT        NOT NULL,
    provider    TEXT        NOT NULL,          -- key in app/core/llm.PROVIDERS
    api_key_enc TEXT        NOT NULL,
    model       TEXT        NOT NULL,
    base_url    TEXT,                          -- only for the 'custom' OpenAI-compatible provider
    is_active   BOOLEAN     NOT NULL DEFAULT FALSE,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by  TEXT,
    PRIMARY KEY (tenant_id, provider)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_tenant_llm_one_active
    ON tenant_llm_providers (tenant_id) WHERE is_active;

-- What may go to a cloud provider (Settings → Integrations → Cloud models → Data protection)
CREATE TABLE IF NOT EXISTS tenant_llm_policy (
    tenant_id        TEXT        PRIMARY KEY,
    ask_before_send  BOOLEAN     NOT NULL DEFAULT TRUE,   -- show the documents and ask before each cloud answer
    mask_identifiers BOOLEAN     NOT NULL DEFAULT TRUE,   -- SSNs, A-numbers, emails, phones, account numbers → REF_0001
    blocked_types    TEXT[]      NOT NULL DEFAULT '{}',   -- document types answered locally only
    upload_cloud     BOOLEAN     NOT NULL DEFAULT FALSE,  -- use the cloud model for field extraction on upload
    upload_types     TEXT[]      NOT NULL DEFAULT '{}',   -- … only for these document types
    upload_model     TEXT,                                -- optional cheaper model for uploads
    monthly_budget_usd NUMERIC(10,2),                     -- cloud calls fall back to local once reached
    cascade_local_first BOOLEAN  NOT NULL DEFAULT TRUE,   -- answer locally first; cloud only if the source check fails
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by       TEXT
);

-- Audit of every call that sent data to a cloud provider
CREATE TABLE IF NOT EXISTS llm_cloud_calls (
    id             BIGSERIAL   PRIMARY KEY,
    tenant_id      TEXT        NOT NULL,
    username       TEXT,
    provider       TEXT        NOT NULL,
    model          TEXT        NOT NULL,
    documents      JSONB       NOT NULL DEFAULT '[]'::jsonb,
    chars_sent     INTEGER     NOT NULL,
    masked_values  INTEGER     NOT NULL DEFAULT 0,
    input_tokens   INTEGER,
    output_tokens  INTEGER,
    est_cost_usd   NUMERIC(12,6),
    status         TEXT        NOT NULL,                   -- ok | error
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_llm_cloud_calls_tenant ON llm_cloud_calls (tenant_id, created_at DESC);

-- One row per cascade decision (local first, cloud only if the source check fails).
-- Stays in this database; labelled data for evaluating and training the escalation decision.
CREATE TABLE IF NOT EXISTS llm_cascade_outcomes (
    id              BIGSERIAL   PRIMARY KEY,
    tenant_id       TEXT        NOT NULL,
    username        TEXT,
    question        TEXT        NOT NULL,
    documents       JSONB       NOT NULL DEFAULT '[]'::jsonb,
    local_answer    TEXT,
    local_passed    BOOLEAN,                -- NULL when the local step was skipped
    local_reason    TEXT,                   -- verified | no_figures | not_found | ungrounded | skipped
    escalated       BOOLEAN     NOT NULL DEFAULT FALSE,
    outcome         TEXT        NOT NULL,   -- local | cloud | consent_requested
    cloud_model     TEXT,
    cloud_answer    TEXT,
    cloud_verified  BOOLEAN,
    disagreement    JSONB,                  -- local figures missing from the verified answer
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_llm_cascade_tenant ON llm_cascade_outcomes (tenant_id, created_at DESC);
