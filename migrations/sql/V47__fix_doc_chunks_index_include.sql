-- Fix idx_doc_chunks_doc_status: INCLUDE (chunk_text) causes index row size to
-- exceed PostgreSQL B-tree maximum (2704 bytes) for large chunks. Recreate
-- the index without the INCLUDE clause — document_id alone is sufficient
-- for the query patterns this index supports.
DROP INDEX IF EXISTS idx_doc_chunks_doc_status;

CREATE INDEX IF NOT EXISTS idx_doc_chunks_doc_status
    ON document_chunks (document_id);
