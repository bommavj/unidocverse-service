ALTER TABLE research_sessions
    ADD COLUMN IF NOT EXISTS parent_session_id UUID REFERENCES research_sessions(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_research_sessions_parent ON research_sessions(parent_session_id);
