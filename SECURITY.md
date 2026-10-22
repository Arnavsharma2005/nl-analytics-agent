# Security

The agent runs SQL that a language model wrote, on behalf of anyone who visits the demo. That is the textbook
case in the [OWASP Top 10 for LLM applications](https://genai.owasp.org/llm-top-10/), so safety is enforced in
code and in the database, never by asking the model nicely.

## Controls

| Risk | Control | Where |
| --- | --- | --- |
| The model writes `DELETE`, `DROP`, `COPY`, `pg_read_file`, `dblink` … | Exactly one statement, which must parse as a `SELECT`; write nodes, row locks, every `pg_*`/`lo_*` function and other server functions refused; only the listed tables in `public` | `app/sql_guard.py` |
| Something gets past the guard | Every query runs in a `READ ONLY` transaction with a statement timeout, as a role that can only `SELECT` from `public` | `app/db.py`, `apps/api/db/roles.sql` |
| Runaway queries (a join with no condition, endless recursion) | `EXPLAIN` cost ceiling before running; statement timeout while running; a server-side cursor that fetches at most the row cap | `app/db.py`, `MAX_QUERY_COST` |
| Personal data in results | Columns listed in `PERSONAL_DATA_COLUMNS` can filter and be counted but are never returned; email addresses and phone numbers are masked in every result before the user or any model sees them | `app/sql_guard.py`, `app/privacy.py` |
| Prompt injection, in a question or in data the summary reads | The guard runs after the model, so injected SQL is still refused; the summary model has no tools and is told the rows are data | `app/agents/summary.py` |
| Error messages leaking internals | Errors are problem details with stable codes; unexpected exceptions are logged, not returned | `app/problems.py` |
| Secrets in the repo | gitleaks on every pull request; keys only in environment variables, never in prompts | `.github/workflows/ci.yml` |

`eval/safety.yaml` is an adversarial set of SQL that tries to break each control. `tests/test_safety.py` makes a
scripted model write each query on every attempt and checks it is refused, bounded or masked. It runs in CI and
must stay at 100%.

## Reporting a vulnerability

Please open a [private security advisory](https://github.com/Arnavsharma2005/nl-analytics-agent/security/advisories/new)
rather than a public issue.
