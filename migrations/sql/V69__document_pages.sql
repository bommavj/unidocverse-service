-- V69: Page-accurate storage. Every page of a document is stored on its own with its real
-- page number and how it was read (text layer or OCR); chunks reference the real page.
-- Filled by app/services/page_ingest.py.
CREATE TABLE IF NOT EXISTS document_pages (
    document_id UUID        NOT NULL,
    page_number INTEGER     NOT NULL,
    tenant_id   UUID,
    text        TEXT        NOT NULL DEFAULT '',
    method      TEXT        NOT NULL DEFAULT 'text',   -- text | ocr | text+ocr | none
    char_count  INTEGER     NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (document_id, page_number)
);
CREATE INDEX IF NOT EXISTS idx_document_pages_tenant ON document_pages (tenant_id);
