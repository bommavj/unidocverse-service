-- V3: Analytics and Activity Tracking
-- Description: Tables for tracking document processing analytics

-- Activity log table
CREATE TABLE activity_log (
    id SERIAL PRIMARY KEY,
    event_type VARCHAR(100) NOT NULL,
    document_id VARCHAR(36) REFERENCES documents(id) ON DELETE SET NULL,
    user_id VARCHAR(255),
    description TEXT,
    status VARCHAR(50),
    event_data JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Indexes
CREATE INDEX idx_activity_log_created_at ON activity_log(created_at DESC);
CREATE INDEX idx_activity_log_event_type ON activity_log(event_type);
CREATE INDEX idx_activity_log_document_id ON activity_log(document_id);

-- Processing stats table
CREATE TABLE processing_stats (
    id SERIAL PRIMARY KEY,
    date DATE NOT NULL,
    total_processed INTEGER DEFAULT 0,
    successful INTEGER DEFAULT 0,
    failed INTEGER DEFAULT 0,
    avg_processing_time_seconds NUMERIC(10,2),
    total_storage_bytes BIGINT DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    UNIQUE(date)
);

-- Index
CREATE INDEX idx_processing_stats_date ON processing_stats(date DESC);

-- Comments
COMMENT ON TABLE activity_log IS 'Tracks all system events and user actions';
COMMENT ON TABLE processing_stats IS 'Daily aggregated processing statistics';