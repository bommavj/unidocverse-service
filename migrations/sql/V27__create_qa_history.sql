-- V30: Q&A history per document and global sessions
CREATE TABLE IF NOT EXISTS document_qa_history (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id  VARCHAR     REFERENCES documents(id) ON DELETE CASCADE,
    question     TEXT        NOT NULL,
    answer       TEXT        NOT NULL,
    sources      JSONB       DEFAULT '[]',
    doc_type     VARCHAR(80),
    lang         VARCHAR(10) DEFAULT 'en',
    confidence   FLOAT,
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_qa_history_document_id ON document_qa_history(document_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_qa_history_global      ON document_qa_history(created_at DESC) WHERE document_id IS NULL;
