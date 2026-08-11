-- Add chart_data and matched_doc_ids to research_sessions so analytics
-- can retrieve the correct chart for any historical session from the DB.
ALTER TABLE research_sessions
    ADD COLUMN IF NOT EXISTS chart_data     JSONB    DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS matched_doc_ids TEXT[]  NOT NULL DEFAULT '{}';
