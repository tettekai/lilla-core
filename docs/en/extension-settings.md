# Extension settings managed on screen

> This page is a translation of the [Japanese original](../ja/extension-settings.md). If the two
> differ, the Japanese version is authoritative.

Of an extension's settings, those that users edit on the dashboard and use without a restart
can be declared with `Extension.settings_model()`. The values are stored in MongoDB's
`extension_settings`, one document per extension, and edited on the observability
dashboard's **Settings** page (top-right menu, `#/settings`; per-extension settings).

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
  startup, pointing to where it moved (the dashboard's Settings page). The YAML value is
  neither silently discarded nor used.
- The input control on the screen follows the field's type. Currently supported are strings
  (multi-line with `multiline`), booleans, numbers, the unset (`None`) variant of each, and
  the "list chosen from options" below. `title` / `description` become the screen's heading and
  description, and `pattern` and validators are used for validation on save.
- A type with no matching input control (an array without a `widget`, a nested model, and so
  on) shows "not supported by this form" on the screen, and saving sends the current value back
  unchanged. The generic form covers only flat settings without nesting. Handle nesting in a
  per-extension page (`dashboard_page()`).

### A list chosen from options (checkbox group)

For a field that picks several values from fixed candidates, write `"checkbox-group"` as the
`widget` in `json_schema_extra`. The input control is not inferred from the type, so just writing
`list[str]` does not give you checkboxes.

```python
class DiarySettings(BaseModel):
    # Dynamic options: choose from writable storages
    storages: list[str] = Field(
        default=["notes"],
        title="清書の保存先",
        json_schema_extra={"widget": "checkbox-group", "choices_from": "writable_file_storages"},
    )
    # Static options: {value, title}, or just a value (the label is the value as a string)
    weekdays: list[int] = Field(
        default=[1, 2, 3, 4, 5],
        json_schema_extra={
            "widget": "checkbox-group",
            "choices": [{"value": 0, "title": "日"}, 1, 2, 3, 4, 5, {"value": 6, "title": "土"}],
        },
    )
```

- Make the field an array of scalars (string, integer, number, boolean) and write **exactly
  one** of `choices` (static) or `choices_from` (dynamic). Neither, both, an unknown `widget` or
  `choices_from`, or a field that is not an array fails at load.
- `choices_from` is resolved by the core every time the screen is opened. The names available
  now are:

  | Name | Options |
  |------|------|
  | `writable_file_storages` | The registered storages (`Extension.file_storages()`) whose `read_only` is false (in registration order; value and label are the storage's name) |

- Values are saved with their types preserved (integer options stay integers). Array fields do
  not show an "unset" state.
- Saving with everything unchecked saves an empty array `[]` (it does not return to the
  initial value).
- Saved values that are not among the current options (for example a storage that is not
  loaded now) are not dropped: they stay checked and are labeled "not in the current options".
  They disappear only when you uncheck them and save.
- Validation on save is only the model's rules. The core does not refuse to save merely because
  a value is not among the current options. Check whether an option is usable at run time on the
  side that uses the setting (the extension).

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

Save the whole set in the "Settings" area of the dashboard's Settings page (the API is
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
- [The observability dashboard](dashboard.md#settings)
