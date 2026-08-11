-- V1: Initial UniDocVerse AI Schema
-- Description: Creates documents table and core indexes

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Documents table
CREATE TABLE documents (
    id VARCHAR(36) PRIMARY KEY DEFAULT uuid_generate_v4()::text,
    filename VARCHAR(500) NOT NULL,
    file_path TEXT NOT NULL,
    file_size BIGINT,
    mime_type VARCHAR(100),

    -- Status tracking
    status VARCHAR(50) DEFAULT 'pending',

    -- Content
    raw_text TEXT,
    summary TEXT,
    doc_type VARCHAR(100),

    -- Metadata (JSONB for flexible structure)
    doc_metadata JSONB DEFAULT '{}'::jsonb,

    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    processed_at TIMESTAMP,

    -- Indexes
    CONSTRAINT documents_status_check CHECK (status IN ('pending', 'processing', 'completed', 'failed'))
);

-- Indexes for performance
CREATE INDEX idx_documents_status ON documents(status);
CREATE INDEX idx_documents_created_at ON documents(created_at DESC);
CREATE INDEX idx_documents_doc_type ON documents(doc_type);
CREATE INDEX idx_documents_metadata ON documents USING GIN (doc_metadata);

-- Full text search index
CREATE INDEX idx_documents_text_search ON documents USING GIN (to_tsvector('english', raw_text));

-- Comments
COMMENT ON TABLE documents IS 'Stores uploaded documents and their metadata';
COMMENT ON COLUMN documents.status IS 'Processing status: pending, processing, completed, failed';
COMMENT ON COLUMN documents.doc_metadata IS 'Flexible JSON metadata for AI analysis results';