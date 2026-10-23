-- V71: Web search for the Research Agent through the organisation's own SearxNG.
-- Off by default. Every search is audited as action 'research.web_search'.
CREATE TABLE IF NOT EXISTS tenant_web_search (
    tenant_id   TEXT        PRIMARY KEY,
    enabled     BOOLEAN     NOT NULL DEFAULT FALSE,
    searxng_url TEXT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by  TEXT
);
