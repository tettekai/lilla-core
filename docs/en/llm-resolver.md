# Choosing the LLM provider per turn (`type: resolver`)

When an entry in `llm.providers` has `type: resolver`, a Python script provided by
the host picks the name of a concrete provider (`openai_compat` / `ollama`) on every
turn that selects that key. The combinations themselves (the same `model` with a
different `extra_params.reasoning_effort`, for example) are expressed as concrete
provider entries; the resolver only picks one of those keys.

```yaml
llm:
  default: router
  providers:
    router:
      type: resolver
      script: ${config_root}/llm_resolver.py
      fallback: deepseek-flash-high
      # timeout_seconds: 10   # upper bound for an async def resolve (default 10 s)
    deepseek-flash-low:
      type: openai_compat
      url: "https://api.deepseek.com"
      model: "deepseek-flash"
      api_key_env: "DEEPSEEK_API_KEY"
      extra_params:
        reasoning_effort: low
    deepseek-flash-high:
      type: openai_compat
      url: "https://api.deepseek.com"
      model: "deepseek-flash"
      api_key_env: "DEEPSEEK_API_KEY"
      extra_params:
        reasoning_effort: high
```

## Resolution order

The key is chosen exactly as before:

1. The `llm_name` passed by the caller (a task YAML, for instance)
2. The `!model` override
3. `llm.default`

If that key has `type: resolver`, the script is called and the returned name is
looked up again. A concrete provider is called directly. `!model router` means
per-turn selection; `!model deepseek-flash-high` pins a provider. A task that
writes `llm_name: router` goes through the resolver; a concrete name does not.

The expansion happens once inside `run_conversation` (after the history is built,
before the LLM is called), so Discord, extension clients and tasks all follow the
same order. When a resolver name reaches `chat_to_llm` without going through
`run_conversation` (`!selftest`, or a task that calls `chat_to_llm` directly),
there is no context to pass, so the script is not called and `fallback` is used.
`ConversationContext.llm_name` in conversation start hooks carries the key before
expansion.

## Fields

| Field | Required | Meaning |
|-------|----------|---------|
| `script` | Yes | The Python file defining `resolve`. `${config_root}` is expanded, a `file:` prefix is optional, relative paths are relative to `CONFIG_ROOT`. `dir:` is not allowed |
| `fallback` | Yes | A concrete provider name in `llm.providers` (not a resolver) |
| `timeout_seconds` | No | Upper bound in seconds for an `async def resolve` (default 10) |

A resolver makes no HTTP calls, so `url` / `model` / `api_key_env` / `wakeup_file` /
`extra_params` are not allowed on it. Conversely, concrete providers require `url`
/ `model` and may not set `script` / `fallback`. The Ollama wake-up (WOL) applies
only to concrete providers.

Startup fails (fail-fast) when:

- `script` resolves outside `CONFIG_ROOT` (including via `..` or a symlink pointing out)
- the `script` file is missing, fails to import, or has no `resolve`
- `fallback` is not in the table, or is itself a resolver
- the fields above do not match the type

`CONFIG_ROOT` has the same trust level as extension modules (see
[`SECURITY.md`](../../SECURITY.md)). The script is trusted code running in the bot's
process, not a sandbox.

## Function contract

```python
def resolve(ctx) -> str | None: ...
# or
async def resolve(ctx) -> str | None: ...
```

- The function name is fixed to `resolve`. A plain `def` runs directly on the event
  loop, so write heavy work (such as calling a judge model) as `async def`
- Return a concrete provider name to use it; return `None` to use `fallback`
- An unknown name, another resolver's name (depth is limited to 1), a non-string,
  an exception or a timeout logs a warning and falls back to `fallback`. The
  conversation keeps going

`ctx` is `lilla_core.services.llm_resolver.LlmResolveContext` (a frozen dataclass).
Adding fields is non-breaking.

| Field | Meaning |
|-------|---------|
| `client_type` | `"discord"` / `"task"` / a kind added by an extension |
| `resolver_name` | The resolver key being resolved |
| `fallback` | That entry's `fallback` |
| `provider_names` | The names in `llm.providers` (resolvers included) |
| `discord_channel_id` | The channel ID for Discord conversations (`None` otherwise) |
| `channel_name` | The `name` from `discord.channels` if the channel is registered |
| `user_text` | Text of the latest user message (timestamp removed, truncated to 500 characters) |
| `has_image` | `True` only when this turn's user message has an image attachment passed to the LLM as an image part |

The full system prompt, tool results and image data (data URLs, bytes) are never
passed. An image in an earlier turn of the history does not make `has_image`
`True`. Which key to choose for images is up to the script; the core keeps no
per-provider vision table.

`user_text` is the user's own words. If you pass it to a judge model, frame it as
data rather than instructions (prompt-injection hygiene).

## Template

A copy-ready sample ships as `lilla_core/templates/llm_resolver.py`:

```bash
cp "$(python -c 'import lilla_core, pathlib; print(pathlib.Path(lilla_core.__file__).parent / "templates" / "llm_resolver.py")')" \
   "$CONFIG_ROOT/llm_resolver.py"
```

By default it returns `None` and lets `fallback` decide. Fill in
`CHANNEL_PROVIDERS` (registered channel name → key) and `VISION_PROVIDER` (the key
for turns with images) to enable those branches. A judge-model example is included
as a comment.
