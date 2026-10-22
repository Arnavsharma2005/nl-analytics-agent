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
On the streaming endpoints the same object arrives as the data of an `error` event (on `/api/ask/stream` with an
extra `error` field holding the message).

A question the agent could not answer (no working SQL after three attempts, or SQL the guard refused) is not an
error: the response is a normal answer with its `error` field set.

| Code | Status | Meaning |
| --- | --- | --- |
| <a id="invalid-request"></a>`invalid_request` | 422 | The body failed validation; `errors` lists each `field` and `message`. |
| <a id="bot-check-required"></a>`bot_check_required` | 403 | The bot check is on and the request has no valid session. Start one with `POST /api/session` and a Turnstile token, then retry. |
| <a id="bot-check-failed"></a>`bot_check_failed` | 403 | Cloudflare did not accept the Turnstile token. Tokens are single-use and expire after five minutes. |
| <a id="quota-exceeded"></a>`quota_exceeded` | 429 | This visitor or IP address has asked its share of questions for now. `retry_after` (also the `Retry-After` header) says when to try again. |
| <a id="daily-limit-reached"></a>`daily_limit_reached` | 503 | Every model provider has used today's budget. Answers already in the cache still work; `retry_after` counts down to midnight UTC. |
| <a id="all-providers-failed"></a>`all_providers_failed` | 503 | Every configured model provider failed or is cooling down; `errors` lists each provider's reason. Try again in a minute. |
| <a id="idempotency-key-reused"></a>`idempotency_key_reused` | 422 | `/v1`: this `Idempotency-Key` was already used for a different question, conversation or session. Send a new key per question. |
| <a id="request-in-progress"></a>`request_in_progress` | 409 | `/v1`: the first request with this `Idempotency-Key` is still being answered. Retry after `retry_after` seconds to get its answer. |
| <a id="message-not-found"></a>`message_not_found` | 404 | `/v1`: no answered message with this id in your session (they are kept for 7 days). |
| <a id="nothing-to-explain"></a>`nothing_to_explain` | 422 | `/v1`: the answer has no query to explain (an out-of-scope or schema question, or a failed one). |
| <a id="transcription-unavailable"></a>`transcription_unavailable` | 503 | `/v1/transcribe`: voice questions are not set up on this server (no Groq key). |
| <a id="unsupported-audio"></a>`unsupported_audio` | 415 | `/v1/transcribe`: send audio/webm, ogg, mp4, mpeg, wav or m4a. |
| <a id="audio-too-large"></a>`audio_too_large` | 413 | `/v1/transcribe`: the recording is over 2 MB. |
| <a id="transcription-failed"></a>`transcription_failed` | 502 | `/v1/transcribe`: the speech-to-text service could not read the recording. |
| <a id="database-unavailable"></a>`database_unavailable` | 503 | `/readyz` could not reach the database. |
| <a id="unavailable"></a>`unavailable` | 503 | The service is temporarily unavailable. |
| <a id="not-found"></a>`not_found` | 404 | No such route. |
| <a id="method-not-allowed"></a>`method_not_allowed` | 405 | The route exists but not for this HTTP method. |
| <a id="http-error"></a>`http_error` | varies | Any other HTTP error. |
| <a id="internal-error"></a>`internal_error` | 500 | A bug on our side. The cause is logged, never returned. |
