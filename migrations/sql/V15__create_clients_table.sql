-- V18__create_clients_table.sql
-- Creates the clients table for Insurance Agency Management.
-- Supports both personal (individual/family) and commercial (business) clients.

CREATE TABLE IF NOT EXISTS clients (

    -- ── Identity ─────────────────────────────────────────────────────────────
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    client_number   VARCHAR(20) UNIQUE NOT NULL,        -- e.g. CLI-0001, auto-generated

    -- ── Client Type ──────────────────────────────────────────────────────────
    client_type     VARCHAR(20) NOT NULL DEFAULT 'personal'
                        CHECK (client_type IN ('personal', 'commercial')),

    -- ── Personal Client Fields ────────────────────────────────────────────────
    first_name      VARCHAR(100),
    last_name       VARCHAR(100),
    date_of_birth   DATE,
    ssn_last4       VARCHAR(4),                         -- last 4 digits only, for reference

    -- ── Commercial Client Fields ──────────────────────────────────────────────
    business_name   VARCHAR(200),
    business_type   VARCHAR(100),                       -- LLC, Corp, Sole Proprietor, etc.
    ein_last4       VARCHAR(4),                         -- last 4 digits only

    -- ── Shared Contact ────────────────────────────────────────────────────────
    email           VARCHAR(255),
    phone           VARCHAR(30),
    mobile          VARCHAR(30),

    -- ── Address ───────────────────────────────────────────────────────────────
    address_line1   VARCHAR(255),
    address_line2   VARCHAR(100),
    city            VARCHAR(100),
    state           VARCHAR(50),
    zip             VARCHAR(20),
    country         VARCHAR(50)  NOT NULL DEFAULT 'US',

    -- ── Agency Metadata ───────────────────────────────────────────────────────
    assigned_agent  VARCHAR(100),                       -- agent name or username
    status          VARCHAR(20)  NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'inactive', 'prospect')),
    source          VARCHAR(20)  NOT NULL DEFAULT 'manual'
                        CHECK (source IN ('manual', 'imported', 'document_ai')),
    notes           TEXT,
    tags            TEXT[],                             -- e.g. {vip, commercial, referral}
    avatar_color    VARCHAR(10)  DEFAULT '#6366f1',     -- for UI avatar

    -- ── Audit ─────────────────────────────────────────────────────────────────
    created_by      VARCHAR(100),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ── Indexes ───────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_clients_client_type   ON clients (client_type);
CREATE INDEX IF NOT EXISTS idx_clients_status        ON clients (status);
CREATE INDEX IF NOT EXISTS idx_clients_last_name     ON clients (last_name);
CREATE INDEX IF NOT EXISTS idx_clients_business_name ON clients (business_name);
CREATE INDEX IF NOT EXISTS idx_clients_email         ON clients (email);
CREATE INDEX IF NOT EXISTS idx_clients_created_at    ON clients (created_at DESC);

-- ── Auto-generate client_number ───────────────────────────────────────────────
-- Format: CLI-0001, CLI-0002, etc.
CREATE SEQUENCE IF NOT EXISTS clients_number_seq START 1;

CREATE OR REPLACE FUNCTION generate_client_number()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.client_number IS NULL OR NEW.client_number = '' THEN
        NEW.client_number := 'CLI-' || LPAD(nextval('clients_number_seq')::TEXT, 4, '0');
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_clients_number
    BEFORE INSERT ON clients
    FOR EACH ROW EXECUTE FUNCTION generate_client_number();

-- ── Auto-update updated_at ────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION update_clients_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_clients_updated_at
    BEFORE UPDATE ON clients
    FOR EACH ROW EXECUTE FUNCTION update_clients_updated_at();

COMMENT ON TABLE clients IS
    'Insurance agency clients — personal (individuals/families) and commercial (businesses).';