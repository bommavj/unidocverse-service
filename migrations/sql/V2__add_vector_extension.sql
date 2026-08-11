-- V3: Add Vector Embedding Column
-- Description: Add embedding column to documents table
CREATE EXTENSION IF NOT EXISTS vector;

-- Add embedding column to documents (768 dimensions for all-mpnet-base-v2 model)
ALTER TABLE documents
ADD COLUMN IF NOT EXISTS embedding vector(768);

-- Create index for vector similarity search (HNSW for speed)
CREATE INDEX IF NOT EXISTS idx_documents_embedding ON documents
USING hnsw (embedding vector_cosine_ops);

-- Comments
COMMENT ON COLUMN documents.embedding IS 'Document embedding vector for semantic search (768-dim, all-mpnet-base-v2)';