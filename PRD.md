# PRD: Natural Language Business Analytics Agent

Owner: Arnav Sharma · Last updated: 21 Oct 2026 · Status: Draft v1

## 1. Problem

Business stakeholders (sales leads, ops managers, founders) have questions about their data every day:
"Which region grew fastest last quarter?", "What are our top 10 products by revenue?". Today each question
goes into an analyst's queue, because answering it needs SQL. The analyst becomes a bottleneck, simple
questions wait days, and many are never asked.

## 2. Goal

Let a non-technical user type a data question in plain English and get back, in seconds:

1. a chart that fits the answer,
2. a short plain-English insight summary, and
3. the SQL and the raw rows, so an analyst can check the answer.

### Success metrics

| Metric | Target | How it is measured |
| --- | --- | --- |
| SQL correctness (execution accuracy) | ≥ 80% | Evaluation set of 30 labelled NL questions; predicted SQL result must match the gold SQL result |
| Invalid SQL reaching the database | 0 | Every query passes the validator (parse, read-only, schema check, `EXPLAIN`) before it runs |
| End-to-end latency | < 10 s median | Timed in the evaluation run |
| Chart relevance | Chart type valid for the result shape on every answered question | Chart spec is checked against result columns |

### Non-goals (v1)

- Writing to the database (INSERT, UPDATE, DELETE, DDL). The agent is strictly read-only.
- Multi-database support. v1 targets PostgreSQL only.
- User accounts and per-user permissions.
- Uploading your own CSV or connecting your own database (possible v2).

## 3. Users

- **Business stakeholder** (primary): knows the business, not SQL. Wants an answer and a chart.
- **Analyst** (secondary): wants to see the SQL to trust or reuse the answer.
- **Recruiter / interviewer** (demo audience): opens the live demo, tries example questions.

## 4. User stories

1. As a stakeholder, I type "monthly revenue in 2025" and see a line chart with a one-paragraph summary.
2. As a stakeholder, I click an example question so I know what kinds of questions work.
3. As a stakeholder, if I ask something the data can't answer ("what's the weather?"), I get a polite
   explanation instead of a wrong chart.
4. As an analyst, I expand "Show SQL" to see the exact query and the result table.
5. As any user, I see progress while the agent works (Understanding → Writing SQL → Building chart → Summarising).

## 5. Solution overview

A LangGraph multi-agent pipeline behind a FastAPI backend, with a React frontend, deployed on Railway with a
managed PostgreSQL database holding a sample e-commerce dataset.

```
User question
    │
    ▼
Router agent ── out of scope / greeting ──► polite reply (end)
    │ schema question ──► schema answer (end)
    │ data question
    ▼
SQL agent ──► validator (parse · SELECT-only · tables/columns exist · EXPLAIN)
    ▲              │ error
    └── retry with error feedback (max 3 attempts)
    │ valid
    ▼
Execute (read-only transaction, 10 s timeout, row cap)
    │
    ▼
Chart agent ──► chart spec (bar / line / pie / scatter / KPI / table), checked against result columns
    │
    ▼
Summary agent ──► 2–4 sentence plain-English insight grounded in the rows
    │
    ▼
Response: chart spec + summary + SQL + rows
```

### 5.1 Agents

| Agent | Input | Output | Notes |
| --- | --- | --- | --- |
| Router | Question, table list | Intent: `data_query`, `schema_question`, `out_of_scope` | Structured output; cheap call |
| SQL | Question, schema description (tables, columns, types, FKs, sample values) | One PostgreSQL `SELECT` | Self-validates; on failure gets the validator error and retries, up to 3 attempts |
| Chart | Question, result columns + types, first rows | Chart spec: type, x, y, series, title | Spec validated against real columns; falls back to a rule-based choice if invalid |
| Summary | Question, SQL, result rows (capped) | 2–4 sentence insight | Must only cite numbers present in the rows |

### 5.2 SQL safety

- Parsed with `sqlglot`; only a single `SELECT` (CTEs allowed) is accepted.
- Every referenced table and column must exist in the introspected schema.
- `EXPLAIN` is run first to catch type and syntax errors without executing.
- Execution runs in a `READ ONLY` transaction with `statement_timeout = 10s`; results capped at 500 rows.
- In production the app connects with a read-only Postgres role.

### 5.3 Sample dataset

A seeded, deterministic e-commerce dataset (2024–2025) so the demo and the evaluation are reproducible:
`regions`, `customers`, `categories`, `products`, `orders`, `order_items`. Includes signups, order status
(completed / cancelled / returned), channels, discounts and costs so questions about revenue, margin, growth,
retention and returns are all answerable.

## 6. API

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/ask` | Body `{question}` → `{intent, sql, columns, rows, chart, summary, attempts, timings}` |
| `POST` | `/api/ask/stream` | Same, streamed as server-sent events: one event per pipeline step, then the final result |
| `GET` | `/api/schema` | Tables and columns, for the UI's "What data is here?" panel |
| `GET` | `/api/examples` | Example questions |
| `GET` | `/api/health` | Liveness and database check |

## 7. Frontend

Single page React app (Vite + TypeScript, Recharts):

- Question box with example question chips.
- Live step indicator driven by the stream.
- Result card: insight summary on top, chart below, collapsible SQL and data table.
- Clear error state for out-of-scope questions and failed queries.

## 8. Tech stack

Python 3.12, LangGraph, LangChain (OpenAI-compatible clients with an automatic fallback chain
across eight free-tier providers: Gemini, Groq, OpenRouter, NVIDIA, Mistral, Cohere, Z.ai and Cloudflare), FastAPI, SQLAlchemy + psycopg, sqlglot, PostgreSQL 16,
React + Vite + TypeScript + Recharts, Docker, Railway, GitHub Actions (tests).

## 9. Evaluation

- `eval/questions.yaml`: 30 questions across difficulty levels (simple aggregates, joins, date filters,
  growth and ranking with window functions, ambiguous wording), each with a gold SQL query.
- `eval/run_eval.py`: runs each question through the full graph, executes predicted and gold SQL, and compares
  result sets (order-insensitive unless the question asks for an order, floats rounded to 2 dp, column names
  ignored). Reports accuracy, per-category accuracy, retries used and latency, and writes `eval/results.json`.
- The resume number must be the number this script reports.

## 10. Milestones

1. Database + seed data + schema introspection.
2. SQL validator and safe executor, with unit tests.
3. LangGraph pipeline with the four agents, tested with a scripted fake LLM.
4. FastAPI endpoints, including streaming.
5. React frontend.
6. Evaluation set and harness.
7. Dockerfile, Railway config, GitHub Actions CI, README with demo GIF/screenshots.

## 11. Risks

| Risk | Mitigation |
| --- | --- |
| LLM writes plausible but wrong SQL | Schema with sample values in the prompt, validator retries, eval set to measure |
| Prompt injection via the question ("drop table …") | Read-only role and transaction, SELECT-only validator |
| Free-tier LLM rate limits during a demo | Fallback chain across 8 providers; a rate-limited or failing provider is skipped and cooled down |
| Summary invents numbers | Summary prompt is given only the rows and told to cite only them |
