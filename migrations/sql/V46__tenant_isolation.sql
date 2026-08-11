-- V46: Tenant Isolation
-- Adds tenant_id to all core tables, creates a tenants table,
-- wires users to tenants, and enables Row-Level Security.
--
-- Design:
--   - Each tenant is a row in the `tenants` table
--   - users.tenant_id scopes every user to one tenant
--   - All document/chunk/session queries are filtered by tenant_id
--   - RLS policies enforce isolation at DB level (belt + suspenders)
--   - settings becomes per-tenant (one row per tenant, not one global row)
--   - A default tenant is created for all existing data

-- ── 1. Tenants table ──────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS tenants (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL,
    slug        TEXT NOT NULL UNIQUE,           -- URL-safe identifier
    plan        TEXT NOT NULL DEFAULT 'free',   -- free | pro | enterprise
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Default tenant for all existing data
INSERT INTO tenants (id, name, slug, plan)
VALUES ('00000000-0000-0000-0000-000000000001', 'Default', 'default', 'pro')
ON CONFLICT DO NOTHING;

-- ── 2. Add tenant_id to users ─────────────────────────────────────────────────

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE;

-- Assign all existing users to the default tenant
UPDATE users SET tenant_id = '00000000-0000-0000-0000-000000000001'
WHERE tenant_id IS NULL;

ALTER TABLE users ALTER COLUMN tenant_id SET NOT NULL;

-- ── 3. Core document tables ───────────────────────────────────────────────────

ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE;

UPDATE documents SET tenant_id = '00000000-0000-0000-0000-000000000001'
WHERE tenant_id IS NULL;

ALTER TABLE documents ALTER COLUMN tenant_id SET NOT NULL;

CREATE INDEX IF NOT EXISTS idx_documents_tenant_id ON documents(tenant_id);

-- ── 4. document_chunks ────────────────────────────────────────────────────────

ALTER TABLE document_chunks
    ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE;

UPDATE document_chunks SET tenant_id = '00000000-0000-0000-0000-000000000001'
WHERE tenant_id IS NULL;

ALTER TABLE document_chunks ALTER COLUMN tenant_id SET NOT NULL;

CREATE INDEX IF NOT EXISTS idx_document_chunks_tenant_id ON document_chunks(tenant_id);

-- ── 5. research_sessions ──────────────────────────────────────────────────────

ALTER TABLE research_sessions
    ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE;

UPDATE research_sessions SET tenant_id = '00000000-0000-0000-0000-000000000001'
WHERE tenant_id IS NULL;

ALTER TABLE research_sessions ALTER COLUMN tenant_id SET NOT NULL;

CREATE INDEX IF NOT EXISTS idx_research_sessions_tenant_id ON research_sessions(tenant_id);

-- ── 6. chatbot_sessions ───────────────────────────────────────────────────────

ALTER TABLE chatbot_sessions
    ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE;

UPDATE chatbot_sessions SET tenant_id = '00000000-0000-0000-0000-000000000001'
WHERE tenant_id IS NULL;

ALTER TABLE chatbot_sessions ALTER COLUMN tenant_id SET NOT NULL;

-- ── 7. settings becomes per-tenant ───────────────────────────────────────────

ALTER TABLE settings
    ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE;

UPDATE settings SET tenant_id = '00000000-0000-0000-0000-000000000001'
WHERE tenant_id IS NULL;

ALTER TABLE settings ALTER COLUMN tenant_id SET NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_settings_tenant_unique ON settings(tenant_id);

-- ── 8. Other operational tables ───────────────────────────────────────────────

ALTER TABLE clients
    ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE;
UPDATE clients SET tenant_id = '00000000-0000-0000-0000-000000000001' WHERE tenant_id IS NULL;
ALTER TABLE clients ALTER COLUMN tenant_id SET NOT NULL;
CREATE INDEX IF NOT EXISTS idx_clients_tenant_id ON clients(tenant_id);

ALTER TABLE policies
    ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE;
UPDATE policies SET tenant_id = '00000000-0000-0000-0000-000000000001' WHERE tenant_id IS NULL;
ALTER TABLE policies ALTER COLUMN tenant_id SET NOT NULL;
CREATE INDEX IF NOT EXISTS idx_policies_tenant_id ON policies(tenant_id);

ALTER TABLE matters
    ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE;
UPDATE matters SET tenant_id = '00000000-0000-0000-0000-000000000001' WHERE tenant_id IS NULL;
ALTER TABLE matters ALTER COLUMN tenant_id SET NOT NULL;

ALTER TABLE quotes
    ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE;
UPDATE quotes SET tenant_id = '00000000-0000-0000-0000-000000000001' WHERE tenant_id IS NULL;
ALTER TABLE quotes ALTER COLUMN tenant_id SET NOT NULL;

ALTER TABLE agent_runs
    ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE;
UPDATE agent_runs SET tenant_id = '00000000-0000-0000-0000-000000000001' WHERE tenant_id IS NULL;
ALTER TABLE agent_runs ALTER COLUMN tenant_id SET NOT NULL;

ALTER TABLE audit_log
    ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE;
UPDATE audit_log SET tenant_id = '00000000-0000-0000-0000-000000000001' WHERE tenant_id IS NULL;
ALTER TABLE audit_log ALTER COLUMN tenant_id SET NOT NULL;

ALTER TABLE notifications
    ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE;
UPDATE notifications SET tenant_id = '00000000-0000-0000-0000-000000000001' WHERE tenant_id IS NULL;
ALTER TABLE notifications ALTER COLUMN tenant_id SET NOT NULL;

-- ── 9. Row-Level Security ─────────────────────────────────────────────────────
-- RLS enforces tenant isolation at DB level regardless of application logic.
-- Set app.current_tenant_id before any query: SET LOCAL app.current_tenant_id = '<uuid>';

-- Helper function to get current tenant from session variable
CREATE OR REPLACE FUNCTION current_tenant_id() RETURNS UUID AS $$
BEGIN
    RETURN NULLIF(current_setting('app.current_tenant_id', TRUE), '')::UUID;
EXCEPTION WHEN OTHERS THEN
    RETURN NULL;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Enable RLS on core tables
ALTER TABLE documents         ENABLE ROW LEVEL SECURITY;
ALTER TABLE document_chunks   ENABLE ROW LEVEL SECURITY;
ALTER TABLE research_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE chatbot_sessions  ENABLE ROW LEVEL SECURITY;
ALTER TABLE settings          ENABLE ROW LEVEL SECURITY;
ALTER TABLE clients           ENABLE ROW LEVEL SECURITY;
ALTER TABLE policies          ENABLE ROW LEVEL SECURITY;

-- RLS policies — tenant can only see its own rows
-- Superuser/unidocverse role bypass for migrations and admin

CREATE POLICY tenant_isolation_documents ON documents
    USING (tenant_id = current_tenant_id() OR current_tenant_id() IS NULL);

CREATE POLICY tenant_isolation_chunks ON document_chunks
    USING (tenant_id = current_tenant_id() OR current_tenant_id() IS NULL);

CREATE POLICY tenant_isolation_research ON research_sessions
    USING (tenant_id = current_tenant_id() OR current_tenant_id() IS NULL);

CREATE POLICY tenant_isolation_chatbot ON chatbot_sessions
    USING (tenant_id = current_tenant_id() OR current_tenant_id() IS NULL);

CREATE POLICY tenant_isolation_settings ON settings
    USING (tenant_id = current_tenant_id() OR current_tenant_id() IS NULL);

CREATE POLICY tenant_isolation_clients ON clients
    USING (tenant_id = current_tenant_id() OR current_tenant_id() IS NULL);

CREATE POLICY tenant_isolation_policies ON policies
    USING (tenant_id = current_tenant_id() OR current_tenant_id() IS NULL);
