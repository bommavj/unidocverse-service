-- V49: Add composite index on (doc_type, status) for _filter_docs_by_plan SQL-side domain filter.
-- Without this, filtering 1M documents by doc_type requires a full sequential scan.
-- Also index (status, tenant_id) for tenant-isolated queries.

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_documents_doc_type_status
    ON documents (doc_type, status)
    WHERE status = 'completed';

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_documents_status_tenant
    ON documents (status, tenant_id)
    WHERE status = 'completed';

-- Partial index on doc_metadata for ocr_quality flag lookups
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_documents_ocr_quality
    ON documents ((doc_metadata->>'ocr_quality'))
    WHERE doc_metadata IS NOT NULL AND doc_metadata->>'ocr_quality' IS NOT NULL;
