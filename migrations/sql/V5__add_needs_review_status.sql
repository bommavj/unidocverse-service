-- Migration: Add needs_review status to documents table
-- Author: UniDocVerse AI (Vijay Bomma)
-- Date: 2025-12-12

-- Drop existing constraint
ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_status_check;

-- Add new constraint with needs_review status
ALTER TABLE documents ADD CONSTRAINT documents_status_check
CHECK (status IN ('pending', 'processing', 'completed', 'failed', 'needs_review'));

-- Add comment
COMMENT ON CONSTRAINT documents_status_check ON documents IS
'Valid document statuses: pending, processing, completed, failed, needs_review';