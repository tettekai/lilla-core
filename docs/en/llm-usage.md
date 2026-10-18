# Recording LLM token usage

> This page is a translation of the [Japanese original](../ja/llm-usage.md). If the two
> differ, the Japanese version is authoritative.

Every time the core calls an LLM, it records the token usage reported in the response as
one document in the MongoDB `llm_usage` collection. The observability dashboard's
**Usage** page (`#/usage`) aggregates it by day, provider and entry point, and Home shows
the total for the last 24 hours.

The point is to tune things like a scheduled task's `max_tool_call_iterations`, the
history length, or resolver-based provider switching against real numbers.

## Configuration

Configure it under `llm.usage_log` in `lilla.yaml`. Omitting the section means "record,
keep for 90 days".

```yaml
llm:
  usage_log:
    enabled: true        # false stops recording (default true)
    retention_days: 90   # retention in days (1 or more; default 90)
```

- `retention_days` is written into each record's `expire_at`, and a TTL index deletes it.
  Changing the value applies to records written after the change (existing records expire
  with the value they were written with)
- `enabled: false` does not delete existing records; the Usage page still shows them

### Estimated cost (optional)

If you set per-million-token prices on a concrete provider (`ollama` / `openai_compat`),
the Usage page and Home show an estimated cost. No currency is assumed; the figure is in
whatever unit you used for the prices. Resolver entries cannot have prices (startup fails).

```yaml
llm:
  providers:
    grok:
      type: openai_compat
      url: "https://api.x.ai/v1"
      model: "grok-4.3"
      api_key_env: "GROK_API_KEY"
      input_price_per_million_tokens: 1.25   # per million input tokens (0 or more)
      output_price_per_million_tokens: 2.5   # per million output tokens (0 or more)
```

- The cost is computed with **the price at the time of recording** and stored in the
  record. Adding a price later does not add a cost to earlier records
- If you set only one of the two prices, the other side counts as 0. A record whose
  response lacked the token count for a priced side has an unknown cost
- **The estimated cost is a guide based on the prices you configured and will not match
  your actual bill** (cache discounts, reasoning tokens, exchange rates and so on differ
  per provider)

## What is recorded

One record per LLM call, on every path (Chat Completions, with tools, Responses API, Ollama
native). Calls whose request failed are not recorded.

| Field | Content |
|-------|---------|
| `created_at` / `expire_at` | When it was recorded, and when the TTL removes it |
| `provider` / `provider_type` / `model` | Key, type and model of the concrete provider actually used (the fallback when a resolver name is called directly) |
| `api` | `chat` / `tools` / `responses` |
| `entry` | Entry-point label (see below) |
| `client_type` / `discord_channel_id` / `task_name` / `tool_name` / `source` | Breakdown of the entry point (whatever is known) |
| `duration_ms` | Time from sending the HTTP request to parsing the response |
| `input_tokens` / `output_tokens` / `total_tokens` | Input, output and total tokens. `null` if the response did not report them (never estimated). The sum is used as the total only when the total is missing and both input and output are present |
| `usage_known` | Whether the total token count is known |
| `raw_usage` | The response's `usage` (for Ollama native, numeric fields such as `prompt_eval_count`) reduced to numbers only. Reasoning/cached token breakdowns stay here |
| `cost` | Estimated cost; `null` when there is no price or it cannot be computed |

**Message text, prompts, and tool arguments or results are never recorded.**

The response fields read for usage are:

| Path | Input | Output | Total |
|------|-------|--------|-------|
| Chat Completions (including with tools) | `usage.prompt_tokens` | `usage.completion_tokens` | `usage.total_tokens` |
| Responses API | `usage.input_tokens` | `usage.output_tokens` | `usage.total_tokens` |
| Ollama native (`/api/chat`) | `prompt_eval_count` | `eval_count` | (input + output) |

## Entry point (`entry`)

"Where the call came from" is passed through `contextvars`
(`lilla_core.core.llm_call_origin`), not through extra arguments on the public functions.
The label uses the innermost known context.

| Label | Meaning |
|-------|---------|
| `tool:<YAML stem>` | A call made inside an LLM tool (such as `llm_expert`; the innermost tool when nested) |
| `task:<YAML stem>` | A call inside a scheduled task (APScheduler / `!runtask` / `POST /api/runtask`) |
| `selftest` / `toolresult` | The LLM check of `!selftest full`, and reply generation in `!toolresult` |
| `conversation:<client_type>` | A regular conversation (`run_conversation`; `discord` or a type added by an extension) |
| `unknown` | Anything else |

An extension that wants its own entry point can push one before calling the LLM, e.g.
`with llm_call_origin(source="my-ext"):` (fields passed as `None` keep the outer value).

## Writes and failures

Records are written as a background asyncio task so the call itself never waits. If a
write fails, only a WARNING (`Failed to record LLM usage: ...`) is logged and the LLM call
still counts as successful.

## Dashboard

- **Usage** (`#/usage`, `GET /api/usage?days=N`): the last N days including today
  (default 7, 1–366), counted in calendar days of `ui.timezone`. Shows totals and per-day,
  per-provider (with model) and per-entry-point call counts, input/output/total tokens,
  average duration and estimated cost. Calls with unknown token counts are counted as
  "unknown" without tokens
- **Home**: one line with the call count and total tokens for the last 24 hours (plus the
  estimated cost if prices are set)

Both return aggregates only, never individual records.
