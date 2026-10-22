# Run it yourself

The Sales analyst ships as one Docker image (the API, the web app and the MCP server) and a compose file that adds
Postgres. It runs on any machine with Docker: a laptop, a small VPS, or a home server. It starts on the demo's sample
sales data; the second half of this page points it at your own database instead.

## Quickstart

You need Docker with Compose v2 and a key for at least one model provider. [Gemini](https://aistudio.google.com/apikey)
and [Groq](https://console.groq.com/keys) both have free tiers.

```bash
mkdir sales-analyst && cd sales-analyst
curl -fsSLO https://raw.githubusercontent.com/Arnavsharma2005/nl-analytics-agent/main/self-host/compose.yml
curl -fsSL https://raw.githubusercontent.com/Arnavsharma2005/nl-analytics-agent/main/self-host/.env.example -o .env
# edit .env: add GEMINI_API_KEY or GROQ_API_KEY, and SESSION_SECRET (openssl rand -hex 32)
docker compose up -d
```

Open http://localhost:8000. The first start loads the sample data, which takes about half a minute;
`docker compose ps` shows the app as healthy once it is ready.

To upgrade, pin `VERSION` in `.env` (for example `VERSION=2.3.0`; the image tags are on the
[packages page](https://github.com/Arnavsharma2005/nl-analytics-agent/pkgs/container/nl-analytics-agent)), then
`docker compose pull && docker compose up -d`. The app's own tables are migrated on start.

## Ask from Claude Desktop or another agent

The bundle turns on the MCP server at `/mcp` (set `MCP=false` in `.env` to turn it off). In Claude Desktop, add a
custom connector with the URL `http://localhost:8000/mcp`, or for clients that only launch local servers:

```json
{ "mcpServers": { "sales-analyst": { "command": "npx", "args": ["-y", "mcp-remote", "http://localhost:8000/mcp"] } } }
```

Its tools are `ask`, `list_tables`, `list_verified_questions` and `run_verified_query`; see
[MCP](../README.md#mcp-ask-from-claude-desktop-cursor-or-another-agent) in the README.

## Your own data

The analyst can answer questions about any Postgres database. The app's own tables (history, limits, uploads) stay
in the bundled Postgres; only the questions go to yours.

### 1. A role that can only read

Connect as a role that can only `SELECT`, so that even a query that got past the SQL guard could not change
anything. As the database's owner:

```sql
CREATE ROLE analytics_reader LOGIN PASSWORD 'choose-a-long-random-password';
ALTER ROLE analytics_reader SET default_transaction_read_only = on;
ALTER ROLE analytics_reader SET statement_timeout = '15s';
GRANT CONNECT ON DATABASE shop TO analytics_reader;
GRANT USAGE ON SCHEMA public TO analytics_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO analytics_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO analytics_reader;
```

[`apps/api/db/roles.sql`](../apps/api/db/roles.sql) is a fuller version, including how to hide a column from the
role entirely. Only tables in the `public` schema are used.

### 2. Point the app at it

In `.env`:

```bash
DATABASE_URL=postgresql://analytics_reader:<password>@db.example.com:5432/shop
SAMPLE_DATA=false                 # don't load the demo's tables into the bundled Postgres
REQUIRE_READ_ONLY_DATA=true       # refuse to start if DATABASE_URL could change data
PERSONAL_DATA_COLUMNS=["customers.email","customers.phone"]
```

With `REQUIRE_READ_ONLY_DATA=true` the app checks the connection on start and stops, naming what is wrong, if the
role is a superuser, can insert, update, delete or truncate any table, or can create tables. Without it, the same
findings are logged as a warning.

`PERSONAL_DATA_COLUMNS` lists columns that may be used to filter and count but are never shown in an answer. A
database on the same machine as Docker is reachable as `host.docker.internal`.

### 3. Tell it what your data means

Without more, the analyst reads your tables from their names and the comments on them (`COMMENT ON COLUMN ...`
helps a great deal). To give it your business rules and checked example questions, make a folder with either or both
of these files, mount it and set `SEMANTIC_DIR`:

`semantic/metrics.yml`:

```yaml
about:
  data: a helpdesk database                # what the data is
  time_column: opened_at                   # the date column time series group by
  money: Amounts are in euros; write them like €1,200.
  out_of_scope: I can only answer questions about support tickets and the agents who handle them.

rules:
  - id: open_tickets
    terms: [open, backlog, unresolved]
    text: Open tickets have status IN ('new', 'open', 'pending'); "resolved" means status = 'closed'.
```

`semantic/queries.yml`:

```yaml
queries:
  - question: How many tickets are open?
    sql: SELECT count(*) AS open_tickets FROM tickets WHERE status IN ('new', 'open', 'pending')
    verified_by: you
    verified_at: 2026-10-22
    starter: true          # offered as an example question on the front page
```

Each part of `about` can be left out; the defaults describe "a business database" and say nothing about money.
Verified queries are shown to the analyst as worked examples, and `run_verified_query` over MCP runs them as written.

In `compose.yml`, uncomment the volume under `app`, and in `.env` set `SEMANTIC_DIR=/semantic`:

```yaml
    volumes:
      - ./semantic:/semantic:ro
```

Then `docker compose up -d` again.

## Checking an image

[`self-host/smoke.sh`](../self-host/smoke.sh) starts the stack from an image and checks that it is ready, serves the
web app, takes a CSV upload and answers over MCP, with no model key. CI runs it on every change to the image and
before each release is published:

```bash
docker build -t nl-analytics-agent:local .
IMAGE=nl-analytics-agent VERSION=local self-host/smoke.sh
```

Releases are published by pushing a version tag (`git tag v2.3.0 && git push origin v2.3.0`), which builds the image
for amd64 and arm64 and pushes it to GitHub Container Registry as `2.3.0`, `2.3` and `latest`.
