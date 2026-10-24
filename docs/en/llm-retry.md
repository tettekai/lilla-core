# Automatic retries for LLM calls (`llm.providers.<key>.max_retries` and related keys)

> This page is a translation of the [Japanese original](../ja/llm-retry.md). If
> the two differ, the Japanese version is authoritative.

So that a conversation, a scheduled task, or `llm_expert` does not fail just because the
upstream API briefly returned high load (`503`) or a rate limit (`429`), or the connection
dropped for a moment, lilla-core **sends the same request again to the same concrete
provider**. This applies to every path: Chat Completions, the Responses API, and native Ollama.

**It is enabled by default** (up to 2 retries). To go back to the previous no-retry
behavior, set `max_retries: 0` on each provider.

```yaml
llm:
  providers:
    grok:
      type: openai_compat
      url: https://api.x.ai/v1
      model: example-model
      api_key_env: GROK_API_KEY
      max_retries: 2              # default 2; 0 disables retries
      retry_backoff_seconds: 1    # default 1 second
      retry_max_wait_seconds: 30  # default 30 seconds
      retry_on_timeout: false     # default false
```

Deciding to switch to a different provider (health checks or fallback) is not done here.
That is the job of a resolver script (see
[Choosing the LLM provider per turn](llm-resolver.md)).

## Keys

| Key | Default | Meaning |
|-----|---------|---------|
| `max_retries` | `2` | How many times to resend on a transient failure (integer, 0 or more). Together with the first attempt, at most `max_retries + 1` requests are sent |
| `retry_backoff_seconds` | `1` | Base wait in seconds before a retry (positive). Before the n-th retry it waits "base × 2^(n-1)" plus up to 50% jitter (with the defaults: 1–1.5 s before the first retry, 2–3 s before the second) |
| `retry_max_wait_seconds` | `30` | Upper bound for a single wait (positive). Computed waits are capped here. If the response's `Retry-After` exceeds it, the call fails without retrying |
| `retry_on_timeout` | `false` | Whether an attempt that exceeded `timeout_seconds` is also retried (`true` / `false`) |

Out-of-range values (negative `max_retries`, seconds of 0 or less) and wrong types (a float or
`true` for `max_retries`, a string for `retry_on_timeout`, etc.) fail startup with a
`ValidationError`. Only concrete providers (`type: ollama` / `type: openai_compat`) may set
these keys; setting them on a `type: resolver` entry fails startup. A turn that goes through a
resolver retries using the settings of the concrete provider the resolver chose.

## Which failures are retried

Only failures that can be judged transient are retried:

- HTTP status `429` / `500` / `502` / `503` / `504`
- Connection errors (could not connect, the response was cut off, and so on)
- Timeouts (exceeding `timeout_seconds`), only when `retry_on_timeout: true`

The following fail immediately without retrying:

- Any other `4xx` (`400` / `401` / `403` / `404`, etc.)
- Failure to parse the response JSON
- A refusal by the [LLM send blocklist](llm-send-guard.md) (the pre-send check runs only once, before the first attempt, and is not repeated on retries)

Timeouts are not retried by default because the upstream may have processed the first request
as well, so you may be charged twice. Also, each attempt waits up to `timeout_seconds`, so the
total wait grows a lot.

## Waits and `Retry-After`

If the response has a `Retry-After` header, that number of seconds is waited instead of the
computed wait (both the seconds form and the HTTP-date form are read; an unreadable value falls
back to the computed wait). If it exceeds `retry_max_wait_seconds`, the call fails at that
attempt without waiting.

Retries make a failing call slower by at most roughly
"`retry_max_wait_seconds` × `max_retries`" plus the time each attempt takes.

## Logging and usage records

- Each retry emits a WARNING log (provider name, status or exception type, which attempt comes
  next, and the wait).
- An HTTP error on an attempt that still has retries left is logged at WARNING, body included.
  An HTTP error on the last attempt is logged at ERROR as before. When it gives up after retrying,
  or when an attempt that still has retries left gets an HTTP error that is not retried (such as `401`), an ERROR log with the
  provider name and attempt count is emitted.
- If the call finally fails, `LLMError` is raised as before.
- [Token usage records](llm-usage.md) cover only the one successful attempt, and the duration is
  that attempt's. Failed attempts are not recorded.
