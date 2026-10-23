-- V73: Enforce organisation isolation in Postgres.
-- The service connects as the owner of these tables, and Postgres skips row-level security for a
-- table's owner unless the table is FORCE'd — so the policies from earlier migrations never fired
-- and every query without its own tenant filter returned all organisations' rows.
--
-- The app sets app.current_tenant_id on each connection to the signed-in user's organisation
-- (app/core/database.py). When it is unset or '' (background jobs, public token links) the
-- policies allow every row, so those paths behave as before and must filter themselves.
-- Inserts are not checked: rows written without a tenant_id must not start failing.

CREATE OR REPLACE FUNCTION udv_unscoped() RETURNS boolean LANGUAGE sql STABLE AS
$$ SELECT COALESCE(current_setting('app.current_tenant_id', true), '') = '' $$;

CREATE OR REPLACE FUNCTION udv_tenant_ok(t text) RETURNS boolean LANGUAGE sql STABLE AS
$$ SELECT udv_unscoped() OR t = current_setting('app.current_tenant_id', true) $$;

-- Replace every policy on the table with read/update/delete policies on `expr`, and force RLS.
CREATE OR REPLACE FUNCTION udv_apply_tenant_policy(tbl text, expr text) RETURNS void LANGUAGE plpgsql AS $f$
DECLARE pol record;
BEGIN
    FOR pol IN SELECT policyname FROM pg_policies WHERE schemaname = 'public' AND tablename = tbl LOOP
        EXECUTE format('DROP POLICY %I ON %I', pol.policyname, tbl);
    END LOOP;
    EXECUTE format('CREATE POLICY udv_tenant_select ON %I FOR SELECT USING (%s)', tbl, expr);
    EXECUTE format('CREATE POLICY udv_tenant_update ON %I FOR UPDATE USING (%s)', tbl, expr);
    EXECUTE format('CREATE POLICY udv_tenant_delete ON %I FOR DELETE USING (%s)', tbl, expr);
    EXECUTE format('CREATE POLICY udv_tenant_insert ON %I FOR INSERT WITH CHECK (true)', tbl);
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', tbl);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', tbl);
END $f$;

DO $$
DECLARE
    tbl text;
    -- Tables that carry tenant_id themselves.
    owned text[] := ARRAY[
        'documents', 'document_chunks', 'document_pages', 'clients', 'policies', 'quotes', 'claims',
        'matters', 'research_sessions', 'chatbot_sessions', 'settings', 'approval_requests',
        'agent_runs', 'audit_log', 'notifications', 'morning_briefs', 'pilot_reports',
        'insurance_automation_rules', 'imm_cases', 'imm_case_tasks', 'imm_intakes', 'imm_leads',
        'imm_signatures', 'llm_cascade_outcomes', 'llm_cloud_calls', 'webhook_endpoints',
        'webhook_deliveries', 'qb_connections', 'qb_pushes', 'sf_connections', 'sf_pushes'];
    -- Tables without tenant_id: visible when their parent row is.
    -- Parent ids are compared as text because the id types differ (uuid / varchar / text).
    children text[][] := ARRAY[
        ['activity_log',           'document_id::text IN (SELECT id::text FROM documents)'],
        ['commission_statements',  'document_id::text IN (SELECT id::text FROM documents)'],
        ['document_qa_history',    'document_id::text IN (SELECT id::text FROM documents)'],
        ['finance_anomalies',      'document_id::text IN (SELECT id::text FROM documents)'],
        ['finance_matches',        'document_id::text IN (SELECT id::text FROM documents)'],
        ['hr_candidates',          'document_id::text IN (SELECT id::text FROM documents)'],
        ['hr_onboarding_tasks',    'document_id::text IN (SELECT id::text FROM documents)'],
        ['legal_clauses',          'document_id::text IN (SELECT id::text FROM documents)'],
        ['legal_contracts',        'document_id::text IN (SELECT id::text FROM documents)'],
        ['patient_biomarkers',     'document_id::text IN (SELECT id::text FROM documents)'],
        ['patient_charts',         'document_id::text IN (SELECT id::text FROM documents)'],
        ['real_estate_properties', 'document_id::text IN (SELECT id::text FROM documents)'],
        ['supply_chain_shipments', 'document_id::text IN (SELECT id::text FROM documents)'],
        ['supply_chain_suppliers', 'document_id::text IN (SELECT id::text FROM documents)'],
        ['doc_risk',               'doc_id::text IN (SELECT id::text FROM documents)'],
        ['clm_entries',            'doc_id::text IN (SELECT id::text FROM documents)'],
        ['clm_history',            'doc_id::text IN (SELECT id::text FROM documents)'],
        ['client_immigration',     'client_id::text IN (SELECT id::text FROM clients)'],
        ['client_notes',           'client_id::text IN (SELECT id::text FROM clients)'],
        ['coi_certificates',       'client_id::text IN (SELECT id::text FROM clients)'],
        ['coi_requests',           'client_id::text IN (SELECT id::text FROM clients)'],
        ['gap_analyses',           'client_id::text IN (SELECT id::text FROM clients)'],
        ['gap_recommendations',    'client_id::text IN (SELECT id::text FROM clients)'],
        ['renewal_activities',     'policy_id::text IN (SELECT id::text FROM policies) OR client_id::text IN (SELECT id::text FROM clients)'],
        ['commission_line_items',  'policy_id::text IN (SELECT id::text FROM policies)'],
        ['insurance_notifications','policy_id::text IN (SELECT id::text FROM policies) OR quote_id::text IN (SELECT id::text FROM quotes)'],
        ['loss_runs',              'document_id::text IN (SELECT id::text FROM documents) OR client_id::text IN (SELECT id::text FROM clients)'],
        ['loss_run_claims',        'loss_run_id::text IN (SELECT id::text FROM loss_runs)'],
        ['real_estate_milestones', 'property_id::text IN (SELECT id::text FROM real_estate_properties)'],
        ['agent_reports',          'run_id::text IN (SELECT id::text FROM agent_runs)'],
        ['agent_tool_calls',       'run_id::text IN (SELECT id::text FROM agent_runs)'],
        ['claim_docs',             'claim_id::text IN (SELECT id::text FROM claims)'],
        ['claim_timeline',         'claim_id::text IN (SELECT id::text FROM claims)'],
        ['imm_case_checklist',     'case_id::text IN (SELECT id::text FROM imm_cases)'],
        ['imm_case_events',        'case_id::text IN (SELECT id::text FROM imm_cases)'],
        ['matter_docs',            'matter_id::text IN (SELECT id::text FROM matters)'],
        ['matter_notes',           'matter_id::text IN (SELECT id::text FROM matters)'],
        ['quote_activity',         'quote_id::text IN (SELECT id::text FROM quotes)'],
        ['quote_carriers',         'quote_id::text IN (SELECT id::text FROM quotes)']];
    i int;
BEGIN
    FOREACH tbl IN ARRAY owned LOOP
        CONTINUE WHEN to_regclass('public.' || tbl) IS NULL;
        PERFORM udv_apply_tenant_policy(tbl, 'udv_tenant_ok(tenant_id::text)');
    END LOOP;
    FOR i IN 1 .. array_length(children, 1) LOOP
        CONTINUE WHEN to_regclass('public.' || children[i][1]) IS NULL;
        PERFORM udv_apply_tenant_policy(children[i][1], 'udv_unscoped() OR ' || children[i][2]);
    END LOOP;
END $$;

-- The service may connect as a superuser (the Docker image does), and superusers skip RLS even on
-- FORCE'd tables. Scoped connections therefore SET ROLE to this role: not a superuser, but a member
-- of every table owner, so it can still read, write and run the app's CREATE/ALTER IF NOT EXISTS.
DO $$
DECLARE o text;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'udv_tenant_scope') THEN
        CREATE ROLE udv_tenant_scope NOLOGIN NOSUPERUSER NOBYPASSRLS INHERIT;
    END IF;
    FOR o IN SELECT DISTINCT tableowner FROM pg_tables WHERE schemaname = 'public' LOOP
        EXECUTE format('GRANT %I TO udv_tenant_scope', o);
    END LOOP;
    GRANT USAGE, CREATE ON SCHEMA public TO udv_tenant_scope;
END $$;
