-- ── V44: HNSW index + job queue ───────────────────────────────────────────────
-- 1. Replace IVFFlat with HNSW on document_chunks.embedding
--    IVFFlat: fast only after 10k+ rows, needs VACUUM to update
--    HNSW:    fast at ANY row count, handles inserts/deletes cleanly,
--             scales to 10M+ records with ~150ms query time
-- 2. Add job_queue table for async document processing
--    Eliminates upload timeouts, enables retry on failure

-- ── 1. HNSW index ─────────────────────────────────────────────────────────────
DROP INDEX IF EXISTS idx_document_chunks_embedding;

CREATE INDEX IF NOT EXISTS idx_document_chunks_embedding_hnsw
    ON document_chunks
    USING hnsw (embedding vector_cosine_ops)
    WITH (
        m = 16,               -- connections per node (16 = good balance speed/memory)
        ef_construction = 64  -- build quality (64 = fast build, still accurate)
    );

-- ef_search controls query accuracy at runtime — set via:
-- SET hnsw.ef_search = 100;  (higher = more accurate, slower)

-- ── 2. Supporting indexes for common query patterns ───────────────────────────
CREATE INDEX IF NOT EXISTS idx_doc_chunks_doc_status
    ON document_chunks (document_id)
    INCLUDE (chunk_text, page_number);

CREATE INDEX IF NOT EXISTS idx_documents_status
    ON documents (status)
    WHERE status != 'completed';

-- ── 3. Job queue table ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS job_queue (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_type        VARCHAR(64)  NOT NULL,           -- 'process_document', 'reembed', etc.
    payload         JSONB        NOT NULL DEFAULT '{}',
    status          VARCHAR(16)  NOT NULL DEFAULT 'pending',
                                                      -- pending | running | completed | failed
    priority        INTEGER      NOT NULL DEFAULT 5,  -- 1=highest, 10=lowest
    attempts        INTEGER      NOT NULL DEFAULT 0,
    max_attempts    INTEGER      NOT NULL DEFAULT 3,
    error_message   TEXT,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ,
    next_retry_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Worker polls: WHERE status IN ('pending','failed') AND next_retry_at <= NOW()
-- ORDER BY priority ASC, created_at ASC
-- FOR UPDATE SKIP LOCKED   ← critical: prevents two workers grabbing same job
CREATE INDEX IF NOT EXISTS idx_job_queue_worker
    ON job_queue (priority, created_at)
    WHERE status IN ('pending', 'failed') ;

CREATE INDEX IF NOT EXISTS idx_job_queue_status
    ON job_queue (status, created_at DESC);

-- ── 4. Document state machine ─────────────────────────────────────────────────
-- Extend documents.status to track exact processing stage
-- Allows resume from failure point instead of restarting from scratch
ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS processing_stage VARCHAR(32) DEFAULT NULL;
    -- NULL=not started | parsing | chunking | embedding | done | error

COMMENT ON COLUMN documents.processing_stage IS
    'Tracks exact stage of document processing pipeline for retry/resume on failure';
