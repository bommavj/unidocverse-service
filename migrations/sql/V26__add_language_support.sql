-- V29__add_language_support.sql
-- Migration: Add language preference support for users and documents

ALTER TABLE users ADD COLUMN IF NOT EXISTS language VARCHAR(5) DEFAULT 'en';
ALTER TABLE documents ADD COLUMN IF NOT EXISTS language VARCHAR(5) DEFAULT 'en';
