-- All domain agents run at 04:00 AM by default
UPDATE scheduled_agents SET cron_hour = 4, cron_minute = 0, updated_at = NOW();
