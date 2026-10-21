-- V65: Fingerprint of each uploaded file (SHA-256 of its bytes), to catch duplicate uploads.
ALTER TABLE documents ADD COLUMN IF NOT EXISTS content_hash TEXT;
CREATE INDEX IF NOT EXISTS idx_documents_tenant_hash ON documents (tenant_id, content_hash) WHERE content_hash IS NOT NULL;
