# Searching history by room name

> This page is a translation of the [Japanese original](../ja/history-search.md). If the
> two differ, the Japanese version is authoritative.

Conversation history itself stays global — it is never split per channel — but the core
ships a built-in LLM tool for recalling "what was said in that room". It is not enabled
by default; opt in by adding this YAML under `${CONFIG_ROOT}/tools/` (the name the LLM
sees is the YAML's file name):

```yaml
type: lilla_core.builtin_tools.llm_conversation_get
```

- It narrows by `datetime_range` (`today`, `last_7_days`, `2026-04-20/2026-04-26`, ...),
  `query` (space-separated AND keywords), `role` (`user` / `assistant` / `all`) and
  `limit` (default 30, capped at 30)
- Passing a `channel_name` registered in `discord.channels` narrows the search to that
  channel. **It is the alias from the config, not the channel's current Discord name**
  ([Registered channels](channels.md))
- Omitting `channel_name` searches across every channel, as before
- An unregistered name returns an error — it never silently falls back to a global search
- Names match exactly after stripping surrounding whitespace, and are case sensitive
- Both the range boundaries and the timestamps in the results use `ui.timezone`
  ([Timezone](timezone.md))

See [Tool contracts](tools.md) for how LLM tools work in general.
