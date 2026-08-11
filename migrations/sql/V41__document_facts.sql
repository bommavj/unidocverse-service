ALTER TABLE documents ADD COLUMN IF NOT EXISTS extracted_facts JSONB;

CREATE INDEX IF NOT EXISTS idx_documents_extracted_facts
ON documents USING GIN(extracted_facts);
