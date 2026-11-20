# The observability dashboard

> This page is a translation of the [Japanese original](../ja/dashboard.md). If the two
> differ, the Japanese version is authoritative.

At startup the core brings up an HTTP dashboard for reading conversation history, user
memos, skills and logs. There is no enable/disable flag — if the bot runs, the terminal is open.
It is configured under `dashboard:` in `lilla.yaml`; omit the section for the defaults.

```yaml
dashboard:
  host: "0.0.0.0"     # address to listen on (default)
  port: 8765          # port to listen on (default)
  cookie_secure: true # add Secure to the session cookie (default)
```

> **Security note**
>
> This port serves an admin UI. While no password is set, `POST /api/setup` is the
> bootstrap for choosing one, and it only accepts a **one-time setup token**. At startup
> the bot generates the token and prints it once to its log as a WARNING
> (`... enter this one-time setup token on the setup screen: <token>`); paste it into the
> setup screen together with the new password. The token is never shown on the page or
> returned by any API, so reaching the port alone is not enough to claim the admin
> password — but anyone who can read the bot's log can. Once the password is registered
> the token is discarded and `/api/setup` is blocked with 403 for good. Restarting before
> setup issues a new token and invalidates the old one.
>
> - `host` defaults to every interface (`0.0.0.0`) because container deployment is the
>   assumed case. **Do not expose this port directly to a public network.** Put an access
>   control in front of it (a reverse proxy, Zero Trust, …), or set `host: 127.0.0.1` if
>   only the local host needs it.
> - **Set the initial password first thing after starting** (opening `/` shows the setup
>   screen; take the setup token from the startup log).
> - `cookie_secure: true` is the safe default and assumes HTTPS. Reaching
>   `http://<host>:8765` directly over a LAN needs `false`, otherwise login succeeds but
>   the browser never sends the cookie back.

## Skills

Skills (`#/skills`) lists the skills called with `!skill` and lets you add, edit, delete,
enable / disable them and toggle "Callable by LLM" (whether `llm_load_skill` may load them).
See [Skills](skills.md) for the naming rules and how to call them.
The prompt field is a [Jinja2 template input field](#jinja2-template-input-field), where you
can check the syntax and the expanded result while typing.

## Jinja2 template input field

Input fields whose text is expanded as a Jinja2 template (the new / edit fields for a skill's
prompt, and the extension prompt override field on the settings page, which is used inside the
[prompt override component](#prompt-override-component) below) use a common component.

- It shows a "Jinja2 template" label and the list of available variables. Clicking a variable
  inserts `{{ name }}` at the cursor. The list always contains the common `current_hour`, and for
  extension prompts also `settings.name` (secret fields excluded)
- A short while after typing stops (about 0.4 seconds), the server performs the same trial
  expansion as on save and shows the result. If it cannot be expanded, the reason is shown
  (syntax errors carry a line number); if it can, "Preview after expansion" shows the expanded
  text. Parts removed on expansion by `{# … #}` are listed below the preview with their line
  number and content
- When an extension prompt cannot read the settings, only the syntax is checked, as on save,
  and the screen says so
- There is no syntax highlighting. The preview is for checking; saving and the checks on save
  work as before

Expansion is not done in the browser; the screen calls the following API (login required; the
text is not saved).

| Method and path | What it does |
|------|------|
| `POST /api/admin/prompt-preview` | Body: `{"text": text, "target": {"type": "skill"}}` or `{"text": text, "target": {"type": "extension", "name": extension_name}}`. A skill is trimmed of surrounding whitespace as on save and expanded with `current_hour` only; an extension prompt is expanded with the current settings (secrets excluded). Success is 200 with `{"rendered": expanded text \| null, "comments": [{"line", "text"}], "settings_unavailable": boolean}` (`rendered` is `null` when the settings could not be read and expansion was skipped). If it cannot be expanded, 400 with `{"error": reason}`. 404 for an extension that is not loaded or has no default prompt |

### Using the component (for dashboard developers)

The component is registered by `src/lilla_core/dashboard/jinja-editor.js` as
`Alpine.data('jinjaEditor', …)`, and its markup lives in `<template id="jinja-editor-tpl">` in
`index.html`. Attach it to an empty element with `x-data`, and bind the text to the caller's
value with `x-modelable="text"` and `x-model`.

```html
<div x-data="jinjaEditor({
       target: () => ({ type: 'extension', name: exts.selected }),
       variables: () => ['settings.day_rollover'],
       fetch: (u, o) => authedFetch(u, o),
       rows: 12,
     })"
     x-modelable="text" x-model="skills.newPrompt"></div>
```

| Argument | What it is |
|------|------|
| `target` | The variable set used for the preview (same shape as the API's `target`). A value, or a function returning one (to follow a selection change) |
| `variables` | Variable names added to the list (the component prepends `current_hour`). A value or a function |
| `fetch` | The function that calls the API. Passing the dashboard's `authedFetch` returns to the login screen when the session expires |
| `rows` / `placeholder` | Look of the input field |

State is kept per instance, so the text and preview do not get mixed when several are placed.
Removing the element (for example with `x-if`) stops a pending preview. Where many rows look at
the same value, such as an edit field used in each row of a list, place the component with
`x-if` only on the row being edited (each placed component queries a preview).

## Prompt override component

The input field where you choose whether to "append to" or "replace" the default prompt and
write the text uses a common component. At present only the prompt field of the settings page
uses it.

- The radio buttons "Append to the default prompt" and "Replace the default prompt" choose the
  mode
- Only in append mode, the default text is shown above the input field, read-only, with its
  language (it can be selected and copied by hand). It is not shown in replace mode
- The input field is a [Jinja2 template input field](#jinja2-template-input-field) and is given
  only the text you typed (the preview and the checks on save cover only the typed text,
  regardless of mode)
- Switching modes does not erase the typed text, and the default is never inserted
  automatically. There is no dedicated copy button

### Using the component (for dashboard developers)

The component is registered by `src/lilla_core/dashboard/prompt-override-editor.js` as
`Alpine.data('promptOverrideEditor', …)`, and its markup lives in
`<template id="prompt-override-editor-tpl">` in `index.html`. Because it uses `jinjaEditor`
inside, a screen that uses it loads both `jinja-editor.js` and this file with `<script>` before
`app.js` (both register inside `alpine:init`). Attach it to an empty element with `x-data`, and
bind the value `{mode: 'append' | 'replace', text}` to the caller's value with
`x-modelable="value"` and `x-model`.

```html
<div x-data="promptOverrideEditor({
       defaultText: () => exts.detail.prompt.default,
       defaultLocale: () => exts.detail.prompt.default_locale,
       target: () => ({ type: 'extension', name: exts.selected }),
       variables: () => ['settings.day_rollover'],
       fetch: (u, o) => authedFetch(u, o),
       rows: 12,
     })"
     x-modelable="value" x-model="exts.promptDraft"></div>
```

| Argument | What it is |
|------|------|
| `defaultText` / `defaultLocale` | The default text and its language shown in append mode. A value or a function returning one |
| `target` / `variables` / `fetch` / `rows` / `placeholder` | Passed straight to `jinjaEditor` (same meaning as the table above). A value or a function |

The caller's value must always be a `{mode, text}` object (initially `{mode: 'append', text: ''}`).
The extension name and the like are not held by the component but passed as arguments. State
(including the radio group) is kept per instance, so mode, text and preview do not get mixed
when several are placed. Cleanup when the element is removed is done by the `jinjaEditor`
inside. Badges, descriptions, warnings and the save / reset buttons are not part of the
component; the calling screen provides them.

## Screen layout

The central navigation holds the screens used day to day (Home / Conversations / Memos /
Skills / Usage) and the screens extensions added (`dashboard_page()`; for example Notes). The
top-right menu holds Logs (`#/admin/logs`), Settings (`#/settings`) and Logout. The dashboard's
wording is hard-coded in Japanese.

## Settings

Settings (top-right menu, `#/settings`) switches between three items in the left menu.

| Menu | Hash | What it shows |
|------|------|------|
| General | `#/settings` | A summary of how the core currently behaves (display language, timezone, LLM providers and the one in use, tool disabling, the internal scheduler, the listen addresses of the dashboard and the shared HTTP server, loaded extensions). **Read-only**; the source of truth is `lilla.yaml` (there is no path to change it from the screen, so it is not managed twice). Secrets (values from `.env`) and provider URLs are not shown |
| Built-in features (currently Notes) | `#/settings/{name}` | Settings of the built-in extensions (`BUILTIN_EXTENSION_MODULES`). They are not in the extensions list but sit beside General |
| Extensions | `#/settings/extensions` | The list of the other loaded extensions. Extensions with nothing to configure are listed too, and those from the official extension pack carry an "Official" mark. Selecting one opens its settings at `#/settings/extensions/{name}` |

The list and headings use the extension's display name (`Extension.display_name()`; `name` if
unset). An extension that has a screen links from its settings to the screen with "Open
<display name>", and from the screen to its settings with "⚙ Settings" above it. The former
`#/admin/extensions` no longer works (no alias is kept, so re-create any bookmark).

Each extension's settings screen edits its on-screen managed settings
(`extension_settings`, the settings proper) and its overrides (`extension_overrides`, which
never change the code or the bundled defaults). Sections with nothing to edit are not shown.

- **Settings**: the settings an extension declared with `settings_model()` (these are the
  settings themselves, not overrides). You can see whether they are saved (the initial
  values if not), each field's value or unset state, and validation errors. The whole set
  is validated before saving and is used from the next piece of work the extension starts
  ([Extension settings managed on screen](extension-settings.md)). If the database cannot
  be read, the screen says "unreadable" and distinguishes this from "not saved"

- **Prompt**: you can override it by choosing "Append to the default prompt" (write more after
  the default) or "Replace the default prompt" (use only the text you typed). The field is the
  [prompt override component](#prompt-override-component), and only in append mode it shows the
  default (chosen in the order `ui.locale` → `ja` → `en`) with its language. The edit field
  holds the saved mode and text (append mode and empty text if there is no override), never the
  default combined with it. In append mode the default part follows updates to the extension's
  default and changes of `ui.locale` (the typed text is not translated). Saving empty in replace
  mode becomes an override meaning "do not inject this extension's prompt". "Reset to default"
  deletes the override and returns the edit field to append mode with empty text. If the saved
  value does not match the current format (for example the former single-string format), a
  warning is shown and the default is used, so save again or press "Reset to default". It takes
  effect from the next conversation ([Extension default prompts and overrides](extension-prompt.md))
- **Scheduled tasks**: for each scheduled task the extension bundles and that is loaded,
  you can override only `schedule` (crontab) and whether the periodic run is enabled. The
  crontab is interpreted in `ui.timezone`. Disabling only stops the periodic run; manual
  runs with `!runtask` still work. Tasks not loaded because the tool YAML says
  `enabled: false` do not appear and cannot be revived from here. Core tasks and other
  extensions' tasks are out of scope. Saving applies to the scheduler immediately (running
  jobs are not stopped); if applying fails, the screen shows the failure and "Re-apply"
  retries ([Overriding an extension's tasks](extension-task-override.md))

A save validates the whole request and writes it in one update; if anything is invalid
(an unknown key, a wrong type, a task out of scope, a value that is not a crontab, an unknown
prompt mode, or typed text that cannot be expanded), nothing is saved. Saving only the prompt
does not erase `tasks` (and vice versa).

APIs used by the screen (all require login):

| Method and path | What it does |
|------|------|
| `GET /api/admin/settings/general` | The contents of General (read-only; contains no secrets or provider URLs) |
| `GET /api/admin/extensions` | All loaded extensions: `name`, `display_name`, `kind` (`builtin` / `official` / `external`), whether editable (`editable`), the hash of its settings screen (`settings_hash`), the hash of its own screen (`page_hash`; `null` if it has none), and so on |
| `GET /api/admin/extensions/{name}` | The prompt and task defaults, overrides and effective values, plus the values currently on the scheduler. The prompt carries `default` / `default_locale` / `overridden` / `mode` / `text` (has a value only when an override exists) / `invalid` / `effective` (the default and the typed text joined by a blank line, unexpanded, for display) / `template_variables`. Also includes the on-screen settings (`settings`: fields, values, whether saved, errors) |
| `PATCH /api/admin/extensions/{name}/overrides` | Saves overrides. Body: `{"prompt": {"mode": "append" \| "replace", "text": string} \| null, "tasks": {tool_name: {"schedule": string \| null, "enabled": boolean \| null}}}`. Omitted keys are left unchanged and `null` deletes an override. In the response, `saved` says whether saving succeeded and `tasks_apply` the result of applying (`applied` / `scheduler_not_running` / `scheduler_disabled` / `failed`; `null` if no task was changed). `prompt` needs both `mode` and `text` and accepts no other keys (the former single-string format is a 400 too). The syntax check and trial expansion cover only `text` |
| `POST /api/admin/extensions/{name}/tasks/apply` | Re-applies the saved task overrides (503 on failure) |
| `POST /api/admin/prompt-preview` | The result of trial-expanding an override text ([Jinja2 template input field](#jinja2-template-input-field)) |
| `PUT /api/admin/extensions/{name}/settings` | Saves the whole on-screen settings. Body: `{"values": {field: value}}`. Invalid input returns 400 (`errors` holds a reason per field) and saves nothing. 404 for an extension that declares no settings |

## Home

Home (`#/`) does not repeat the other lists; it only shows the current state. Each time it
opens it calls `GET /api/dashboard/status` (login required) and shows three things:

- Liveness: that the dashboard itself responds, and whether a ping to MongoDB succeeds.
  It does not run the full self-test that includes the LLM check (`!selftest full`).
- The number of `ERROR` and `WARNING` entries in the last 24 hours (counted from the
  `logs` collection). Zero is shown as "none". No message text, logger names or stack
  traces are returned. If logs are not stored in MongoDB, the counts are always "none".

- The number of LLM calls and total tokens in the last 24 hours (plus the estimated cost
  if prices are configured). The detailed breakdown is on the Usage page.

When MongoDB cannot be reached the page still loads; the affected items show as
"no response" / "unavailable".

## Usage

Usage (`#/usage`) aggregates LLM token usage by day, provider and entry point. See
[Recording LLM token usage](llm-usage.md) for the settings and what is recorded.

Only `/oauth/{extension name}` sits outside the auth middleware, for public GETs a
browser makes without a session (OAuth redirect targets and the like). An upstream access
control would bypass just that prefix.

If you forget the password, delete the record in MongoDB's `admin_credentials`
collection by hand; the setup screen then lets you set a new one. A new setup token is
printed to the log the next time the setup screen is opened (or at the next startup).

See [Dashboard contributions](extension-dashboard.md) for adding tabs and HTTP routes
from an extension.
