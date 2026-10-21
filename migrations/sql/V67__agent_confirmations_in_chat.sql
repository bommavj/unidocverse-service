-- V67: The research agent asks for confirmation in the chat. An unanswered request lapses after a day
-- instead of waiting 30 days in the Approvals tab. Expire the ones already left waiting.
UPDATE approval_requests SET status = 'expired', updated_at = NOW()
WHERE source = 'agent' AND status = 'pending' AND created_at < NOW() - interval '1 day';
UPDATE approval_requests SET expires_at = created_at + interval '1 day'
WHERE source = 'agent' AND status = 'pending';
