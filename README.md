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
- **Model tiers.** Routing the question and choosing the chart are light work, so they can run on a provider's
  cheaper model with a much larger free quota: set `<PROVIDER>_FAST_MODEL`, e.g. `GROQ_FAST_MODEL=llama-3.1-8b-instant`.
  SQL, summaries and schema answers always use the main model. A fast model has its own cooldown and its own
  budget (`"groq:fast"` in `LLM_DAILY_BUDGET`). Unset, every step uses the main model. Check the eval before and
  after turning one on.

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
alembic upgrade head               # create the app's own tables (usage limits, answer cache)
uvicorn app.main:app --reload      # API on http://localhost:8000

cd ../web
npm install
npm run dev                        # UI on http://localhost:5173
```

Or run everything in containers: `docker compose up --build`, then open http://localhost:8000.

### The new web app

`apps/web-next` is the v2 frontend, built on the `/v1` API. It will replace `apps/web` once it is deployed. It uses
React 19, Tailwind, TanStack Query and ECharts (loaded only when the first chart is drawn), with types generated
from the API's OpenAPI spec. Answers stream in, with each figure in the summary marked and a chart beside it.
"How this was answered" shows the steps, timings, models, failed tries, the SQL and a plain-English
explanation. You can also rate answers, download the CSV or chart, ask by voice, and pick from suggested
follow-up questions.

```bash
cd apps/web-next
npm ci
npm run dev                        # http://localhost:5173, forwarding /v1 to the API on :8000
npm run build && npx playwright test   # browser tests against a mocked API, with axe accessibility checks
```

After changing an API response model: `python apps/api/scripts/export_openapi.py`, then `npm run gen:api` in
`apps/web-next`. CI fails if either is out of date.

## API

Errors are [problem details](docs/errors.md) with a stable `code` to branch on.

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/api/ask` | `{"question": "..."}` → chart spec, summary, SQL, rows, attempts, timings |
| `POST` | `/api/ask/stream` | Same, as server-sent events: one `step` event per agent, then `result` |
| `POST` | `/api/session` | `{"turnstile_token": "..."}` → a visitor session cookie (the token is needed only when the bot check is on) |
| `GET` | `/api/config` | Settings the web app needs, such as the Turnstile site key |
| `GET` | `/api/schema` | Tables and columns |
| `GET` | `/api/providers` | Fallback chain: which providers are configured and which are cooling down |
| `GET` | `/api/examples` | Example questions |
| `GET` | `/healthz` | Liveness for uptime pings and the host's health check; never touches the database |
| `GET` | `/readyz` | Readiness: the database answers (`/api/health` is the same check) |

### /v1

The API the new web app (and later SDKs, MCP and bots) uses. `/api/*` above stays as it is for the current web
app. The full schema is at `/docs` while the app runs.

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/v1/conversations/{id}/messages` | Ask `{"question": "..."}` in a conversation (a UUID the client makes up). Send an `Idempotency-Key` UUID: it becomes the message id, and a retry with the same key replays the answer without asking (or spending quota) again. With `Accept: text/event-stream` the answer streams as `message`, `step`, `table`, `chart`, `token` (the summary as it is written) and `done`; otherwise it returns the message as JSON. Each message comes with three follow-up questions from the verified library. |
| `GET` | `/v1/messages/{id}` | A message you asked (messages are kept for 7 days and belong to your session) |
| `POST` | `/v1/messages/{id}/feedback` | `{"rating": "up" \| "down", "category": "wrong_data" \| "misunderstood" \| "wrong_chart" \| "lost_context" \| "other", "comment": "..."}` |
| `POST` | `/v1/messages/{id}/explain` | The answer's SQL as plain-English steps (one model call, then kept) |
| `POST` | `/v1/transcribe` | Multipart `audio` (webm, ogg, mp4, mpeg, wav or m4a, up to 2 MB) → `{"text": "..."}`, with Whisper on Groq; the key stays on the server |
| `POST` | `/v1/session` | Same as `/api/session` |
| `GET` | `/v1/config` | Turnstile site key, starter questions and which features are on |

## Business rules and verified queries

What the words in a question mean here lives in `apps/api/semantic/`, not in code, so it can be reviewed like
any other change:

- `metrics.yml`: the business rules the SQL agent follows (revenue counts completed orders only, how to read
  "last quarter", and so on). Each rule lists the terms it is about.
- `queries.yml`: verified queries, questions whose SQL someone has checked against the data. Those marked
  `starter` are the example questions on the front page. CI runs every one through the SQL guard and the
  database, and checks that none gives away a held-out eval question.

Changing a rule changes the SQL agent's prompt, so record the eval cassette again (below).

### Context and checks

Four switches, each off by default. Each is turned on only when the held-out eval score holds up with it
(`CONTEXT_RETRIEVAL=true python eval/run_eval.py --split test`, compared with a run without it).

| Setting | What it does |
| --- | --- |
| `CONTEXT_RETRIEVAL` | Shows the SQL agent the three verified queries most like the question, and the data values the question names, even misspelt ("Pro Laptops" → `'Pro Laptop'`). The agent also states how it read an ambiguous question; these come back as `assumptions`. |
| `VERIFIED_FAST_PATH` | Answers a question with a verified query outright when it asks the same thing in other words. Every detail that changes the answer must agree: numbers and years, top or bottom, the measure (count, share, average), the business terms, the breakdown and the values named. The answer says which verified question it used (`verified_question`). Needs `CONTEXT_RETRIEVAL`. |
| `CHECK_RESULTS` | Sends a query that finds nothing back to the SQL agent once. If it still finds nothing, that is the answer. |
| `CHECK_SUMMARIES` | Checks every figure in the summary against the result, allowing for rounding, ₹ L/Cr units, and simple sums, differences, ratios and shares. A summary that fails is rewritten once, and `summary_verified` says whether the final one passed. |

Similar questions are found with the `bge-m3` embedding model on Cloudflare Workers AI when the `CLOUDFLARE_*`
keys are set, and by shared words otherwise or when Cloudflare can't be reached. CI checks that the fast path
never answers a held-out eval question.

## Evaluation

`eval/questions.yaml` holds 120 labelled questions in four groups (simple aggregates, joins, time-based,
advanced: window functions, growth, shares, cohorts), each with a gold SQL query and a difficulty (easy, medium,
hard). They are split in two:

- **dev** (50): free to look at while tuning prompts, and the source of the first verified queries.
- **test** (70): held out. This is the number to report. Nothing in the prompts or the verified-query library may
  be written from these questions, and CI fails if a verified query returns the answer to one of them.

The first 30 questions are the original v1 set; they are all in dev.

```bash
python eval/run_eval.py              # runs all 120 through the full agent (uses the fallback chain)
python eval/run_eval.py --split test # only the held-out questions
python eval/run_eval.py --delay 4    # slower, to stay under free-tier rate limits
python eval/run_eval.py --gold-only  # check every gold query runs (no LLM needed; part of CI)
```

A prediction counts as correct when its result matches the gold result: same rows, values within rounding,
column names and order ignored, extra columns allowed, and row order checked only for rankings and time
series. The script prints accuracy by split, difficulty and group, how many answers were rescued by the retry
loop, and median latency, and saves the run to `eval/results/<date>-<commit>.json`. Compare any two runs question by question:

```bash
python eval/compare.py eval/results/<before>.json eval/results/<after>.json
```

To check a code change without spending model calls, record one real run and replay it. Replay feeds the
pipeline the same model answers, so any change in outcome comes from the code. CI does this on every pull
request once `eval/baseline/` is committed, and fails if a question that passed now fails.

```bash
python eval/run_eval.py --record eval/baseline/cassette.json --out eval/baseline/results.json   # needs keys
python eval/run_eval.py --replay eval/baseline/cassette.json                                    # no keys
```

If a change alters a prompt, replay reports a cassette miss for the questions it affects; record again.

The results file also records which provider wrote each query. For a clean number per model, run with a
single provider, e.g. `LLM_PROVIDERS=groq python eval/run_eval.py`.

| Model | SQL correctness | Median latency |
| --- | --- | --- |
| _fill in from your run_ | | |

## Usage limits

The public demo runs on free model quotas, so it protects them:

- **Bot check.** With `TURNSTILE_SITE_KEY` and `TURNSTILE_SECRET_KEY` set, a visitor passes a Cloudflare Turnstile
  check (usually invisible) before getting a session, and asking needs a session.
- **Quotas.** 30 questions per session per hour and 300 per IP address per day by default.
- **Model budgets.** `LLM_DAILY_BUDGET` caps calls per provider per day, a little under each free tier, so the app
  shows "today's free limit is used up" instead of failing at random.
- **Answer cache.** A repeat of a recent question is answered from the cache without any model calls, for 24 hours
  and until the next deploy.

The counters and the cache live in the app's own tables (schema `app`, migrated with `alembic upgrade head`). If
that database is unreachable, the limits are skipped and logged rather than taking the app down. Why the app keeps
its own fallback chain instead of LiteLLM is in [docs/adr](docs/adr/0001-keep-the-fallback-chain-over-litellm.md).

## Tests

```bash
cd apps/api && python -m pytest
ruff check .    # from the repo root
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

For a public demo, connect as a read-only role. `apps/api/db/roles.sql` creates one (`analyst_ro`) that can only
`SELECT` from `public`, with read-only transactions and a timeout enforced by the database. How the agent is kept
safe is described in [SECURITY.md](SECURITY.md).

## Project layout

```
apps/api/
  app/analyst.py  Analyst.ask / Analyst.stream: the one entry point for answering a question, and the Answer model
  app/agents/     router, sql_agent, chart, summary
  app/semantic.py, semantic/   business rules (metrics.yml) and verified queries (queries.yml)
  app/context/    similar verified queries, named values and the verified fast path
  app/checks.py   empty-result and summary-figure checks
  app/graph.py    LangGraph wiring and the retry loop
  app/sql_guard.py, app/db.py   validation, introspection, read-only execution
  app/llm.py, app/providers.py  fallback chain across the 5 providers
  app/replay.py   record model answers to a cassette and replay them
  app/sessions.py, app/usage.py, app/answer_cache.py   bot check, quotas, model budgets, answer cache
  migrations/     Alembic migrations for the app's own tables
  app/main.py     FastAPI app (also serves the built frontend)
  app/seed.py     sample dataset
  tests/
apps/web/         React + Vite + Recharts (the current frontend)
apps/web-next/    the v2 frontend on /v1; functions/ is the Cloudflare Pages proxy, e2e/ the browser tests
eval/             questions.yaml, run_eval.py, compare.py, results/ (run history), baseline/ (replay cassette)
PRD.md            product requirements
```
