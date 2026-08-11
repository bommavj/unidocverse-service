-- License Management Schema
-- Add this to your migrations/sql/ folder as V3__license_management.sql

-- License table
CREATE TABLE IF NOT EXISTS licenses (
    id SERIAL PRIMARY KEY,
    organization VARCHAR(255) NOT NULL,
    email VARCHAR(255) NOT NULL,
    license_key TEXT NOT NULL UNIQUE,
    signature TEXT NOT NULL,
    expiry TIMESTAMP,
    features JSONB DEFAULT '{}',
    is_active BOOLEAN DEFAULT TRUE,
    activated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Index for fast license lookups
CREATE INDEX IF NOT EXISTS idx_licenses_active ON licenses(is_active, expiry);
CREATE INDEX IF NOT EXISTS idx_licenses_key ON licenses(license_key);

-- License requests table (optional - for users requesting licenses)
CREATE TABLE IF NOT EXISTS license_requests (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    email VARCHAR(255) NOT NULL,
    organization VARCHAR(255) NOT NULL,
    use_case TEXT,
    status VARCHAR(50) DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Function to get active license
CREATE OR REPLACE FUNCTION get_active_license()
RETURNS TABLE (
    id INTEGER,
    organization VARCHAR,
    email VARCHAR,
    license_key TEXT,
    expiry TIMESTAMP,
    features JSONB
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        l.id,
        l.organization,
        l.email,
        l.license_key,
        l.expiry,
        l.features
    FROM licenses l
    WHERE l.is_active = TRUE
      AND (l.expiry IS NULL OR l.expiry > NOW())
    ORDER BY l.activated_at DESC
    LIMIT 1;
END;
$$ LANGUAGE plpgsql;
