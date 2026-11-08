# Extension settings managed on screen

> This page is a translation of the [Japanese original](../ja/extension-settings.md). If the two
> differ, the Japanese version is authoritative.

Of an extension's settings, those that users edit on the dashboard and use without a restart
can be declared with `Extension.settings_model()`. The values are stored in MongoDB's
`extension_settings`, one document per extension, and edited on the observability
dashboard's **Extensions** page (admin menu, `#/admin/extensions`).

## How it differs from YAML settings and overrides

| | Where it lives | Edited via | Takes effect |
|---|---|---|---|
| `config_model()` | `extensions.<section>` in `lilla.yaml` ([Config composition](extension-config.md)) | A file | At startup |
| `settings_model()` (this page) | MongoDB's `extension_settings` (the settings **proper**) | The dashboard | From the next piece of work |
| `extension_overrides` | MongoDB (**overrides** of `default_prompt()` and of bundled task defaults) | The dashboard | [Prompt](extension-prompt.md) / [Tasks](extension-task-override.md) |

- Each field is owned by **either** YAML or the screen. There is no precedence or fallback
  that would let both specify it.
- Within one extension you can mix YAML-owned fields (`config_model()`) and screen-owned
  fields (`settings_model()`).

## Declaring

```python
from pydantic import BaseModel, ConfigDict, Field, field_validator

from lilla_core.core.extension import Extension


class DiarySettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    template: str | None = Field(
        default=None,
        title="テンプレート",
        description="日記の Markdown 本文",
        json_schema_extra={"multiline": True},
    )
    day_rollover: str = Field(default="04:00", title="日付の切り替わり", pattern=r"^\d{2}:\d{2}$")

    @field_validator("template")
    @classmethod
    def _not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("template must not be blank")
        return value


class DiaryExtension(Extension):
    name = "diary"

    def settings_model(self):
        return DiarySettings
```

- **Every field needs a default.** The default is the initial value used before anything is
  saved. To express "unset", make `None` the default, as in `str | None = None`.
- A field without a default, or a default that fails the model's validation (a declaration
  mistake), fails at load.
- Having a field with the same name as one in `config_model()` is a double declaration of
  ownership and fails at load.
- Writing a `settings_model()` field under `extensions.<section>` in `lilla.yaml` fails at
  startup, pointing to where it moved (the dashboard's Extensions page). The YAML value is
  neither silently discarded nor used.
- The input control on the screen follows the field's type. Currently supported are strings
  (multi-line with `multiline`), booleans, numbers, and the unset (`None`) variant of each.
  `title` / `description` become the screen's heading and description, and `pattern` and
  validators are used for validation on save.

## Reading

```python
from lilla_core.services.extension_settings import load_settings


async def execute(input, context):
    settings = await load_settings("diary", DiarySettings)  # once, at the start of the work
    ...  # keep using settings for the duration of this piece of work
```

- `load_settings()` reads MongoDB on every call (there is no cache). Work that starts right
  after a save sees the new values, with no restart. It does not watch for changes or poll.
- **Within one piece of work, keep using the instance (snapshot) you read first.** Re-reading
  midway would mix old and new values across a save. Saving neither changes nor stops work
  already running. The extension decides what counts as one piece of work (one tool run, one
  task run, and so on). Setting `frozen=True` also guards against accidental modification.
- If you pass `model`, the return value is typed, and a model that differs from the declared
  one raises `ValueError`.

Failures are reported as exceptions, distinct from "not saved".

| State | Result |
|------|------|
| Not saved | Returns the model with the declared initial values (it does not read YAML or external files) |
| MongoDB cannot be read | `SettingsUnavailableError` |
| The stored value fails the current model's validation (including unknown fields) | `InvalidStoredSettingsError` (`errors` holds a reason per field) |

Both exceptions derive from `ExtensionSettingsError`. When a stored value is invalid, it is
neither silently replaced with the initial values nor written back. The failure continues
until a correct value is saved again on the screen.

## Saving

Save the whole set in the "Settings" area of the dashboard's Extensions page (the API is
`PUT /api/admin/extensions/{name}/settings`, with the body `{"values": {field: value}}`).

- The whole set is validated with the model and then replaced in a single write
  (`updated_at` is UTC). If anything is invalid (an unknown field, a wrong type, a violated
  validation rule), nothing is saved and a reason per field is returned.
- Omitted fields are saved with their initial values.
- If saving fails, the screen does not report success.

## When changing the model

There is no general migration mechanism. When you change the model, keep the following in mind.

- When **adding** a field, give it a default. A field missing from the saved set takes its
  default.
- **Removing or renaming** a field, or **tightening its type or validation rules**, makes
  stored values fail validation, resulting in `InvalidStoredSettingsError` (the screen also
  shows "the stored values do not match"). If a migration is needed, tell users, as part of
  the extension's upgrade steps, to save again on the screen.
- When moving a YAML field to the screen, remove it from `config_model()` and add it to
  `settings_model()`. If it remains in a user's `lilla.yaml`, startup fails pointing to the
  destination, so describe the migration steps (note the value → remove it from YAML → save it
  on the screen).

## Related pages

- [Extension basics](extensions.md)
- [Config composition](extension-config.md)
- [The observability dashboard](dashboard.md#extensions)
