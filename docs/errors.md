# API errors

Every error from the API is a [problem details](https://www.rfc-editor.org/rfc/rfc9457) object, sent as
`application/problem+json`:

```json
{
  "type": "https://github.com/Arnavsharma2005/nl-analytics-agent/blob/main/docs/errors.md#all-providers-failed",
  "code": "all_providers_failed",
  "title": "No model provider is available right now",
  "status": 503,
  "detail": "Every model provider failed (Groq · gpt-oss-120b: rate limited)"
}
```

Branch on `code`. It never changes once released. `title` and `detail` are written for people and may be reworded.
On the streaming endpoint the same object arrives as the data of an `error` event, with an extra `error` field
holding the message.

A question the agent could not answer (no working SQL after three attempts, or SQL the guard refused) is not an
error: the response is a normal answer with its `error` field set.

| Code | Status | Meaning |
| --- | --- | --- |
| <a id="invalid-request"></a>`invalid_request` | 422 | The body failed validation; `errors` lists each `field` and `message`. |
| <a id="all-providers-failed"></a>`all_providers_failed` | 503 | Every configured model provider failed or is cooling down; `errors` lists each provider's reason. Try again in a minute. |
| <a id="database-unavailable"></a>`database_unavailable` | 503 | `/readyz` could not reach the database. |
| <a id="unavailable"></a>`unavailable` | 503 | The service is temporarily unavailable. |
| <a id="not-found"></a>`not_found` | 404 | No such route. |
| <a id="method-not-allowed"></a>`method_not_allowed` | 405 | The route exists but not for this HTTP method. |
| <a id="http-error"></a>`http_error` | varies | Any other HTTP error. |
| <a id="internal-error"></a>`internal_error` | 500 | A bug on our side. The cause is logged, never returned. |
