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
  "prompt": "The overriding prompt text",
  "updated_at": { "$date": "2026-10-27T00:00:00Z" }
}
```

- `extension` is the extension's `name` (a unique index exists and is created at startup).
- `prompt` is a single string with no language distinction and replaces the whole default.
- Writing an empty string in `prompt` is an override meaning "do not inject this
  extension's prompt".
- If the document exists but has no `prompt` key, the default is used (the same document's
  `tasks` key can also override task schedules; see
  [Overriding an extension's tasks](extension-task-override.md). Keys such as `settings` may
  be added later, and only keys that exist act as overrides).
- If `prompt` is not a string (a number, array, object, `null`, and so on), a WARNING that
  includes the extension name is logged and the default is used.
- You normally edit it on the observability dashboard's **Extensions** page (admin menu,
  `#/admin/extensions`; [The observability dashboard](dashboard.md#extensions)). "Reset to
  default" only deletes the `prompt` key and does not copy the default text as an override.
  You can also set it directly with `mongosh` and the like, as in the example below.

```javascript
db.extension_overrides.updateOne(
  { extension: "diary" },
  { $set: { prompt: "The overriding prompt text", updated_at: new Date() } },
  { upsert: true },
)
```

Overrides are not cached; they are read every time the system prompt is built, so they take
effect from the next response without a restart.

## How the text is chosen

For each loaded extension that has a default prompt, the text is chosen in this order:

1. If the override document has a `prompt` key whose value is a string, use it
2. Otherwise, use the default for the language matching `ui.locale`
3. If there is no default matching `ui.locale`, use the first one found in the order `ja` →
   `en` → the first language in the definition

If loading the overrides itself fails (for example MongoDB is unreachable), a WARNING is
logged and every extension is injected with its default. The response is not stopped.

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

- **Overrides do not distinguish languages.** Changing `ui.locale` after overriding does not
  switch the overriding text. Re-enter the override after changing the language.
- **An overridden extension does not follow default updates.** Even if a version upgrade
  changes the default, the overriding text stays. Extensions that are not overridden always
  use the latest default.
- **The extension's `name` is the override document's identifier.** If you change an
  extension's `name` later, existing overrides stop applying.
- Each response reads MongoDB once more (it does not read if no extension has a default
  prompt).

## Related pages

- [Extension basics](extensions.md)
- [Overriding an extension's tasks](extension-task-override.md)
- [Per-provider system prompt additions (`llm.providers.<key>.prompt`)](llm-provider-prompt.md)
