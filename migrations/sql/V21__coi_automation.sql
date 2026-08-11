-- V24__coi_automation.sql
-- Certificate of Insurance (COI) automation

CREATE TABLE IF NOT EXISTS coi_requests (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id           UUID        NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    requested_by        VARCHAR(100),
    requested_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    holder_name         VARCHAR(200) NOT NULL,
    holder_address      TEXT,
    holder_email        VARCHAR(255),
    purpose             TEXT,
    policy_ids          UUID[],
    additional_insured  VARCHAR(200),
    status              VARCHAR(20) NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending','generating','completed','failed','expired')),
    notes               TEXT,
    completed_at        TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS coi_certificates (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id          UUID        NOT NULL REFERENCES coi_requests(id) ON DELETE CASCADE,
    client_id           UUID        NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    certificate_number  VARCHAR(50) UNIQUE,
    certificate_data    JSONB NOT NULL DEFAULT '{}',
    pdf_path            VARCHAR(500),
    issued_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    valid_until         DATE,
    -- ✅ NOT a generated column — computed by application or trigger instead
    is_expired          BOOLEAN NOT NULL DEFAULT FALSE,
    issued_by           VARCHAR(100),
    emailed_to          VARCHAR(255),
    emailed_at          TIMESTAMPTZ
);

-- Auto-generate certificate number
CREATE SEQUENCE IF NOT EXISTS coi_cert_number_seq START 1;

CREATE OR REPLACE FUNCTION generate_coi_number()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.certificate_number IS NULL OR NEW.certificate_number = '' THEN
        NEW.certificate_number := 'COI-' || TO_CHAR(NOW(), 'YYYY') || '-'
                                  || LPAD(nextval('coi_cert_number_seq')::TEXT, 5, '0');
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_coi_certificate_number
    BEFORE INSERT ON coi_certificates
    FOR EACH ROW EXECUTE FUNCTION generate_coi_number();

-- Trigger to auto-update is_expired on UPDATE
CREATE OR REPLACE FUNCTION update_coi_expired()
RETURNS TRIGGER AS $$
BEGIN
    NEW.is_expired := (NEW.valid_until IS NOT NULL AND NEW.valid_until < CURRENT_DATE);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_coi_update_expired
    BEFORE INSERT OR UPDATE ON coi_certificates
    FOR EACH ROW EXECUTE FUNCTION update_coi_expired();

CREATE INDEX IF NOT EXISTS idx_coi_requests_client  ON coi_requests(client_id);
CREATE INDEX IF NOT EXISTS idx_coi_requests_status  ON coi_requests(status);
CREATE INDEX IF NOT EXISTS idx_coi_certs_client     ON coi_certificates(client_id);
CREATE INDEX IF NOT EXISTS idx_coi_certs_request    ON coi_certificates(request_id);
CREATE INDEX IF NOT EXISTS idx_coi_certs_number     ON coi_certificates(certificate_number);
CREATE INDEX IF NOT EXISTS idx_coi_certs_expired    ON coi_certificates(is_expired);

COMMENT ON TABLE coi_certificates IS 'Generated Certificates of Insurance (ACORD 25 format).';