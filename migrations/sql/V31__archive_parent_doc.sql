-- V31: Support archive extraction — parent_doc_id links child documents to their ZIP/RAR/7z parent.
-- doc_type 'archive' is also used to mark the parent container record.

ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS parent_doc_id VARCHAR
        REFERENCES documents(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_documents_parent_doc_id ON documents(parent_doc_id);

-- Track how many children have been extracted from an archive
ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS child_count INTEGER DEFAULT 0;

-- Allow 'extracted' status for archive parent documents
ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_status_check;
ALTER TABLE documents ADD CONSTRAINT documents_status_check CHECK (
    status IN ('pending','processing','completed','failed','needs_review','extracted')
);
