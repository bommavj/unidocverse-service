-- V21__renewal_pipeline.sql
-- Renewal Pipeline: activity tracking per policy renewal

CREATE TABLE IF NOT EXISTS renewal_activities (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    policy_id       UUID        NOT NULL REFERENCES policies(id) ON DELETE CASCADE,
    client_id       UUID        NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    activity_type   VARCHAR(30) NOT NULL
                        CHECK (activity_type IN (
                            'note','call','email','quote_sent','quote_accepted',
                            'quote_declined','bound','lost','non_renewed','reminder_sent'
                        )),
    title           VARCHAR(200),
    notes           TEXT,
    due_date        DATE,
    completed_at    TIMESTAMPTZ,
    completed_by    VARCHAR(100),
    created_by      VARCHAR(100),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Renewal notifications config per agency
CREATE TABLE IF NOT EXISTS renewal_notification_rules (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    days_before     INTEGER     NOT NULL,   -- e.g. 90, 60, 30, 14
    notification_type VARCHAR(20) NOT NULL DEFAULT 'email'
                        CHECK (notification_type IN ('email','sms','dashboard')),
    template        TEXT,
    is_active       BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Seed default renewal notification rules
INSERT INTO renewal_notification_rules (days_before, notification_type, template)
VALUES
    (90, 'dashboard', 'Policy {policy_number} renews in 90 days — start renewal process'),
    (60, 'email',     'Renewal reminder: {policy_number} for {insured_name} expires {expiry_date}'),
    (30, 'email',     'URGENT: Policy {policy_number} expires in 30 days'),
    (14, 'dashboard', 'CRITICAL: Policy {policy_number} expires in 14 days — immediate action required')
ON CONFLICT DO NOTHING;

CREATE INDEX IF NOT EXISTS idx_renewal_activities_policy    ON renewal_activities(policy_id);
CREATE INDEX IF NOT EXISTS idx_renewal_activities_client    ON renewal_activities(client_id);
CREATE INDEX IF NOT EXISTS idx_renewal_activities_due_date  ON renewal_activities(due_date);
CREATE INDEX IF NOT EXISTS idx_renewal_activities_type      ON renewal_activities(activity_type);