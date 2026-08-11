-- V28: Add all domain feature flag columns to settings
ALTER TABLE settings
    ADD COLUMN IF NOT EXISTS legal_mode        BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS medical_mode      BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS finance_mode      BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS real_estate_mode  BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS hr_mode           BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS supply_chain_mode BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN settings.legal_mode        IS 'Enables Legal & Compliance Mode';
COMMENT ON COLUMN settings.medical_mode      IS 'Enables Healthcare & Medical Admin Mode';
COMMENT ON COLUMN settings.finance_mode      IS 'Enables Finance, Tax & Auditing Mode';
COMMENT ON COLUMN settings.real_estate_mode  IS 'Enables Real Estate Mode';
COMMENT ON COLUMN settings.hr_mode           IS 'Enables HR & People Mode';
COMMENT ON COLUMN settings.supply_chain_mode IS 'Enables Supply Chain Mode';
