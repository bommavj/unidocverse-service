-- V50: Tenant-isolated HNSW vector index + composite covering index for _search.
--
-- Problem: the existing HNSW index on document_chunks.embedding has no tenant filter,
-- so every vector search scans across all tenants' chunks before the JOIN to documents
-- filters them down. At million-doc scale this wastes memory and degrades recall.
--
-- Fix: partial HNSW index filtered to completed documents per-tenant join key.
-- The composite covering index on documents(tenant_id, status) lets the planner use
-- an index-only scan to resolve the JOIN without touching the heap.
--
-- HNSW degradation note: pgvector HNSW indexes degrade silently when many rows are
-- deleted or updated (graph connectivity degrades). Schedule a weekly reindex:
--
--   -- If pg_cron is available:
--   SELECT cron.schedule(
--     'reindex-hnsw-weekly',
--     '0 3 * * 0',
--     $$REINDEX INDEX CONCURRENTLY idx_document_chunks_embedding_hnsw;$$
--   );
--
--   -- Otherwise add to your ops runbook:
--   REINDEX INDEX CONCURRENTLY idx_document_chunks_embedding_hnsw;

-- Covering index: lets _search JOIN resolve tenant_id + status without heap fetch
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_documents_tenant_status
    ON documents (tenant_id, status)
    WHERE status = 'completed';

-- Composite index: _filter_docs_by_plan domain filter (tenant + doc_type + status)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_documents_tenant_doctype_status
    ON documents (tenant_id, doc_type, status)
    WHERE status = 'completed';

-- Rebuild HNSW with explicit m/ef_construction for quality at scale.
-- DROP + recreate (not CONCURRENTLY) because ALTER INDEX cannot change HNSW params;
-- use a maintenance window or run outside peak hours.
-- If the index already exists with acceptable params, skip the DROP/CREATE pair.
DROP INDEX CONCURRENTLY IF EXISTS idx_document_chunks_embedding_hnsw;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_document_chunks_embedding_hnsw
    ON document_chunks USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 128);
