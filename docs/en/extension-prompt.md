# Extension default prompts and overrides

> This page is a translation of the [Japanese original](../ja/extension-prompt.md). If the two
> differ, the Japanese version is authoritative.

An extension can carry the prompt it relies on as a default in its code. Just enabling it
through `LILLA_EXTENSIONS` puts that prompt into the system prompt, so users do not need to
add to the `prompt.system` file. Without editing code or files, a user can override the text
per extension with a single MongoDB document.

## Writing a default prompt

Override `Extension.default_prompt()` and return the text per language.

```python
from lilla_core.core.extension import Extension


class DiaryExtension(Extension):
    name = "diary"

    def default_prompt(self):
        return {
            "ja": "## 日記\n会話の終わりに、その日の出来事を一言で振り返ってください。",
            "en": "## Diary\nAt the end of a conversation, reflect on the day in one line.",
        }


extension = DiaryExtension()
```

- The return value is a `dict` of `{"language": "text"}`. The default is `None` (no prompt).
- No particular language is required (an English-only extension is fine). To match the
  user's `ui.locale`, provide both `ja` and `en` where possible (one may be a translation
  of the other).
- One extension can carry only one prompt.
- The shape is validated at load. A non-`dict`, an empty `dict`, or keys or values that are
  not strings fail at startup with `ValueError`.
- Unlike `client_prompt_providers()`, it goes into every conversation regardless of
  `client_type` (except `task`; [see below](#insertion-position)).

## The override document

An override is placed in MongoDB's `extension_overrides` collection, one document per
extension. Create one only for the extensions you want to override; without it the default
is used.

```json
{
  "extension": "diary",
  "prompt": { "mode": "append", "text": "Text appended after the default" },
  "updated_at": { "$date": "2026-10-27T00:00:00Z" }
}
```

- `extension` is the extension's `name` (a unique index exists and is created at startup).
- `prompt` is an object with exactly two keys, `mode` and `text`. The mode and the text are
  stored together as one field; the full text combined with the default is not stored.
  - `mode: "append"` (add to the default prompt): `text` is appended after the default text,
    separated by a blank line. The default part is the one at run time, so it follows
    updates to the extension's default and changes to `ui.locale`. If `text` is empty, only
    the default is used.
  - `mode: "replace"` (replace the default prompt): only `text` is used. Writing an empty
    string in `text` is an override meaning "do not inject this extension's prompt".
  - `text` is a string with no language distinction (it is not translated).
- If the document exists but has no `prompt` key, the default is used (the same document's
  `tasks` key can also override task schedules; see
  [Overriding an extension's tasks](extension-task-override.md). Keys such as `settings` may
  be added later, and only keys that exist act as overrides).
- If `prompt` does not match this shape (a string, a number, `null`, missing or extra keys,
  an unknown `mode`, a non-string `text`, and so on), a WARNING that includes the extension
  name is logged and the default is used. The invalid text is not injected.
  - The former format (`prompt` as a single string replacing the whole default) is not
    reinterpreted and is treated as this "invalid" case. If one remains, save the override
    again on the settings page or press "Reset to default" (it is not migrated automatically).
- You normally edit it on the observability dashboard's **Settings** page (top-right menu,
  `#/settings`; per-extension settings; [The observability dashboard](dashboard.md#extensions)). "Reset to
  default" only deletes the `prompt` key and does not copy the default text as an override.
  You can also set it directly with `mongosh` and the like, as in the example below.

```javascript
db.extension_overrides.updateOne(
  { extension: "diary" },
  { $set: { prompt: { mode: "append", text: "Text to append" }, updated_at: new Date() } },
  { upsert: true },
)
```

Overrides are not cached; they are read every time the system prompt is built, so they take
effect from the next response without a restart.

## How the text is chosen

For each loaded extension that has a default prompt, the text is chosen as follows:

1. From that extension's own `default_prompt()`, pick the default for the language matching
   `ui.locale`. If there is none, use the first one found in the order `ja` → `en` → the
   first language in the definition (the shared system prompt and other extensions' texts
   are not involved)
2. If there is no override, use only the default
3. For `append`, expand the default and `text` **independently** as
   [templates](#embedding-settings-with-templates), then join the results in this order
   separated by a blank line (`\n\n`)
4. For `replace`, expand and use only `text`

- The joined text is not expanded as one template. So the appended part (`text`) cannot refer
  to `{% set %}` or macros in the default part (and vice versa). What both can use is only
  the shared `current_hour` and the extension's `settings`.
- An empty or whitespace-only part is not expanded. A part that becomes whitespace-only after
  expansion is not joined either, so no extra separator is added. If both are whitespace-only,
  the extension's prompt is not injected.
- A part whose expansion fails uses its text as written, before expansion (see below). It does
  not affect the expansion result of the other part.

If loading the overrides itself fails (for example MongoDB is unreachable), a WARNING is
logged and every extension is injected with its default. The response is not stopped.

## Embedding settings with templates

Both the default prompt and the override are expanded as [Jinja2](https://jinja.palletsprojects.com/)
templates before being injected. You can embed the values of the extension's on-screen
settings (`settings_model()`; [Extension settings managed on screen](extension-settings.md))
into the prompt.

```python
    def default_prompt(self):
        return {
            "ja": (
                "## 日記\n"
                "{% if settings.faircopy_storage %}"
                "清書は notes の `diary/{{ settings.faircopy_path }}` に保存されます。"
                "{% else %}"
                "清書は自動では保存されません。"
                "{% endif %}"
            ),
        }
```

- The variables passed to the template are `current_hour`, common to every prompt
  ([Prompt templates](prompt-template.md)), and `settings`, only for extension prompts. The
  contents of `settings` are that extension's settings, re-read every time the system prompt
  is built (a saved value takes effect from the next response). For an extension without
  settings, `settings` is empty and the DB is not read.
- Fields of type `SecretStr` / `SecretBytes` are not put in `settings` (values inside nested
  models or lists are excluded too).
- You can use ordinary Jinja2 syntax (`{{ settings.name }}`, `{% if %}`, `{% for %}`,
  filters, and so on). Expansion runs in a sandbox, and templates cannot reach Python
  attributes or modules. There is no auto-escaping because the output is not HTML.
- Setting values are output as already-expanded values, and a `{{` inside a value is not
  expanded again.
- `settings` is a read-only container, and both `settings.name` and `settings['name']` return
  the field's value. Even if a field name is the same as `items` / `keys` / `get` and so on, it
  yields the value (the same goes for nested settings and lists).
- An undefined name (a typo, for example) fails instead of becoming an empty string.
- If expansion fails (a syntax error, an undefined name, a sandbox refusal, and so on), a
  WARNING that includes the extension name is logged and that part (the default or the
  appended text) is injected **as written, before expansion**. The response is not stopped.
  When the settings cannot be read, both the default and the appended text use their text
  before expansion. Nothing is logged on success.
- If the expanded text is whitespace-only, the extension's prompt is not injected.
- Trailing newlines of the text are kept (text without any syntax is unchanged by expansion).
- To write `{{` / `{%` / `{#` as literal characters, wrap them in `{% raw %}…{% endraw %}`.

### What extension authors should watch for

- If an existing default prompt contains `{{` / `{%` / `{#` as literal characters, expansion
  fails and WARNINGs keep appearing. Wrap them in `{% raw %}` or change the wording.
- The purpose text of `notes_directories()` is expanded as part of the notes default prompt,
  so do not write `{{` / `{%` / `{#` in it (the core neither checks nor escapes it).

### Overriding on the dashboard

When you save an override on the settings page, in both `append` and `replace` only the
syntax of **the text you entered (`text`)** is checked, and then it is trial-expanded with the
current settings. The text joined with the default is not checked (if only the default has a
problem, you can still save as long as the text you entered is correct). A syntax error, an
undefined name or a sandbox refusal gives 400 and nothing is saved. Since the appended text is
also expanded independently at run time, the result of this trial expansion matches the run-time
expansion of the appended text.

- The trial expansion only verifies the branches taken with the current settings (a typo on
  the untaken side of an `{% if %}` is not found). If you change the settings after saving and
  enter another branch, and expansion fails, the text before expansion is injected as above.
- When the settings cannot be read (the DB is unreachable or a stored value is invalid), the
  trial expansion is skipped and only the syntax is checked.
- On the screen you choose between "Add to the default prompt" and "Replace the default
  prompt"; only in add mode is the default text (with its language, read-only) shown above the
  input field. Switching modes does not erase the text you entered, and the default is not
  inserted automatically. To use the default as a base for editing, copy it from the add-mode
  display and paste it in replace mode. The input field holds the saved mode and text, not the
  full text combined with the default.
- The input field is a [Jinja2 template input field](dashboard.md#jinja2-template-input-field),
  where you can click to insert `current_hour` and the names usable as `settings.name` (fields
  holding secrets are excluded). While you type, the same trial expansion as on save runs, and
  it shows a preview of the expansion of just the text you entered (expanded with the current
  settings and the current time) and the reason for any error. As on save, branches not taken
  cannot be checked.

## Insertion position

The chosen texts are joined in the order of `LILLA_EXTENSIONS`, separated by blank lines, and
inserted at the position below. They are layered on top of the existing system prompt, not
replacing it.

```
(shared system prompt: prompt.system)
(per-client_type additions: prompt.discord and client_prompt_providers())
(conversation prompt: prompt.conversation)
(extension prompts)             ← here
(registered-channel section)
(channel note, user memos, session memory, current time, and so on)
```

As with the conversation prompt, it is not injected when `client_type` is `task` (scheduled
tasks).

## Notes

- **The override text does not distinguish languages.** Changing `ui.locale` switches the
  default part of `append` to the new language, but the text you entered is not translated.
  The text of `replace` does not switch.
- **A `replace` override does not follow default updates.** Even if a version upgrade changes
  the default, the text you entered stays. `append`, and extensions that are not overridden,
  always use the latest default.
- **The extension's `name` is the override document's identifier.** If you change an
  extension's `name` later, existing overrides stop applying.
- Each response reads MongoDB once more (it does not read if no extension has a default
  prompt).

## Related pages

- [Extension basics](extensions.md)
- [Overriding an extension's tasks](extension-task-override.md)
- [Per-provider system prompt additions (`llm.providers.<key>.prompt`)](llm-provider-prompt.md)
