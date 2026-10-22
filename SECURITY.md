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
| Scripts burning the free model quota | Cloudflare Turnstile before a visitor session is issued; questions limited per session per hour and per IP per day; model calls limited per provider per day, with a clear "daily limit reached" error; repeat questions answered from a cache | `app/sessions.py`, `app/usage.py`, `app/answer_cache.py` |
| Prompt injection, in a question or in data the summary reads | The guard runs after the model, so injected SQL is still refused; the summary model has no tools and is told the rows are data | `app/agents/summary.py` |
| Sign-in: forged callbacks, stolen codes, open redirects | OAuth with PKCE and a state value checked against a signed, ten-minute cookie; no GitHub scopes requested and the token is not kept; the page to return to must be a path on this site; cookies are HttpOnly, SameSite=Lax and Secure, and each is signed for one purpose only | `app/auth.py`, `app/api_auth.py`, `app/sessions.py` |
| Uploaded files: reading the server's files, the network or other uploads through SQL | Each upload is queried in its own DuckDB with external access, extension loading and installs turned off and the settings locked, a memory limit, one thread and a statement timeout; the guard also refuses DuckDB's file, catalog and settings functions (`read_*`, `glob`, `getenv`, `duckdb_*` …) | `app/datasets/duck.py`, `app/sql_guard.py` |
| Personal data in an uploaded file | Columns are checked by name and by value when the file arrives; personal ones can filter and be counted but are never returned, nor is a whole row of a table that has one (`SELECT t`, `COLUMNS(*)`, `UNPIVOT`); their values are left out of the column notes the models see | `app/datasets/profile.py`, `app/sql_guard.py` |
| Hostile uploads (huge, binary, Excel, too many rows or columns) and filling the database | Size, row and column limits checked before anything is kept; at most 3 uploads per owner and a total cap across everyone; every upload is deleted after a day | `app/datasets/csv_upload.py`, `app/datasets/store.py` |
| Prompt injection in an uploaded file's values | Values reach the models only as examples in column notes and as result rows; the guard still checks whatever SQL comes back, and the summary is told the rows are data | `app/agents/summary.py` |
| Share links exposing more than the answer, or being guessed | A share is a copy of what its owner saw (rows already masked), without the models used or failed tries, and is never re-run; its id is 96 random bits, malformed ids are refused before any lookup, only the owner can create or delete one, and it expires after 90 days | `app/shares.py`, `app/api_shares.py` |
| One visitor reading another's questions or uploads | Every message and uploaded file belongs to a signed-in user or an anonymous session, and only its owner can read, rate, explain, continue or query it | `app/messages.py`, `app/datasets/store.py` |
| Error messages leaking internals | Errors are problem details with stable codes; unexpected exceptions are logged, not returned | `app/problems.py` |
| Secrets in the repo | gitleaks on every pull request; keys only in environment variables, never in prompts | `.github/workflows/ci.yml` |

`eval/safety.yaml` is an adversarial set of SQL that tries to break each control. `tests/test_safety.py` makes a
scripted model write each query on every attempt and checks it is refused, bounded or masked. It runs in CI and
must stay at 100%.

## Reporting a vulnerability

Please open a [private security advisory](https://github.com/Arnavsharma2005/nl-analytics-agent/security/advisories/new)
rather than a public issue.
