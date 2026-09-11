<img src="docs/lilla-logo.svg" alt="Lilla" width="160" />

# lilla-core

The core runtime of Lilla. A general-purpose foundation library for building AI agents.

It runs as a Discord bot, receives messages, calls an LLM (Ollama / OpenAI-compatible),
and provides the "skeleton of an agent": the tool_call loop, command processing, and
replies.

Character settings, domain-specific tools (integrations with external services, etc.),
and purpose-specific HTTP/dashboard servers — anything that varies per user — are kept
out of the core. Such elements can be added from the outside through extension points
and the `LILLA_EXTENSIONS_MODULE` environment variable, which is loaded at startup.

lilla-core is designed to be able to start as a standalone Discord bot even with no
extensions registered at all. Each extension point falls back to safe default behavior
when nothing is registered, so the core never depends on the presence of extensions.

## Constraints

- Conversation history, `runtime_state` (the active LLM provider, tools-disabled flag),
  and session memory are **all shared process-wide**, not scoped per channel or per
  user. `repository/conversation_repository.py` does not separate history by channel
  or user, so conversations from different channels are mixed into a single history.
  lilla-core is built around a single owner talking to one bot process; it is not
  designed for multi-user or multi-tenant use.

## Features

- Runs as a Discord bot out of the box (works with no extensions)
- Switch between multiple LLM providers via config: Ollama (with Wake-on-LAN support)
  and OpenAI-compatible APIs
- tool_call loop, plus management of conversation history, user memos, and session
  memory
- Plugin-style `!` commands (just drop a file into `commands/`)
- Scheduled tasks via APScheduler (`task_*.yaml`)
- Approval flow (commands from non-owners can be approved/rejected on Discord)
- Seven extension points for customization without modifying the core
  (extra repositories, message hooks, startup tasks, result delivery, client-specific
  prompts, conversation-start hooks, and tool execution context)
- User-facing Discord text is pulled from locale catalogs (`ja` / `en`)

## Requirements

- Python 3.12 or later
- MongoDB (used to persist conversation history, user memos, etc.)
- A Discord bot token

## Setup

```bash
pip install -e ".[dev]"

export CONFIG_ROOT=./config.example
export DISCORD_TOKEN=...
export MONGODB_URI=mongodb://xxx

python -m lilla_core.bot
```

The directory pointed to by `CONFIG_ROOT` should contain `lilla.yaml` (non-secret
structural config) and, if needed, `logging.yaml`. See `config.example/` for a sample.

> **Privacy note:** `config.example/logging.yaml` sets the root logger to `DEBUG`.
> At that level, `core/http_util.py` logs request/response bodies to stdout, which
> can include LLM request bodies (i.e. the system prompt and conversation history).
> Keep this in mind when running with the example logging config, especially if
> stdout is collected somewhere.

Secrets (tokens, connection strings, etc.) are read from environment variables or
`.env` (see `.env.example`). OS environment variables take precedence over `.env`.

For local development, values in `.env` are also written into the process
environment at startup (any variable you already export takes precedence). This
covers LLM provider API keys as well (e.g. `GROK_API_KEY`, or whatever name you set
via `api_key_env` in `lilla.yaml`), which are read directly from the environment by
`api/llm_client.py` rather than through `AppConfig`.

To start with extensions registered, set the `LILLA_EXTENSIONS_MODULE` environment
variable to the import path of your extension module (omit it to start with the core
alone).

```bash
export LILLA_EXTENSIONS_MODULE=my_extension_package
python -m lilla_core.bot
```

### Discord bot setup

In the [Discord Developer Portal](https://discord.com/developers/applications), under
your application's **Bot** page, configure the following before inviting the bot.

**Privileged Gateway Intents**

- **Message Content Intent** — turn this ON. `bot_client.py` requests
  `intents.message_content = True`; without it, Discord delivers messages with an empty
  body, so the bot can't read what was said and never responds.

No other privileged intent (Server Members / Presence) is required — the core doesn't
use guild member or presence data.

**Permissions for the bot invite URL**

When generating the invite URL (OAuth2 URL Generator, `bot` scope), grant at least:

- **View Channels** — to see the channels it needs to read and post in
- **Send Messages** — replies, command output, and approval requests
  (`message.reply()` / `channel.send()` throughout `commands/` and `handlers/`)
- **Read Message History** — `cleardirty` resolves prior messages with
  `channel.fetch_message()`
- **Attach Files** — `selftest` and the approval flow send file attachments with
  `discord.File`

If Message Content Intent is left off, the bot will appear to receive messages but
`message.content` will be empty, so it silently fails to respond. If any of the above
permissions are missing, expect send/reply failures or the bot being unable to see the
channels it needs.

## Testing

```bash
pytest tests/
```

## Directory layout (overview)

```
src/lilla_core/
├── bot.py                # Discord bot entry point
├── bot_client.py         # Shared module holding the commands.Bot instance
├── core/                 # Config management, extension points, shared utilities
├── commands/             # `!command` implementations (one file per command)
├── handlers/             # Dispatch for Discord events/commands, scheduled tasks
├── services/             # tool_call loop, conversation history, system prompt build
├── ui/ locales/          # Catalogs of user-facing Discord text (ja / en)
├── utils/                # Shared utilities for dates, paths, hashing, etc.
├── loaders/               # Dynamic loading of tools (llm_*.yaml / task_*.yaml)
├── api/                  # LLM client (Ollama / OpenAI-compatible)
├── repository/           # Data persistence to MongoDB
└── tool_support/         # Opt-in helpers for tool implementations
tests/                    # Unit tests (pytest)
config.example/           # Sample lilla.yaml / logging.yaml
```

Detailed design notes and the role of each file are documented in
[`CLAUDE.md`](./CLAUDE.md).

## Extension points

Seven extension points let you plug in functionality from outside the core. Each has a
safe default that keeps the core working on its own when nothing is registered.

| Registration function | Purpose |
|----------|------|
| `register_startup_repo` | Extra repository factories to initialize on `on_ready` |
| `register_message_hook` | A handler always invoked at the start of `on_message` |
| `register_startup_task` | Startup tasks awaited before `bot.start()` (e.g. an HTTP server) |
| `register_result_delivery` | Overrides where `!toolresult` delivers results, per `client_type` |
| `register_client_prompt_provider` | System prompt additions per `client_type` |
| `register_conversation_start_hook` | Client-specific preprocessing before conversation handling starts |
| `register_tool_context_provider` | Injects values into the tool execution context |

Extensions can add functionality without modifying core code, by defining an
`AppConfig` subclass and swapping it in via `set_config()`, and by calling the
registration functions in `core/extension_points.py`.

## Tool contracts

Tools are loaded dynamically from `${TOOL_ROOT}/**/*.py` based on YAML config files in
`${CONFIG_ROOT}/tools/`. Each YAML's file name stem becomes the tool's name, and its
`type` field is used to locate the matching `.py` file (`loaders/llm_tool_loader.py` /
`loaders/task_tool_loader.py` implement the details below).

**LLM tools** (`${CONFIG_ROOT}/tools/llm_*.yaml`, implemented in
`${TOOL_ROOT}/**/<type>.py`):

- The `.py` file must define a module-level `SCHEMA` (an OpenAI tools-format function
  schema dict), or a `build_schema(config: dict) -> dict` function if the schema needs
  to be built dynamically from the YAML config (checked first, before `SCHEMA`).
- It must also define `async def execute(tool_input: dict, context: dict) -> dict`,
  returning the standard result dict (`success` / `tool_name` / `data` / `error`, etc. —
  see `tool_support/tool_result.py` for the helper constructors).
- If the YAML's `type` is `self`, the loader skips searching `TOOL_ROOT` and instead
  loads the `.py` file next to the YAML with the same stem.
- The YAML may set `description` (overrides the schema's description),
  `supported_client_type` (defaults to `"all"`), a `cache` block, and other
  tool-specific keys — the latter must not collide with the runtime context keys
  below (checked at startup; a collision raises at load time).

**Task tools** (`${CONFIG_ROOT}/tools/task_*.yaml`, implemented in
`${TOOL_ROOT}/**/<type>.py`):

- The `.py` file must define a class, instantiated as `cls(config, name)` where
  `config` is the YAML dict (with `_yaml_path` added) and `name` is the YAML stem.
- The instance is expected to expose `description`, `category`, and `schedule` (a cron
  expression string) attributes, plus `async def execute(context: dict) -> None`.
- `schedule` is optional — a tool without it is not registered with the scheduler, but
  can still be run manually via `!runtask`.

**The `context` dict passed to `execute`** varies by call site. For LLM tools it always
includes `client_type` and a nested-call helper `call_tool(tool_name, tool_input)`,
plus any tool-specific keys from that tool's YAML, and any keys registered via
`register_tool_context_provider`. For task tools, a scheduled run passes
`discord_client` / `now` / `llm_tools`, and a manual `!runtask` run additionally passes
`params`.

## Contributing

Issues and pull requests are welcome. See [`CONTRIBUTING.md`](./CONTRIBUTING.md) for
setup, tests, branch/PR rules, and commit conventions. Detailed design and development
rules live in [`CLAUDE.md`](./CLAUDE.md).

## Changelog

See [`CHANGELOG.md`](./CHANGELOG.md) for a history of notable changes.

## License

MIT
