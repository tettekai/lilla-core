<img src="docs/lilla-logo.svg" alt="Lilla" width="160" />

# lilla-core

The core runtime of Lilla. A general-purpose foundation library for building AI agents.

It runs as a Discord bot, receives messages, calls an LLM (Ollama / OpenAI-compatible),
and provides the "skeleton of an agent": the tool_call loop, command processing, and
replies.

Character settings, domain-specific tools (integrations with external services, etc.),
and purpose-specific HTTP/dashboard servers — anything that varies per user — are kept
out of the core. Such elements are added from the outside by subclassing `Extension`
(`core/extension.py`) and listing the module in the `LILLA_EXTENSIONS` environment
variable, which is loaded at startup.

lilla-core is designed to be able to start as a standalone Discord bot with zero
extensions loaded. Every `Extension` method has a safe default that contributes
nothing, so the core never depends on the presence of extensions.

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
- `Extension` adapter class for customization without modifying the core, with any
  number of extensions loaded per process (extra repositories, message hooks, startup
  work, result delivery, client-specific prompts, conversation-start hooks, tool
  execution context, extra tool roots, and extra command packages)
- User-facing Discord text is pulled from locale catalogs (`ja` / `en`)
- One configurable timezone (`ui.timezone`) for schedules, "today", and the
  current time shown to the LLM

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

`lilla.yaml` must define at least one entry under `llm.providers`, and `llm.default`
must match one of those provider names — otherwise startup fails with a
`ValidationError`.

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

To start with extensions loaded, set the `LILLA_EXTENSIONS` environment variable to a
comma-separated list of module import paths (omit it, or leave it empty, to start with
the core alone). Modules are loaded in the order given.

```bash
export LILLA_EXTENSIONS=my_extension_package,another_pack
python -m lilla_core.bot
```

### Timezone

`ui.timezone` in `lilla.yaml` decides the clock the bot treats as "now" and "today"
for humans. The crontab expressions of scheduled tasks, the current time embedded in
the system prompt, the conversation-history window, and relative date ranges such as
`today` all follow it.

- Omitted (or YAML `null`): the OS local timezone of the process.
- An IANA name such as `Asia/Tokyo`: that timezone only. The OS timezone is ignored.
- An empty string, or a name `zoneinfo` does not accept: startup fails. There is no
  fallback, so a typo cannot silently shift every date by a day.

In `utils/datetime_utils.py`, `local_timezone()`, `local_now()`, `to_jst_date()` and
`jst_day_end_utc()` all resolve to that same timezone on every call, so the wall clock
and the calendar date never disagree. The `JST` constant is the one exception: it stays
at UTC+9 no matter what `ui.timezone` says, for code that needs Japan time explicitly.

> **Note:** container images usually run with their OS timezone set to UTC. If you
> leave `ui.timezone` unset there, cron schedules and "today" are UTC as well. Set it
> explicitly whenever the dates matter.

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
├── core/                 # Config management, the Extension base class, shared utilities
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

## Extensions

An extension subclasses `lilla_core.core.extension.Extension`, overrides only the
methods it needs, and is exported from its module as a single `extension` attribute.
Every method has a safe default that contributes nothing, so the core keeps working on
its own.

```python
from lilla_core.core.extension import Extension


class MyExtension(Extension):
    name = "my-extension"

    def tool_context_providers(self):
        return {"my_client": get_my_client}

    async def setup(self, ctx):
        await start_my_http_server(ctx.llm_tools, ctx.tools, ctx.bot)


extension = MyExtension()
```

| Method | Purpose |
|----------|------|
| `startup_repos` | Extra repository factories to initialize on `on_ready` |
| `on_message` | Invoked at the start of `on_message`; return `True` to stop further handling |
| `setup` | Startup work awaited before `bot.start()` (e.g. an HTTP server). Receives one `SetupContext` (`tools`, `llm_tools`, `bot`, `config`); new fields may be added without breaking existing extensions |
| `result_deliveries` | Where `!toolresult` delivers results, per `client_type` |
| `client_prompt_providers` | System prompt additions per `client_type`, as `{client_type: [provider, ...]}`. Additive: several extensions may target the same `client_type`; the core concatenates them in load order, separated by blank lines |
| `conversation_start_hooks` | Async hooks run before conversation handling starts, as `{client_type: [hook, ...]}`. Additive like the prompts. Each hook receives one `ConversationContext` (`client_type`, `client_state`, `discord_channel_id`, `llm_name`) |
| `tool_context_providers` | Values injected into the tool execution context |
| `tool_roots` | Extra directories searched for tool `.py` files |
| `command_packages` | Extra packages scanned for `@register_command` handlers |
| `config_models` | YAML sections this extension adds to `AppConfig` |
| `env_fields` | Secret fields this extension adds to `cfg.env` |
| `required_config_sections` | YAML sections this extension reads but does not provide |
| `required_env_fields` | `cfg.env` fields this extension reads but does not provide |
| `required_tool_context_keys` | Tool context keys this extension's tools read but does not provide |
| `requires` (class attribute) | Names of extensions this one depends on; they must be loaded and listed earlier in `LILLA_EXTENSIONS` |
| `api_version` (class attribute) | The `Extension` contract version this extension was written against. Defaults to the core's current `EXTENSION_API_VERSION`; an unsupported value fails at load |

Contribution keys must not collide **between extensions**: duplicate `Extension.name`,
config section names, env field names, tool context keys, result-delivery `client_type`
keys, command names, or tool file names across different roots all fail fast at startup
rather than silently picking a winner. Client prompts and conversation start hooks are
the exception by design: they are lists per `client_type` and are concatenated in load
order, so several extensions can contribute to the same client.
`client_type="discord"` is special in two ways: the core provides a built-in system
prompt that is used only when no extension contributes one, and the core owns
`!toolresult` delivery, so extensions cannot register a result delivery for it.

A client extension that drives `run_conversation()` itself may pass any object as
`client_state` (for example its set of connected sockets). The core does not interpret
it: it is exposed to hooks as `ConversationContext.client_state` and to LLM tools as
the `client_state` context key.

### Config composition

An extension declares the config it adds, and the core composes one Pydantic model out
of every declaration at startup. Writing an `AppConfig` subclass by hand and swapping it
in via `set_config()` as an import side effect is no longer part of the contract: the
composed instance replaces anything an extension sets during import, so the order of
`LILLA_EXTENSIONS` does not affect config.

```python
class GoogleConfig(BaseModel):
    client_id: str | None = None
    redirect_uri: str = "http://localhost/google-callback"


class MyExtension(Extension):
    name = "my-extension"

    def config_models(self):
        return {"google": GoogleConfig}

    def env_fields(self):
        return {"google_client_secret": "GOOGLE_CLIENT_SECRET"}
```

`get_config().google.client_id` and `get_config().env.google_client_secret` are then
readable process-wide.

- A section is **required** when its model has at least one required field, and optional
  otherwise. A required section missing from `lilla.yaml` fails at startup.
- Composed env fields are always `str | None` with a default of `None`. The contract
  carries no type, so required or non-string secrets cannot be expressed this way.
- Providing a section name or an env field name twice fails fast, even when the two
  models are identical. Core-owned names are reserved as well.
- `required_config_sections()` lists sections the extension reads but does not provide,
  such as a shared `google:` section owned by another pack. `required_env_fields()` and
  `required_tool_context_keys()` do the same for `cfg.env` fields and tool context keys.
  If nothing provides a required name (and it is not a core-owned one), the load fails
  and names the extension that asked for it.

### Dependencies between extensions

An extension declares the extensions it depends on in the `requires` class attribute.
The core checks at load time that every name is loaded **and listed before** the
dependent extension in `LILLA_EXTENSIONS`; it never reorders them, because message
hooks, `setup()` and tool roots all run in load order.

```python
class GoogleCalendarExtension(Extension):
    name = "lilla-google-calendar"
    requires = ("lilla-google-oauth",)

    def required_config_sections(self):
        return ["google"]

    def required_env_fields(self):
        return ["google_client_secret"]

    def required_tool_context_keys(self):
        return ["google_client"]
```

`requires` answers "is the other pack loaded, in the right order?"; the `required_*`
methods answer "does somebody provide the specific thing I read?". There is no generic
`validate()` hook: run-time checks belong in `setup()`.

Modules listed in `LILLA_EXTENSIONS` run as **trusted code** in the same process. This
is not a sandbox. The same applies to every directory tools are loaded from:
`paths.tool_root`, each extension's `tool_roots()`, and `${CONFIG_ROOT}/tools`. Anyone
who can write there can run code inside the bot process, so there is no separate
allow-list for tool paths. The loaders only verify that a resolved tool file still lies
under one of those directories (a `type` containing `..` or a symlink pointing outside
is refused).

### Extension contract compatibility

`lilla_core.core.extension.EXTENSION_API_VERSION` is the version of the `Extension`
contract this core provides, and `SUPPORTED_EXTENSION_API_VERSIONS` is the set it
accepts at load time. An extension may pin the version it was written against:

```python
from lilla_core.core.extension import EXTENSION_API_VERSION, Extension


class MyExtension(Extension):
    name = "my-extension"
    api_version = 1  # or leave the default, which is EXTENSION_API_VERSION
```

If the declared version is not supported, `load_extensions()` fails and names the
extension and both versions, instead of loading an extension written against an older
contract and breaking later at run time.

The policy for changing the contract:

- **Non-breaking, no version bump**: adding a method to `Extension` (always with a
  default that contributes nothing), adding a field to a `*Context` dataclass
  (`SetupContext`, `ConversationContext`), adding a new lookup function or a new
  core-owned context key.
- **Breaking, bumps `EXTENSION_API_VERSION`**: changing a method's signature or the shape
  of its return value, removing or renaming a method, a `*Context` field, a context key
  or a lookup function, or changing when a hook is called. Such changes are recorded
  under **BREAKING** in `CHANGELOG.md`.
- Extension packages should depend on a version range of `lilla-core`
  (for example `lilla-core>=0.3,<0.4`) so a breaking core release is not picked up
  silently.

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
plus any tool-specific keys from that tool's YAML, any keys contributed by an
extension's `tool_context_providers()`, and `client_state` when the calling client
passed one to `run_conversation()`. For task tools, a scheduled run passes
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
