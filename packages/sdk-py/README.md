# nl-analytics-sdk (Python)

A small client for the analytics agent's `/v1` API. Its models are generated from the API's OpenAPI spec
(`./generate.sh`), so they match the API of the same commit.

```sh
pip install nl-analytics-sdk
# or, before a release is published, from the repository:
pip install "git+https://github.com/Arnavsharma2005/nl-analytics-agent#subdirectory=packages/sdk-py"
```

```python
from nl_analytics import Analyst, ApiError

with Analyst("https://nl-analytics-agent.onrender.com", api_key="aak_...") as analyst:
    chat = analyst.conversation()
    first = chat.ask("Revenue by region in 2025")
    print(first.answer.summary)
    print(first.answer.columns, first.answer.rows)
    print(chat.ask("and in 2024?").answer.sql)

    for insight in analyst.insights().insights:
        print(insight.headline)

    started = analyst.start_report("How did 2025 go, and where should we focus next year?")
    report = analyst.wait_for_report(started.id)
```

Make a key at `/keys` on the web app after signing in. Requests with it ask as you, within your question limits.
A refused request raises `ApiError` with a stable `code` (`quota_exceeded`, `invalid_api_key`,
`origin_not_allowed`, ...) and, when the API gives one, `retry_after` in seconds.
