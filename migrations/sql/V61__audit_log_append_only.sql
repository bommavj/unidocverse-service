-- V61: The audit log is append-only.
-- Rows can't be edited or deleted through the application. The only exception is
-- removing a whole organisation (tenant cascade), which sets app.audit_purge = 'on'.

CREATE TABLE IF NOT EXISTS audit_log (
    id TEXT PRIMARY KEY, doc_id TEXT, action TEXT NOT NULL, actor TEXT DEFAULT 'system',
    detail TEXT, ip TEXT, extra JSONB DEFAULT '{}', created_at TIMESTAMP DEFAULT NOW(), tenant_id UUID
);

CREATE INDEX IF NOT EXISTS idx_audit_log_tenant_created ON audit_log (tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_log_doc ON audit_log (doc_id) WHERE doc_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_audit_log_action ON audit_log (tenant_id, action);

CREATE OR REPLACE FUNCTION udv_audit_log_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' AND current_setting('app.audit_purge', true) = 'on' THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'audit_log is append-only (% blocked)', TG_OP;
END $$;

DROP TRIGGER IF EXISTS trg_audit_log_immutable ON audit_log;
CREATE TRIGGER trg_audit_log_immutable
    BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION udv_audit_log_immutable();
