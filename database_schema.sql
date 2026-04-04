CREATE DATABASE IF NOT EXISTS jhdash_events
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS Events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    event_name TEXT NOT NULL,
    event_date TEXT,
    event_time TEXT,
    source_url TEXT,
    main_url TEXT,
    secondary_url TEXT,
    event_address TEXT,
    venue_name TEXT,
    host_organization_name TEXT,
    event_description TEXT,
    event_status TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    event_composite TEXT,
    embedding VECTOR(1536),
    occurrences JSONB NOT NULL DEFAULT '{}'
);


CREATE TABLE IF NOT EXISTS CanonicalNames (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name_type TEXT,
    canonical_name TEXT NOT NULL,
    raw_name TEXT NOT NULL,
    website_url TEXT,
    embedding VECTOR(1536),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Add a unique constraint on raw_name and name_type
ALTER TABLE canonical_names ADD CONSTRAINT unique_raw_name_name_type UNIQUE (raw_name, name_type);