-- GhostVault Intelligence System — PostgreSQL initialisation
-- Runs once on first container start

-- Extensions
CREATE EXTENSION IF NOT EXISTS "pgcrypto";   -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS "pg_trgm";    -- trigram indexes for address search

-- Ensure UTF-8
SET client_encoding = 'UTF8';
