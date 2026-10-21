-- V64: Vague questions are now answered with a clarifying question in the conversation;
-- they no longer go to the approval queue. Withdraw the ones still pending there.
UPDATE approval_requests SET status = 'withdrawn', updated_at = NOW()
WHERE source = 'clarification' AND status = 'pending';
