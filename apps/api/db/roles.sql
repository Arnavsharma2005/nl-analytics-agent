-- A login role that can only read the analytics tables. In production, DATABASE_URL should connect as this role,
-- so that even a query that got past the SQL guard could not write, read files or reach other schemas.
--
-- Run once as the database owner, after loading the data (python -m app.seed), with psql:
--   psql "$OWNER_DATABASE_URL" -v ro_password="'choose-a-long-random-password'" -f apps/api/db/roles.sql
-- then set DATABASE_URL=postgresql://analyst_ro:<password>@<host>/<database>.

CREATE ROLE analyst_ro LOGIN PASSWORD :ro_password;

-- Read-only by default, with a hard timeout, whatever the session asks for.
ALTER ROLE analyst_ro SET default_transaction_read_only = on;
ALTER ROLE analyst_ro SET statement_timeout = '15s';
ALTER ROLE analyst_ro SET idle_in_transaction_session_timeout = '30s';
ALTER ROLE analyst_ro CONNECTION LIMIT 10;

DO $$ BEGIN EXECUTE format('GRANT CONNECT ON DATABASE %I TO analyst_ro', current_database()); END $$;

-- Only the public schema, only SELECT, and nothing new can be created in it.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO analyst_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO analyst_ro;

-- Tables added later by the owner are readable too.
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO analyst_ro;

-- Optional, strictest: make customers.email unreadable at the database level. The SQL guard already refuses to
-- return it and results are masked, but with this even "how many customers use gmail" stops working.
-- REVOKE SELECT ON customers FROM analyst_ro;
-- GRANT SELECT (customer_id, name, region_id, segment, signup_date) ON customers TO analyst_ro;
