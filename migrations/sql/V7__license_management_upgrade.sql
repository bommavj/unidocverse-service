-- 1. Add version column
ALTER TABLE licenses
ADD COLUMN IF NOT EXISTS version INTEGER DEFAULT 1;

-- 2. Add device_id column
ALTER TABLE licenses
ADD COLUMN IF NOT EXISTS device_id TEXT;

-- 3. Drop UNIQUE constraint on license_key
ALTER TABLE licenses
DROP CONSTRAINT IF EXISTS licenses_license_key_key;

-- 4. Add new indexes
CREATE INDEX IF NOT EXISTS idx_licenses_key_version
    ON licenses (license_key, version DESC);

CREATE INDEX IF NOT EXISTS idx_licenses_device
    ON licenses (device_id);

-- 5. Update get_active_license() to return latest version
DROP FUNCTION IF EXISTS get_active_license();

CREATE OR REPLACE FUNCTION get_active_license()
RETURNS TABLE (
    id INTEGER,
    organization VARCHAR,
    email VARCHAR,
    license_key TEXT,
    expiry TIMESTAMP,
    features JSONB,
    version INTEGER
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        l.id,
        l.organization,
        l.email,
        l.license_key,
        l.expiry,
        l.features,
        l.version
    FROM licenses l
    WHERE l.is_active = TRUE
      AND (l.expiry IS NULL OR l.expiry > NOW())
    ORDER BY l.version DESC, l.activated_at DESC
    LIMIT 1;
END;
$$ LANGUAGE plpgsql;
