-- V51: Full-text (keyword) index on chunk text for hybrid search.
--
-- Vector search alone misses exact terms — ticker symbols (LXRX), parcel and
-- form numbers, "Total revenues" — that keyword search finds directly. Hybrid
-- search ranks candidates from both and fuses them (RRF) before reranking.
-- The expression must match hybrid_search.py exactly for the index to be used.

CREATE INDEX IF NOT EXISTS idx_document_chunks_fts
    ON document_chunks
    USING gin (to_tsvector('english', coalesce(chunk_text, '')));
