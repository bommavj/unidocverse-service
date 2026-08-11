-- V15__add_username_column.sql
-- Migration: add username column for local authentication

ALTER TABLE users ADD COLUMN IF NOT EXISTS username VARCHAR(100);

-- Populate username from email for existing users (take part before @)
UPDATE users SET username = split_part(email, '@', 1) WHERE username IS NULL;

-- Make it required and unique
ALTER TABLE users ALTER COLUMN username SET NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'users_username_unique'
    ) THEN
        ALTER TABLE users ADD CONSTRAINT users_username_unique UNIQUE (username);
    END IF;
END$$;