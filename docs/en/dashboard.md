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

Skills (`#/skills`) lists the skills called with `!skill` and lets you add, edit, delete
and enable / disable them. See [Skills](skills.md) for the naming rules and how to call them.

## Extensions

Extensions (admin menu, `#/admin/extensions`) edits, for each loaded extension, its
on-screen managed settings (`extension_settings`, the settings proper) and its overrides
(`extension_overrides`, which never change the code or the bundled defaults). Only
extensions that have on-screen settings, a default prompt or a bundled scheduled task are
listed; sections with nothing to edit are not shown.

- **Settings**: the settings an extension declared with `settings_model()` (these are the
  settings themselves, not overrides). You can see whether they are saved (the initial
  values if not), each field's value or unset state, and validation errors. The whole set
  is validated before saving and is used from the next piece of work the extension starts
  ([Extension settings managed on screen](extension-settings.md)). If the database cannot
  be read, the screen says "unreadable" and distinguishes this from "not saved"
- **Prompt**: shows the default (chosen in the order `ui.locale` → `ja` → `en`), whether
  an override exists and the text currently in effect, and lets you override the whole
  prompt with a single string. Saving it empty becomes an override meaning "do not inject
  this extension's prompt". "Reset to default" only deletes the override and does not copy
  the default text. Changing `ui.locale` does not translate an override. It takes effect
  from the next conversation ([Extension default prompts and overrides](extension-prompt.md))
- **Scheduled tasks**: for each scheduled task the extension bundles and that is loaded,
  you can override only `schedule` (crontab) and whether the periodic run is enabled. The
  crontab is interpreted in `ui.timezone`. Disabling only stops the periodic run; manual
  runs with `!runtask` still work. Tasks not loaded because the tool YAML says
  `enabled: false` do not appear and cannot be revived from here. Core tasks and other
  extensions' tasks are out of scope. Saving applies to the scheduler immediately (running
  jobs are not stopped); if applying fails, the screen shows the failure and "Re-apply"
  retries ([Overriding an extension's tasks](extension-task-override.md))

A save validates the whole request and writes it in one update; if anything is invalid
(an unknown key, a wrong type, a task out of scope, a value that is not a crontab), nothing
is saved. Saving only the prompt does not erase `tasks` (and vice versa).

APIs used by the screen (all require login):

| Method and path | What it does |
|------|------|
| `GET /api/admin/extensions` | Loaded extensions, and a marker for whether each is editable |
| `GET /api/admin/extensions/{name}` | The prompt and task defaults, overrides and effective values, plus the values currently on the scheduler. Also includes the on-screen settings (`settings`: fields, values, whether saved, errors) |
| `PATCH /api/admin/extensions/{name}/overrides` | Saves overrides. Body: `{"prompt": string \| null, "tasks": {tool_name: {"schedule": string \| null, "enabled": boolean \| null}}}`. Omitted keys are left unchanged and `null` deletes an override. In the response, `saved` says whether saving succeeded and `tasks_apply` the result of applying (`applied` / `scheduler_not_running` / `scheduler_disabled` / `failed`; `null` if no task was changed) |
| `POST /api/admin/extensions/{name}/tasks/apply` | Re-applies the saved task overrides (503 on failure) |
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
