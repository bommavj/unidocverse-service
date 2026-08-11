ALTER TABLE licenses ADD COLUMN IF NOT EXISTS license_type VARCHAR(20) DEFAULT 'full';
-- 'trial' or 'full'