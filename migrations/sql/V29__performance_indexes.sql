-- V29: Performance indexes — upgrade IVFFlat → HNSW on document_chunks,
--      add composite indexes for common query patterns.
-- Each index is created in its own guarded block: on schemas where a column
-- does not exist the index is skipped instead of aborting the whole migration.
-- Not CONCURRENTLY: Flyway cannot run that inside its migration connection.

DROP INDEX IF EXISTS idx_document_chunks_embedding;

DO $$
DECLARE
    stmt TEXT;
BEGIN
    FOREACH stmt IN ARRAY ARRAY[
        -- 1. HNSW: faster approximate search, no need to tune list count with data size
        'CREATE INDEX IF NOT EXISTS idx_document_chunks_embedding_hnsw
            ON document_chunks USING hnsw (embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 64)',
        -- 2. Chunk retrieval by document (no INCLUDE: text + embedding exceed the btree row limit)
        'CREATE INDEX IF NOT EXISTS idx_document_chunks_doc_chunk
            ON document_chunks (document_id, chunk_index)',
        -- 3. Documents: composite index for the most common list + filter query
        'CREATE INDEX IF NOT EXISTS idx_documents_type_created
            ON documents (doc_type, created_at DESC)
            WHERE doc_type IS NOT NULL',
        -- 4. Documents: full-text search on filename + text
        'CREATE INDEX IF NOT EXISTS idx_documents_fts
            ON documents USING gin (to_tsvector(''english'', coalesce(filename, '''') || '' '' || coalesce(raw_text, '''')))',
        -- 5. Agent runs: status + sector filter (Agent Queue dashboard)
        'CREATE INDEX IF NOT EXISTS idx_agent_runs_sector_status
            ON agent_runs (sector, status, created_at DESC)',
        -- 6. Approval requests: pending queue lookup
        'CREATE INDEX IF NOT EXISTS idx_approval_requests_status_created
            ON approval_requests (status, created_at DESC)
            WHERE status = ''pending'''
    ] LOOP
        BEGIN
            EXECUTE stmt;
        EXCEPTION WHEN OTHERS THEN
            RAISE NOTICE 'V29 skipped index: %', SQLERRM;
        END;
    END LOOP;
END $$;

ANALYZE documents;
ANALYZE document_chunks;
