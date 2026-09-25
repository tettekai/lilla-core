# Extension basics

> This page is a translation of the [Japanese original](../ja/extensions.md). If the two
> differ, the Japanese version is authoritative.

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

Load the module by listing it in `LILLA_EXTENSIONS`
([Install and run](getting-started.md#loading-extensions)).

## Methods

| Method | Purpose |
|--------|---------|
| `startup_repos` | Extra repository factories to initialize on `on_ready` |
| `on_message` | Invoked at the start of `on_message`; return `True` to stop further handling |
| `setup` | Startup work awaited before `bot.start()` (e.g. an HTTP server). Receives one `SetupContext` (`tools`, `llm_tools`, `bot`, `config`); new fields may be added without breaking existing extensions |
| `result_deliveries` | Where `!toolresult` delivers results, per `client_type` |
| `client_prompt_providers` | System prompt additions per `client_type`, as `{client_type: [provider, ...]}`. Additive: several extensions may target the same `client_type`; the core concatenates them in load order, separated by blank lines |
| `conversation_start_hooks` | Async hooks run before conversation handling starts, as `{client_type: [hook, ...]}`. Additive like the prompts. Each hook receives one `ConversationContext` (`client_type`, `client_state`, `discord_channel_id`, `llm_name`) |
| `tool_context_providers` | Values injected into the tool execution context |
| `tool_roots` | Extra directories searched for tool `.py` files |
| `tool_config_roots` | Directories of default tool YAML files (`llm_*.yaml` / `task_*.yaml`) shipped by the extension. A YAML with the same stem in `${CONFIG_ROOT}/tools` replaces it wholesale; put `enabled: false` there to turn a bundled tool off ([Tool contracts](tools.md)) |
| `locale_dirs` | Directories of UI message catalogs (`{locale}.yaml`) shipped by the extension. The catalog's only top-level key must be the extension's `name` ([below](#locale-catalogs)) |
| `command_packages` | Extra packages scanned for `@register_command` handlers |
| `dashboard_page` | One tab on the observability dashboard (`DashboardPage(label, group)`), where `group` is `main` (primary nav) or `admin` (overflow menu). Paths are not declared; they are derived from `name` ([Dashboard contributions](extension-dashboard.md)) |
| `dashboard_static_dir` | Directory served at `/static/ext/{name}/`. Put `page.js` at its root when the extension contributes a tab |
| `dashboard_routes` | HTTP routes (`DashboardRoute`) mounted inside session auth. Paths must live under `/api/{name}` |
| `dashboard_public_routes` | Public routes mounted outside session auth (OAuth callbacks and the like). Paths must live under `/oauth/{name}`; validating `state` is the extension's job |
| `config_model` | The one YAML section model this extension adds, or `None`. It lives at `extensions.<name with hyphens as underscores>` (`cfg.extensions.google_oauth` for `google-oauth`) ([Config composition](extension-config.md)) |
| `env_fields` | Secret fields this extension adds to `cfg.env` |
| `required_env_fields` | `cfg.env` fields this extension reads but does not provide |
| `required_tool_context_keys` | Tool context keys this extension's tools read but does not provide |
| `requires` (class attribute) | Names of extensions this one depends on; they must be loaded and listed earlier in `LILLA_EXTENSIONS` ([below](#dependencies-between-extensions)) |
| `api_version` (class attribute) | The `Extension` contract version this extension was written against. Defaults to the core's current `EXTENSION_API_VERSION`; an unsupported value fails at load ([below](#extension-contract-compatibility)) |

## Collision rules

Contribution keys must not collide **between extensions**: duplicate `Extension.name`,
config section names, env field names, tool context keys, result-delivery `client_type`
keys, command names, or tool file names across different roots all fail fast at startup
rather than silently picking a winner. Client prompts and conversation start hooks are
the exception by design: they are lists per `client_type` and are concatenated in load
order, so several extensions can contribute to the same client.

`client_type="discord"` is special in two ways: the core provides a built-in system
prompt that is used only when no extension contributes one, and the core owns
`!toolresult` delivery, so extensions cannot register a result delivery for it.

## Locale catalogs

Extensions may also ship their own Discord text through `locale_dirs()`. Each directory
holds `{locale}.yaml` files named the same way as the core's (`ja.yaml`, `en.yaml`, ...),
and the catalog's **only top-level key must be the extension's `name`**.

```yaml
# en.yaml (for an extension with name = "lilla-habits")
lilla-habits:
  notify:
    title: Today's habits
```

Calls then look like `t("lilla-habits.notify.title")`. The core never prefixes keys for
you: the key in the YAML and the key you pass to `t()` are the same string.

- The core layers extension catalogs onto its own in load order, so lookups keep the
  usual "`ui.locale` → `ja` → the key itself" fallback, and an extension that ships only
  `ja.yaml` still works under `ui.locale: en`
- If one extension returns several directories, its own node is merged shallowly in load
  order (a later directory wins on the same key)
- A catalog whose top-level key is not the extension name, or an extension whose name
  collides with a core top-level key (`selftest`, ...), fails fast with `ValueError`
  while the extensions are registered, so `t()` itself still never raises
- A directory that does not exist logs a warning (once per locale) and is skipped, and a
  broken YAML is logged as an error and treated as empty for that locale

## `client_state`

A client extension that drives `run_conversation()` itself may pass any object as
`client_state` (for example its set of connected sockets). The core does not interpret
it: it is exposed to hooks as `ConversationContext.client_state` and to LLM tools as
the `client_state` context key.

## Dependencies between extensions

An extension declares the extensions it depends on in the `requires` class attribute.
The core checks at load time that every name is loaded **and listed before** the
dependent extension in `LILLA_EXTENSIONS`; it never reorders them, because message
hooks, `setup()` and tool roots all run in load order.

```python
class GoogleCalendarExtension(Extension):
    name = "lilla-google-calendar"
    requires = ("lilla-google-oauth",)  # also covers reading extensions.lilla_google_oauth

    def required_env_fields(self):
        return ["google_client_secret"]

    def required_tool_context_keys(self):
        return ["google_client"]
```

`requires` answers "is the other pack loaded, in the right order?"; the `required_*`
methods answer "does somebody provide the specific thing I read?". There is no generic
`validate()` hook: run-time checks belong in `setup()`.

## Trust boundary

Modules listed in `LILLA_EXTENSIONS` run as **trusted code** in the same process. This
is not a sandbox. The same applies to every directory tools are loaded from
([Tool contracts](tools.md#trusting-tool-directories)).

## Extension contract compatibility

`lilla_core.core.extension.EXTENSION_API_VERSION` is the version of the `Extension`
contract this core provides, and `SUPPORTED_EXTENSION_API_VERSIONS` is the set it
accepts at load time. An extension may pin the version it was written against:

```python
from lilla_core.core.extension import EXTENSION_API_VERSION, Extension


class MyExtension(Extension):
    name = "my-extension"
    api_version = 2  # or leave the default, which is EXTENSION_API_VERSION
```

If the declared version is not supported, `load_extensions()` fails and names the
extension and both versions, instead of loading an extension written against an older
contract and breaking later at run time. The current version is 2, which replaced
`config_models()` and `required_config_sections()` with `config_model()`; an extension
that still defines either removed method fails at load with a message naming the
replacement, even if it does not declare `api_version`.

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

## Related pages

- [Config composition](extension-config.md)
- [Dashboard contributions](extension-dashboard.md)
- [Testing an extension](extension-testing.md)
