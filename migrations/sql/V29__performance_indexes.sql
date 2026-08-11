-- V29: Performance indexes — upgrade IVFFlat → HNSW on document_chunks,
--      add composite indexes for common query patterns.

-- 1. Upgrade document_chunks embedding index from IVFFlat → HNSW
--    HNSW: faster approximate search, no need to tune list count with data size.
DROP INDEX IF EXISTS idx_document_chunks_embedding;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_document_chunks_embedding_hnsw
    ON document_chunks USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- 2. Covering index for chunk retrieval by document (eliminates table-fetch on joins)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_document_chunks_doc_chunk
    ON document_chunks (document_id, chunk_index)
    INCLUDE (content, embedding);

-- 3. Documents: composite index for the most common list + filter query
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_documents_type_created
    ON documents (doc_type, created_at DESC)
    WHERE doc_type IS NOT NULL;

-- 4. Documents: full-text search index on filename + content
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_documents_fts
    ON documents USING gin (to_tsvector('english', coalesce(filename, '') || ' ' || coalesce(content, '')));

-- 5. Agent runs: status + sector filter (used by Agent Queue dashboard)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_agent_runs_sector_status
    ON agent_runs (sector, status, created_at DESC);

-- 6. Approval requests: pending queue lookup
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_approval_requests_status_created
    ON approval_requests (status, created_at DESC)
    WHERE status = 'pending';

-- Run ANALYZE to refresh planner stats after bulk index creation
ANALYZE documents;
ANALYZE document_chunks;
