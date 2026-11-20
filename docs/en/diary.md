# Diary (official extension pack)

> This page is a translation of the [Japanese original](../ja/diary.md). If the two
> differ, the Japanese version is authoritative.

The `diary` extension of the official extension pack builds up a Markdown draft of each day's
diary during conversations, and handles the evening hearing and the next morning's fair copy.
Drafts live in MongoDB; the fair copy is written under `diary/` of the
[notes store (notes)](notes.md).

| Import path | `name` | Role |
|-------------|--------|------|
| `lilla_core.extensions.diary` | `diary` | Three LLM tools for the diary draft, two default tasks (hearing and fair copy), a prompt for conversations, and settings edited on the dashboard. Depends on no other extension |

## Enabling

```bash
export LILLA_EXTENSIONS=diary
# The full path works the same: export LILLA_EXTENSIONS=lilla_core.extensions.diary
```

There is no ordering constraint when you list it with other extensions. notes is a built-in
feature that is always loaded, so you do not need to list notes for the fair copy.

The tool YAML and task YAML ship with the extension, so the following are loaded even if you do
not put them in `${CONFIG_ROOT}/tools` (a YAML with the same stem in `${CONFIG_ROOT}/tools`
replaces the bundled one entirely; to stop one, use `enabled: false`).

| stem | Kind | Contents |
|------|------|----------|
| `llm_diary_draft_get` | LLM tool | Returns the day's draft. If there is none, creates it from the template and fills `<<date>>` with the date and weekday (e.g. `2026-09-28 (Mon)`) |
| `llm_diary_draft_set` | LLM tool | Replaces the day's draft as a whole. Does not save outside the writable time window, or if the template's fixed text has been broken |
| `llm_diary_draft_faircopy_get` | LLM tool (task only) | Returns a fair copy with the remaining holes of the draft replaced by `-` (`<<moodscore>>` is kept). Does not save. `supported_client_type: task`, so it does not appear in normal conversation |
| `task_hearing_diary` | Task (`type: task_diary_llm`) | Every day at 22:00. Looks at the day's draft and asks mainly about items not yet filled in |
| `task_write_diary` | Task (`type: task_diary_faircopy`) | Every day at 03:30. Writes the previous day's fair copy to notes without going through the LLM |

To let normal conversation read and write the draft, list the two LLM tools in
`tools.sets.main`. The default `available_tools` of the hearing task is `$main`, so the hearing
uses the same tools.

```yaml
tools:
  sets:
    main:
      - llm_diary_draft_get
      - llm_diary_draft_set
```

## Settings

`extensions.diary` in `lilla.yaml` currently has no items (you can omit the section). Settings
are made on the **observability dashboard** via the top-right menu → **Settings** →
**Extensions** → **Diary** → the "Settings" card, and the values are stored in MongoDB's
`extension_settings` (the `diary` document). Writing the items below under `extensions.diary` in
`lilla.yaml` makes startup fail with an error that points to where they moved.

| Item | Meaning |
|------|---------|
| Template (`template`) | The Markdown **body of the diary itself**. Unset initially |
| Day rollover time (`day_rollover`) | `HH:MM` (default `04:00`, judged in `ui.timezone`). Input such as `6:5` is normalized to `06:05` on save |
| Fair copy storage (`faircopy_storage`) | A checkbox for whether the fair copy task writes the fair copy under `diary/` of notes (only `notes`). On if never saved |
| Fair copy path (`faircopy_path`) | A path relative to `diary/` in notes. Default `{year}/{month}/{date}.md` (e.g. `diary/2026/11/2026-11-12.md` in notes) |

- The bot and the dashboard start even if the template is unset. Meanwhile, creating a draft
  (opening a new day with `llm_diary_draft_get`) and saving (`llm_diary_draft_set`) fail with a
  "template is not set" error. Fetching an existing draft and the fair copy still work.
- To return to unset, check "Unset" and save. An empty or whitespace-only body cannot be saved.
- For diary day d, writing is allowed from d's rollover time until the next day's rollover time.

## Writing the template

- A hole is `<<name>>`. `<<date>>` is filled with the date and weekday when the draft is
  created; other holes are filled in conversation. Holes left at fair-copy time become `-`
  (except `<<moodscore>>`).
- A nameless `<<>>`, a name containing whitespace such as `<< two words >>`, and stray `<<` /
  `>>` that do not form a hole are rejected on save. Nested Markdown quotes (`>> `) and the like
  cannot be read as holes either, so they cannot be written in the template.
- `{{name}}` is not a hole; it stays in the draft as a plain string.
- The body is not rewritten on save. There are no required headings or the like.
- Holes are treated separately from "fixed text" such as headings, frontmatter keys, the date
  line and the ending, and saving a draft checks that only the insides of holes changed.

An example template:

```markdown
---
<<date>>
type: daily
moodscore: <<moodscore>>
---

## Today's highlight
<<highlight>>

## Memo
<<memo>>
```

## Fair copy

The fair copy task (`task_write_diary`) builds the day's fair copy without going through the LLM
and writes it directly under `diary/` of notes. You can get the contents of notes by cloning
notes' Git repository.

- The target day is decided by the rollover time (if it runs before the rollover, it is the
  previous day). If there is no draft for that day, it does nothing. The same day is overwritten.
- When saved, a short report with the written path is sent to `target` (default
  `dm:{DISCORD_MY_USER_ID}`; if `<<moodscore>>` is unfilled, that is mentioned too).
- When writing fails (notes is read-only, a write error, etc.), it goes to the error
  notification channel.
- If you uncheck **Storage** and save, the fair copy is saved nowhere (the fair copy task does
  nothing). It does not go back to on when the page is shown again.
- The saved value is either `["notes"]` (on) or `[]` (off). A value containing a name other than
  `notes`, or a non-list value, makes loading the settings fail with a "stored value is invalid"
  error. Save again on the screen.
- **Path** is a path relative to `diary/`, and the fair copy is written to `diary/` + the path in
  notes.
  - It must contain `{date}` (`YYYY-MM-DD`). `{year}` / `{month}` / `{day}` are also available.
  - A leading `/` (absolute path), `..` or `.` components, empty components (`a//b` or a trailing
    `/`) and `\` are rejected when saving the settings. You cannot point outside `diary/`.
  - The value is used as-is as a path relative to `diary/`. If you write `diary/` at the start,
    it writes to `diary/diary/...`.
  - If a saved path does not match the rules above, loading the settings fails with a "stored
    value is invalid" error (it is not silently replaced with the default).
- notes can only be written under directories declared by loaded extensions. If you remove
  `diary` from `LILLA_EXTENSIONS`, `diary/` also disappears from where notes can be written.
- Change the time on the dashboard's Settings page under `tasks.task_write_diary`, and the
  destination in `${CONFIG_ROOT}/tools/task_write_diary.yaml` (`type: task_diary_faircopy` and
  `target`).

### Using another destination

Uncheck the storage, and put a separate fair-copy task with `type: task_scheduled_llm` (put
`llm_diary_draft_faircopy_get` and a tool for writing out in `available_tools`, and describe the
write-out steps in `prompt`). `task_diary_llm` is only for the hearing (`task_hearing_diary`);
other stems cause a startup error.

```yaml
# ${CONFIG_ROOT}/tools/task_write_diary.yaml (replaces the bundled fair copy task entirely)
type: task_scheduled_llm
schedule: "30 3 * * *"
llm_provider: REPLACE_ME
target: "dm:{DISCORD_MY_USER_ID}"
prompt: file:${config_root}/prompt/write-diary.md
available_tools:
  - llm_diary_draft_faircopy_get
  - REPLACE_ME   # a tool for writing out
```

## Hearing task

The defaults of the hearing (`task_hearing_diary`) are as follows. Items omitted in a YAML with
the same stem are also filled with these defaults.

| Item | Default |
|------|---------|
| `schedule` | `0 22 * * *` |
| `llm_provider` | `llm.default` |
| `prompt` | The bundled `task_prompts/task_hearing_diary.{ja,en}.md` (chosen in the order `ui.locale` → `ja` → `en`) |
| `target` | `dm:{DISCORD_MY_USER_ID}` |
| `available_tools` | `["$main"]` |

Replies arrive as normal conversation and are reflected into the draft with
`llm_diary_draft_set`. You can also change the time and stop the scheduled run on the dashboard's
Settings page under `tasks.task_hearing_diary`
([Overriding an extension's tasks](extension-task-override.md)).

## Prompt for conversations

The `diary` extension carries a conversation-facing description (when to read and write the
draft, how to treat holes, not guessing `moodscore`, how to pick the date) in `default_prompt()`
in ja / en ([Extension default prompts and overrides](extension-prompt.md)).

- When the fair copy storage is on, it also includes where the fair copy goes (`diary/` +
  `faircopy_path` in notes), that past diaries are read by building the path from the date and
  using `llm_notes_read`, and that for days not yet fair-copied you look at the draft. This part
  is built from the current settings at every conversation ([Prompt templates](prompt-template.md)).
- If you override the `diary` prompt on the dashboard, the override's body is used. The same
  `settings` are passed to the override's body too, so to include it you can write, for example,
  `{% if settings.faircopy_storage %}diary/{{ settings.faircopy_path }}{% endif %}`.
- When the settings cannot be read (cannot connect to the DB, or the stored value is invalid),
  the description is inserted unexpanded and a WARNING is logged (the conversation does not stop).

## When changes take effect

- A saved value is used from processing that **starts after** the save (the next call of
  `llm_diary_draft_get` / `llm_diary_draft_set`). No restart is needed.
- A running process continues to the end with the values it read at the start (it does not switch
  midway).
- When the settings cannot be read (cannot connect to the DB, or the stored value is invalid),
  they are not treated as unset; an error saying so is returned.

## Notes when changing the template

- Existing drafts (`diary_drafts`) are not rewritten automatically. A new template is used from
  the first time you open a day that has no draft yet.
- Saving a draft verifies that the body keeps the **current** template's fixed text. If you change
  the template's fixed text after the day's draft was created, that day's draft can no longer be
  saved. It is safest to change the template after you have finished with the day's draft (after
  the date rolls over).

## Stored data

| Location | Contents |
|----------|----------|
| MongoDB `diary_drafts` | Per-day drafts (`_id` is `YYYY-MM-DD`, `content` is the Markdown body) |
| MongoDB `extension_settings` (`diary`) | Settings saved on the dashboard |
| notes `diary/` | Fair copies |
