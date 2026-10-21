-- V62: Audit trail retention.
--
--  • audit_log becomes partitioned by month (created_at), so old months can be
--    removed in one step instead of row-by-row deletes, and queries over recent
--    activity only touch recent partitions.
--  • audit_retention holds each organisation's policy: how long to keep reads
--    (views, searches, AI questions) and changes / security events, plus a legal hold.
--  • The nightly job (app/services/audit_retention.py) archives expired rows to
--    compressed files before deleting them, using the app.audit_purge exception of
--    the append-only trigger.

-- ── 1. Partitioned audit_log (data preserved) ────────────────────────────────
CREATE OR REPLACE FUNCTION udv_audit_ensure_partitions(from_month date, months_ahead int DEFAULT 3)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE
    m date := date_trunc('month', from_month)::date;
    last date := (date_trunc('month', now()) + make_interval(months => months_ahead))::date;
    name text;
BEGIN
    WHILE m <= last LOOP
        name := format('audit_log_y%sm%s', to_char(m, 'YYYY'), to_char(m, 'MM'));
        IF to_regclass('public.' || name) IS NULL THEN
            EXECUTE format('CREATE TABLE %I PARTITION OF audit_log FOR VALUES FROM (%L) TO (%L)',
                           name, m, (m + interval '1 month')::date);
        END IF;
        m := (m + interval '1 month')::date;
    END LOOP;
END $$;

DO $$
DECLARE
    oldest date;
BEGIN
    IF (SELECT c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relname = 'audit_log' AND n.nspname = 'public') = 'r' THEN
        ALTER TABLE audit_log RENAME TO audit_log_unpartitioned;
        SELECT coalesce(min(created_at), now())::date INTO oldest FROM audit_log_unpartitioned;
    ELSE
        oldest := now()::date;
    END IF;

    IF to_regclass('public.audit_log') IS NULL THEN
        CREATE TABLE audit_log (
            id         TEXT      NOT NULL,
            doc_id     TEXT,
            action     TEXT      NOT NULL,
            actor      TEXT      DEFAULT 'system',
            detail     TEXT,
            ip         TEXT,
            extra      JSONB     DEFAULT '{}'::jsonb,
            created_at TIMESTAMP NOT NULL DEFAULT now(),
            tenant_id  UUID      NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            CONSTRAINT audit_log_part_pkey PRIMARY KEY (id, created_at)
        ) PARTITION BY RANGE (created_at);
        CREATE TABLE IF NOT EXISTS audit_log_default PARTITION OF audit_log DEFAULT;
        PERFORM udv_audit_ensure_partitions(oldest, 3);
    END IF;

    IF to_regclass('public.audit_log_unpartitioned') IS NOT NULL THEN
        INSERT INTO audit_log (id, doc_id, action, actor, detail, ip, extra, created_at, tenant_id)
        SELECT id, doc_id, action, actor, detail, ip, coalesce(extra, '{}'::jsonb), coalesce(created_at, now()), tenant_id
        FROM audit_log_unpartitioned;
        DROP TABLE audit_log_unpartitioned;      -- a table drop, not row deletes: the trigger doesn't fire
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_audit_log_p_tenant_created ON audit_log (tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_log_p_doc ON audit_log (doc_id) WHERE doc_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_audit_log_p_action ON audit_log (tenant_id, action);

-- Append-only, as in V61 (row triggers on a partitioned table apply to every partition)
DROP TRIGGER IF EXISTS trg_audit_log_immutable ON audit_log;
CREATE TRIGGER trg_audit_log_immutable
    BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION udv_audit_log_immutable();

-- ── 2. Retention policy per organisation ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS audit_retention (
    tenant_id       TEXT        PRIMARY KEY,
    read_days       INTEGER     NOT NULL DEFAULT 90,      -- views, searches, AI questions
    change_days     INTEGER     NOT NULL DEFAULT 2555,    -- changes and security events; 0 = keep forever
    legal_hold      BOOLEAN     NOT NULL DEFAULT FALSE,   -- pauses all cleanup
    last_run_at     TIMESTAMPTZ,
    last_run_result JSONB,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by      TEXT
);
