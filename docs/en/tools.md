# Tool contracts

> This page is a translation of the [Japanese original](../ja/tools.md). If the two
> differ, the Japanese version is authoritative.

Tools are loaded dynamically from `${TOOL_ROOT}/**/*.py` based on YAML config files in
`${CONFIG_ROOT}/tools/`. Each YAML's file name stem becomes the tool's name, and its
`type` field is used to locate the matching `.py` file (`loaders/llm_tool_loader.py` /
`loaders/task_tool_loader.py` implement the details below).

## Tool YAML shipped by extensions

An extension can ship default YAML files through `tool_config_roots()`. The loader
collects YAML from those directories first (in extension load order) and then from
`${CONFIG_ROOT}/tools`; a file with the same stem in `${CONFIG_ROOT}/tools` replaces the
bundled one wholesale (no merging), so your settings always win. Two extensions bundling
the same stem fail at startup. To switch a bundled tool off, put a YAML with the same
stem in `${CONFIG_ROOT}/tools` containing `enabled: false`; `enabled: false` disables any
tool YAML, bundled or not.

## LLM tools

`${CONFIG_ROOT}/tools/llm_*.yaml`, implemented in `${TOOL_ROOT}/**/<type>.py`.

- The `.py` file must define a module-level `SCHEMA` (an OpenAI tools-format function
  schema dict), or a `build_schema(config: dict) -> dict` function if the schema needs
  to be built dynamically from the YAML config (checked first, before `SCHEMA`).
- It must also define `async def execute(tool_input: dict, context: dict) -> dict`,
  returning the standard result dict (`success` / `tool_name` / `data` / `error`, etc. —
  see `tool_support/tool_result.py` for the helper constructors).
- If the YAML's `type` is `self`, the loader skips searching `TOOL_ROOT` and instead
  loads the `.py` file next to the YAML with the same stem.
- If the YAML's `type` contains a dot (`type: lilla_google_calendar.tools.calendar_get`),
  it is an **import path**: the loader imports that module with `importlib` instead of
  searching the tool roots. This is how an installed package (for example an extension
  published on PyPI) ships its tools. The module is a regular import: it is registered
  in `sys.modules` under its dotted name (a stem-resolved file is executed as a
  standalone module and is not), so relative imports inside it work and no directory
  check applies. An import failure is logged as a warning and only that tool is
  skipped, like a missing file.
- The YAML may set `description` (overrides the schema's description),
  `supported_client_type` (defaults to `"all"`), a `cache` block, and other
  tool-specific keys — the latter must not collide with the runtime context keys
  below (checked at startup; a collision raises at load time).

## Task tools

`${CONFIG_ROOT}/tools/task_*.yaml`, implemented in `${TOOL_ROOT}/**/<type>.py`.

- The `.py` file must define a class, instantiated as `cls(config, name)` where
  `config` is the YAML dict (with `_yaml_path` added) and `name` is the YAML stem.
- The instance is expected to expose `description`, `category`, and `schedule` (a cron
  expression string) attributes, plus `async def execute(context: dict) -> None`.
- `schedule` is optional — a tool without it is not registered with the scheduler, but
  can still be run manually via `!runtask`.
- `type` accepts an import path here too (`type: some_package.tasks.daily_summary`); the
  class is looked up in the imported module the same way as in a file. The trigger kind
  comes from the YAML's file name, not from `type`, so an import-path task tool is still
  a `task` tool and can be run with `!runtask`.

## The `context` passed to `execute`

It varies by call site. For LLM tools it always includes `client_type` and a nested-call
helper `call_tool(tool_name, tool_input)`, plus any tool-specific keys from that tool's
YAML, any keys contributed by an extension's `tool_context_providers()`, and
`client_state` when the calling client passed one to `run_conversation()`. Task tools get
the same extension-provided keys, plus `discord_client` / `now` / `llm_tools` on a
scheduled run and additionally `params` on a manual `!runtask` run. The core-owned keys
of both kinds are reserved: an extension whose `tool_context_providers()` returns one of
them fails at load instead of being silently overwritten.

## Built-in tools

`lilla_core` ships built-in tools as concrete examples of the import-path form. None is
enabled by default; each one is opt-in, enabled only when you put its YAML under
`${CONFIG_ROOT}/tools/`.

| Module | Kind | What it does |
|--------|------|--------------|
| `lilla_core.builtin_tools.llm_current_datetime` | LLM | A sample that just returns the current date and time |
| `lilla_core.builtin_tools.llm_conversation_get` | LLM | [Searching history by room name](history-search.md) |
| `lilla_core.builtin_tools.task_channel_summary` | task | [Channel notes (nightly summary)](channel-notes.md) |

Enabling the sample (`${CONFIG_ROOT}/tools/llm_current_datetime.yaml`):

```yaml
type: lilla_core.builtin_tools.llm_current_datetime
```

## Trusting tool directories

Anyone who can write to a directory tools are loaded from (`paths.tool_root`, each
extension's `tool_roots()`, and `${CONFIG_ROOT}/tools`) can run code inside the bot
process, so there is no separate allow-list for tool paths. The loaders only verify that
a resolved tool file still lies under one of those directories (a `type` containing
`..` or a symlink pointing outside is refused).
