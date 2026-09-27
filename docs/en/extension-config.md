# Config composition

> This page is a translation of the [Japanese original](../ja/extension-config.md). If
> the two differ, the Japanese version is authoritative.

An extension declares the config it adds, and the core composes one Pydantic model out
of every declaration at startup. Writing an `AppConfig` subclass by hand and swapping it
in via `set_config()` as an import side effect is no longer part of the contract: the
composed instance replaces anything an extension sets during import, so the order of
`LILLA_EXTENSIONS` does not affect config.

```python
class GoogleConfig(BaseModel):
    client_id: str | None = None
    redirect_uri: str = "http://localhost/google-callback"


class GoogleOAuthExtension(Extension):
    name = "google-oauth"

    def config_model(self):
        return GoogleConfig

    def env_fields(self):
        return {"google_client_secret": "GOOGLE_CLIENT_SECRET"}
```

## Section key and placement

An extension adds at most **one** section model, and it never names the section itself:
the key is its `name` with hyphens replaced by underscores (`google-oauth` →
`google_oauth`; a name without hyphens is used as is). To carry several groups of
settings, nest them as fields of that one model. Extension sections live under the
core-owned `extensions:` key in `lilla.yaml`, never at the top level, so the core can add
a top-level section later without clashing with any extension:

```yaml
dashboard:
  port: 8765
extensions:
  google_oauth:
    client_id: ...
```

## Reading it back

`get_config().extensions.google_oauth.client_id` and
`get_config().env.google_client_secret` are then readable process-wide; there is no
top-level `get_config().google_oauth`. Because
`get_config()` is typed as the base `AppConfig`, use `get_section()` when you want the
section back as its own model for type checking and completion:

```python
from lilla_core.core.config import get_section

client_id = get_section("google-oauth", GoogleConfig).client_id
```

It takes the extension's `name` (the section key works too), looks only under
`extensions:` (read core sections such as `get_config().ui`
directly) and raises `ValueError` when the section was never declared or is not an
instance of the given model, so a misspelled name fails loudly instead of returning
nothing.

## Composition rules

- A section is **required** when its model has at least one required field, and optional
  otherwise. A required section missing from `lilla.yaml` fails at startup.
- Unknown keys under `extensions:` fail at startup, so a leftover section for an
  extension that is no longer loaded does not linger unnoticed. With no extensions
  loaded, `extensions` is empty.
- A declared section written at the top level instead of under `extensions:` also fails
  at startup, since otherwise it would be ignored and the model defaults would apply
  silently. A top-level key that is also a core section name is the core's and is left
  alone.
- Composed env fields are always `str | None` with a default of `None`. The contract
  carries no type, so required or non-string secrets cannot be expressed this way.
- `config_model()` must return a Pydantic `BaseModel` subclass or `None`; anything else
  fails at load. Since extension names are unique and contain no underscores, two
  extensions can never derive the same section key. An extension whose name starts with
  a digit cannot declare a model, because the key would not be a valid identifier.
  Section keys may match a core-owned section (they live in a separate namespace).
- Providing an env field name twice fails fast; core-owned env field names are reserved.
- To read another extension's section, depend on that extension with `requires`
  ([Dependencies between extensions](extensions.md#dependencies-between-extensions))
  and read `cfg.extensions.<its key>`; there is no separate declaration for it.
  `required_env_fields()` and `required_tool_context_keys()` still list `cfg.env` fields
  and tool context keys the extension reads but does not provide. If nothing provides a
  required name, the load fails and names the extension that asked for it; core-owned
  env fields and tool context keys always satisfy a requirement.
