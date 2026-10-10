# Tool contracts

> This page is a translation of the [Japanese original](../ja/tools.md). If the two
> differ, the Japanese version is authoritative.

Tools are loaded dynamically from `${TOOL_ROOT}/**/*.py` based on YAML config files in
`${CONFIG_ROOT}/tools/`. Each YAML's file name stem becomes the tool's name, and its
`type` field is used to locate the matching `.py` file (`loaders/llm_tool_loader.py` /
`loaders/task_tool_loader.py` implement the details below).

## Tool search roots

When `type` is written as a file name, the roots are searched in this order:

1. `paths.tool_root` (the host)
2. each extension's `tool_roots()` (extension load order)
3. `lilla_core/builtin_tools` ([built-in tools](#built-in-tools); always last)

If the same `.py` name exists in more than one root, startup fails, because which one
wins would otherwise be implicit. The built-in tools are the one exception: they always
lose, so a file with the same name in the host or in an extension is used instead and
startup is not stopped (that is how you shadow a built-in tool). Duplicates inside a
single root keep using the first match.

Being on a search root is not the same as being enabled: a tool without a YAML is never
loaded, no matter which root it lives in.

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

## Tool sets (`tools.sets`)

Loading a tool from its YAML does not, on its own, make it visible to the LLM in a
normal conversation. Which tools the LLM sees in a normal conversation is decided by the
`main` set among the named tool sets written under `tools.sets` in `lilla.yaml`.

```yaml
tools:
  sets:
    main:            # tools the LLM sees in a normal conversation
      - llm_weather
      - $health
    health:          # set names other than main are up to you
      - llm_health_get
```

- Each entry of a set is a tool YAML stem, or a reference to another set written as `$`
  followed by the set name. `main` may include other sets, like `$health`
- Set names may only use ASCII letters, digits, `-` and `_`, and are case sensitive. The
  only reserved name is `main`. An empty name, or one containing `$` or other characters,
  makes startup fail
- When `main` is unset there are no tools. A missing `tools.sets`, a missing `sets.main`
  and `main: []` all mean the same thing (forgetting to write it never opens up every tool)
- Expansion follows the order of appearance, and duplicates are removed keeping the first
  occurrence. The order passed to the LLM is not this order but the tool load order
- A reference cycle (coming back to the same set while expanding it) makes startup fail.
  Several sets referencing the same set is sharing, not a cycle
- Every set is expanded and checked at startup, even one nothing references. An unknown
  set name or a tool name that is not loaded makes startup fail
- Filtering by `supported_client_type` happens after expansion. A set that mixes tools for
  different client types does not fail at startup; at run time only the tools usable by
  that client type are passed

The `available_tools` of a scheduled LLM task
([`available_tools`](scheduled-llm.md#the-tool-allow-list-available_tools)) and of
[`llm_expert`](#llm_expert-expert-agent) can reference a set as `$` followed by its name,
such as `$main` or `$health`.

The former `tools.main_available_tools` has been removed. If it is still present, startup
fails; move its entries to `tools.sets.main`.

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

`lilla_core` ships built-in tools (`lilla_core/builtin_tools`). They sit on a search root
just like an extension's tools, so `type` is written as a file name. None is enabled by
default; each one is opt-in, enabled only when you put its YAML under
`${CONFIG_ROOT}/tools/`.

| `type` | Kind | What it does |
|--------|------|--------------|
| `llm_current_datetime` | LLM | A sample that just returns the current date and time |
| `llm_conversation_get` | LLM | [Searching history by room name](history-search.md) |
| `task_channel_summary` | task | [Channel notes (nightly summary)](channel-notes.md) |
| `task_scheduled_llm` | task | [Scheduled notifications delegated to the LLM](scheduled-llm.md) |
| `llm_expert` | LLM | Delegation to an expert sub-agent (see below) |

### `llm_expert` (expert agent)

A tool that delegates work to a sub-agent with its own system prompt and toolset. The
implementation lives in the core; the prompt, the tools it may use and the LLM are written
in YAML (`${CONFIG_ROOT}/tools/llm_*.yaml`). You can add more YAML files to get several
experts from one implementation. Write the file name `llm_expert` as the `type`.

```yaml
type: llm_expert
description: Health data expert. Delegate questions about condition and exercise
prompt: dir:${config_root}/prompt/experts/health
llm_provider: grok            # optional
available_tools: [llm_health_get]   # tool set references such as `$main` work too
```

Combining `api: responses` with `grok_tools` makes a single call that uses the Responses
API's built-in tools (`api: responses` together with `available_tools` is not supported).
If the expert needs re-authentication, it aborts and passes that request up as it is.

Enabling the sample (`${CONFIG_ROOT}/tools/llm_current_datetime.yaml`):

```yaml
type: llm_current_datetime
```

The import-path form (`type: lilla_core.builtin_tools.llm_current_datetime`) still works,
so existing YAML keeps loading as before.

## Trusting tool directories

Anyone who can write to a directory tools are loaded from (`paths.tool_root`, each
extension's `tool_roots()`, `lilla_core/builtin_tools`, and `${CONFIG_ROOT}/tools`) can
run code inside the bot
process, so there is no separate allow-list for tool paths. The loaders only verify that
a resolved tool file still lies under one of those directories (a `type` containing
`..` or a symlink pointing outside is refused).
