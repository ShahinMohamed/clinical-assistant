-- Apply with the Neon owner account before granting the app access.
CREATE SCHEMA IF NOT EXISTS app_private;
REVOKE ALL ON SCHEMA app_private FROM PUBLIC;

CREATE TABLE IF NOT EXISTS app_private.research_budget (
    bucket TEXT NOT NULL,
    window_start TIMESTAMPTZ NOT NULL,
    requests INTEGER NOT NULL CHECK (requests > 0),
    PRIMARY KEY (bucket, window_start)
);

-- The deployment script creates a separate clinical_app LOGIN and grants it:
-- CONNECT, USAGE on public/app_private, SELECT on the cohort and evidence
-- tables, and SELECT/INSERT/UPDATE/DELETE on research_budget. Never deploy
-- the Neon owner connection string as the application's DATABASE_URL.
