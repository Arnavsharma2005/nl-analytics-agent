# Natural Language Business Analytics Agent

Ask a business question in plain English, get back a chart, a short insight summary, and the SQL behind it.
Built so business stakeholders don't have to wait on an analyst to write SQL for them.

**Live demo:** https://nl-analytics-agent.onrender.com (free hosting, so the first visit can take up to a minute to wake up)

> "Which region grew revenue fastest from 2024 to 2025?" → a bar chart, a two-sentence answer, and a
> validated PostgreSQL query you can inspect.

## How it works

A LangGraph pipeline with four agents and a validate-and-repair loop:

```
question ─► Router agent ──► out of scope ─► polite reply
                 │      └──► schema question ─► description of the data
                 ▼ data question
            SQL agent ─► Validator ─► Execute (read-only) ─► Chart agent ─► Summary agent ─► answer
                 ▲           │ error        │ error
                 └───────────┴──────────────┘  (retries with the error message, up to 3 attempts)
```

| Agent | What it does |
| --- | --- |
| Router | Classifies the message: data question, question about the schema, or out of scope |
| SQL | Writes one PostgreSQL `SELECT` from the live schema (columns, types, foreign keys, comments, enum values) and business rules (what "revenue" means, how to read "last quarter") |
| Validator | Not an LLM. `sqlglot` checks it is a single read-only `SELECT` over known tables with no dangerous functions, then Postgres `EXPLAIN` checks columns and types. Errors go back to the SQL agent |
| Chart | Picks bar / line / pie / scatter / KPI / table and the axes. The spec is checked against the actual result columns, with a rule-based fallback |
| Summary | Writes a 2–4 sentence plain-English insight using only numbers in the result |

Queries run inside a `READ ONLY` transaction with a 10 s statement timeout and a 500-row cap.

**Stack:** Python, LangGraph, LangChain, FastAPI, PostgreSQL, SQLAlchemy, sqlglot, React, TypeScript, Recharts, Docker, Railway, GitHub Actions.

## Model fallback chain

The agent runs on free-tier APIs, which rate-limit and go down. So every model call goes through a
fallback chain of five providers, all reached through their OpenAI-compatible endpoints:

| # | Provider | Default model | Key variable |
| --- | --- | --- | --- |
| 1 | Google Gemini | `gemini-flash-latest` | `GEMINI_API_KEY` |
| 2 | Groq | `openai/gpt-oss-120b` | `GROQ_API_KEY` |
| 3 | OpenRouter | `nvidia/nemotron-3-super-120b-a12b:free` | `OPENROUTER_API_KEY` |
| 4 | Mistral | `mistral-large-latest` | `MISTRAL_API_KEY` |
| 5 | Cloudflare Workers AI | `@cf/meta/llama-3.3-70b-instruct-fp8-fast` | `CLOUDFLARE_API_TOKEN` + `CLOUDFLARE_ACCOUNT_ID` |

- Each call goes to the first provider that has a key and isn't cooling down.
- A rate limit, timeout (30 s), outage, rejected key or unusable reply moves the call to the next provider.
- A provider that failed is skipped for a while: 60 s after a per-minute limit or outage, 1 h after a daily
  quota, a bad key or a missing model. That way one slow provider doesn't slow down every request.
- Structured outputs are requested as plain JSON and validated with Pydantic, which works on every provider.
- Each answer shows which model wrote it and which providers it fell back past.
  `GET /api/providers` lists the chain and any cooldowns, and never returns keys.
- Reorder the chain with `LLM_PROVIDERS`, and override a model with `<PROVIDER>_MODEL`, e.g. `GROQ_MODEL=qwen/qwen3.6-27b`.

Free model lists change often, so check each provider's model page if one reports "model not found".

## The data

`apps/api/app/seed.py` generates a deterministic e-commerce dataset for Jan 2024 to Dec 2025: 6 regions,
2,000 customers, 8 categories, 120 products, about 17.8k orders and 31k line items, with growth trends,
festive-season peaks, returns and cancellations. The same seed always gives the same rows, so evaluation
results are reproducible.

## Run it locally

Requires Python 3.12+, Node 22+, and PostgreSQL 16 (or Docker).

```bash
cp .env.example .env               # add keys for any of the 5 providers (at least one)
docker compose up db -d            # or use your own Postgres and set DATABASE_URL

cd apps/api
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
python -m app.seed                 # create tables and load the sample data
uvicorn app.main:app --reload      # API on http://localhost:8000

cd ../web
npm install
npm run dev                        # UI on http://localhost:5173
```

Or run everything in containers: `docker compose up --build`, then open http://localhost:8000.

## API

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/api/ask` | `{"question": "..."}` → chart spec, summary, SQL, rows, attempts, timings |
| `POST` | `/api/ask/stream` | Same, as server-sent events: one `step` event per agent, then `result` |
| `GET` | `/api/schema` | Tables and columns |
| `GET` | `/api/providers` | Fallback chain: which providers are configured and which are cooling down |
| `GET` | `/api/examples` | Example questions |
| `GET` | `/api/health` | Liveness and database check |

## Evaluation

`eval/questions.yaml` holds 30 labelled questions in four groups (simple aggregates, joins, time-based,
advanced: window functions, growth, shares, cohorts), each with a gold SQL query.

```bash
python eval/run_eval.py              # runs all 30 through the full agent (uses the fallback chain)
python eval/run_eval.py --delay 4    # slower, to stay under free-tier rate limits
python eval/run_eval.py --gold-only  # check every gold query runs (no LLM needed; part of CI)
```

A prediction counts as correct when its result matches the gold result: same rows, values within rounding,
column names and order ignored, extra columns allowed, and row order checked only for rankings and time
series. The script prints accuracy by group, how many answers were rescued by the retry loop, and median
latency, and saves `eval/results.json`.

The results file also records which provider wrote each query. For a clean number per model, run with a
single provider, e.g. `LLM_PROVIDERS=groq python eval/run_eval.py`.

| Model | SQL correctness | Median latency |
| --- | --- | --- |
| _fill in from your run_ | | |

## Tests

```bash
cd apps/api && python -m pytest
```

Tests use scripted fake models against a real Postgres. They cover the SQL guard, the retry loop
(bad column, runtime error, giving up after 3 attempts), the provider fallback chain (rate limits,
timeouts, bad keys, junk output, cooldowns), routing, chart validation, the evaluator and the API including
streaming. None of them need an API key.

## Deploy on Railway

Step-by-step instructions, including where to get each API key, are in [DEPLOY.md](DEPLOY.md).

1. Create a project with a PostgreSQL database and a service from this repo (the `Dockerfile` is picked up via `railway.json`).
2. On the service, set `DATABASE_URL` to `${{Postgres.DATABASE_URL}}`, plus the API keys from `.env.example`.
3. Deploy. The pre-deploy step seeds the database on first deploy and skips it afterwards.

For a public demo, connect with a read-only role:

```sql
CREATE ROLE analytics_reader LOGIN PASSWORD '...';
GRANT CONNECT ON DATABASE railway TO analytics_reader;
GRANT USAGE ON SCHEMA public TO analytics_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO analytics_reader;
```

## Project layout

```
apps/api/
  app/agents/     router, sql_agent, chart, summary
  app/graph.py    LangGraph wiring and the retry loop
  app/sql_guard.py, app/db.py   validation, introspection, read-only execution
  app/llm.py, app/providers.py  fallback chain across the 5 providers
  app/main.py     FastAPI app (also serves the built frontend)
  app/seed.py     sample dataset
  tests/
apps/web/         React + Vite + Recharts
eval/             questions.yaml and run_eval.py
PRD.md            product requirements
```
