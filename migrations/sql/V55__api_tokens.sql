-- API tokens: long-lived credentials for programs (scripts, integrations, the MCP bridge).
-- Only a SHA-256 hash of the token is stored; the raw value is shown once at creation.
CREATE TABLE IF NOT EXISTS api_tokens (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     UUID NOT NULL,
    user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    prefix        TEXT NOT NULL,                       -- first characters, shown in lists to tell tokens apart
    token_hash    TEXT NOT NULL UNIQUE,
    scope         TEXT NOT NULL DEFAULT 'read' CHECK (scope IN ('read', 'write')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at    TIMESTAMPTZ,
    last_used_at  TIMESTAMPTZ,
    revoked_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_api_tokens_tenant ON api_tokens (tenant_id);
CREATE INDEX IF NOT EXISTS idx_api_tokens_user ON api_tokens (user_id);
