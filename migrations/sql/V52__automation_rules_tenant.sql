-- Insurance automation rules belong to a tenant. Existing rules go to the default
-- tenant; built-in templates stay in code and are copied per tenant when seeded.
-- The table is also created at runtime (ensure_tables), so guard on its existence.
DO $$
BEGIN
  IF to_regclass('public.insurance_automation_rules') IS NOT NULL THEN
    ALTER TABLE insurance_automation_rules ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id);
    UPDATE insurance_automation_rules SET tenant_id = '00000000-0000-0000-0000-000000000001' WHERE tenant_id IS NULL;
    ALTER TABLE insurance_automation_rules ALTER COLUMN tenant_id SET NOT NULL;
    CREATE INDEX IF NOT EXISTS idx_automation_rules_tenant ON insurance_automation_rules (tenant_id, enabled);
    CREATE UNIQUE INDEX IF NOT EXISTS uq_automation_rules_tenant_name ON insurance_automation_rules (tenant_id, name);
  END IF;
END $$;
