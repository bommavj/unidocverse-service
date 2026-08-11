-- V14: Create users table
CREATE TABLE IF NOT EXISTS users (
    id            UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    email         VARCHAR(255) UNIQUE,
    full_name     VARCHAR(255) NOT NULL DEFAULT '',
    password_hash VARCHAR(255) NOT NULL,
    role          VARCHAR(50)  NOT NULL DEFAULT 'user',
    is_active     BOOLEAN      NOT NULL DEFAULT TRUE,
    avatar_color  VARCHAR(20)  NOT NULL DEFAULT '#6366f1',
    last_login    TIMESTAMPTZ,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    google_id     VARCHAR(255),
    microsoft_id  VARCHAR(255),
    apple_id      VARCHAR(255)
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
