CREATE TABLE IF NOT EXISTS research_sessions (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    question    TEXT        NOT NULL,
    answer      TEXT        NOT NULL,
    sources     TEXT[]      NOT NULL DEFAULT '{}',
    steps       JSONB       NOT NULL DEFAULT '[]',
    iterations  INTEGER     NOT NULL DEFAULT 0,
    doc_ids     TEXT[]      NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_research_sessions_created_at
    ON research_sessions (created_at DESC);
