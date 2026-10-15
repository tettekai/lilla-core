# Google OAuth / Calendar / Drive (official extension pack)

> This page is a translation of the [Japanese original](../ja/google.md). If the two
> differ, the Japanese version is authoritative.

The core ships the Google OAuth2, Google Calendar, and Google Drive / Sheets extensions as an
official extension pack. All of them are written against the same `Extension` contract as any external extension,
and they are loaded only when you list their import paths in `LILLA_EXTENSIONS`. If you do not
list them, the core starts on its own as before (no separate package or extras needed).

| Import path | `name` | Role |
|-------------|--------|------|
| `lilla_core.extensions.google_oauth` | `google-oauth` | Obtaining and refreshing OAuth2 tokens, starting the authorization flow, and the authorization-code callback. Provides `GoogleOAuthClient`, which other Google API extensions subclass |
| `lilla_core.extensions.google_calendar` | `google-calendar` | The Google Calendar client and the LLM tools for reading and creating events. `requires = ("google-oauth",)` |
| `lilla_core.extensions.google_drive` | `google-drive` | LLM tools for searching, reading, creating, and overwriting text files in Google Drive, and for reading ranges of Google Sheets. `requires = ("google-oauth",)` |

## Enabling

List `google_oauth` **before** any extension that `requires` it (a wrong order fails at
startup; the core does not reorder).

```bash
export LILLA_EXTENSIONS=google_oauth,google_calendar,google_drive
export GOOGLE_CLIENT_SECRET=...
```

You do not have to list extensions you do not use (for Drive only, `google_oauth,google_drive`).

The official extension pack can be referenced by its short module name
(`google_oauth` / `google_calendar` / `google_drive`) alone. The line above is equivalent to (the full
path form still works as before):

```bash
export LILLA_EXTENSIONS=lilla_core.extensions.google_oauth,lilla_core.extensions.google_calendar,lilla_core.extensions.google_drive
```

The short form only applies to names without a `.`. Anything containing a `.` (a
third-party pack, or a fully-qualified path to an official extension) is used as-is;
it is never expanded into the official extension pack.

You can also load OAuth alone, without Calendar, just for your own Google API extension
(Tasks and so on). In that case list only `lilla_core.extensions.google_oauth` and put
your extension after it.

## Configuration

Write one section per extension under `extensions:` in `lilla.yaml`. OAuth settings, the
calendar list, and the Drive whitelist are kept apart. Every field in every section has a
default, so the bot starts without them (but you cannot authenticate without `client_id`, and
Drive denies everything while its whitelist is empty).

```yaml
extensions:
  google_oauth:
    client_id: "xxxxxxxx.apps.googleusercontent.com"
    # Must match the "Authorized redirect URI" in Google Cloud. The default is below.
    redirect_uri: http://localhost:8765/oauth/google-oauth/callback
  google_calendar:
    # Calendars to read and write. Events are only ever created in calendars listed here.
    calendars:
      - id: primary
        friendly_name: Personal
      - id: xxxxxxxx@group.calendar.google.com
        friendly_name: Family
  google_drive:
    # IDs of the folders to allow (the part after /folders/ in the URL). Everything below them is covered.
    read_only_folders:
      - "1AbCdEfGhIjKlMnOpQrStUvWxYz"
    writable_folders:
      - "1ZyXwVuTsRqPoNmLkJiHgFeDcBa"
```

| Setting | Where | Meaning |
|---------|-------|---------|
| `extensions.google_oauth.client_id` | `lilla.yaml` | OAuth client ID from Google Cloud |
| `extensions.google_oauth.redirect_uri` | `lilla.yaml` | Where the authorization code comes back (default `http://localhost:8765/oauth/google-oauth/callback`) |
| `GOOGLE_CLIENT_SECRET` | `.env` / OS environment | Client secret (`cfg.env.google_client_secret`) |
| `extensions.google_calendar.calendars` | `lilla.yaml` | A list of `id` / `friendly_name` pairs |
| `extensions.google_drive.read_only_folders` | `lilla.yaml` | Folder IDs that may only be read (default empty) |
| `extensions.google_drive.writable_folders` | `lilla.yaml` | Folder IDs that may be read and written (default empty) |
| `extensions.google_drive.write_scope` | `lilla.yaml` | The scope used for writing: `file` (default) or `full` (see [Drive / Sheets tools](#drive--sheets-tools)) |
| `extensions.google_drive.max_read_chars` | `lilla.yaml` | Maximum number of characters of file text or sheet values returned to the LLM (default `100000`; the rest is cut off) |

If a section is left behind for an extension you did not load, the core treats it as a
leftover and fails at startup (when you drop `google_calendar`, remove
`extensions.google_calendar` too; when you drop `google_drive`, remove `extensions.google_drive`).

The time zone is the core's `ui.timezone`; the Calendar extension has no time zone
setting of its own. The same IANA name is used as `timeZone` for timed events, so it
is a good idea to set `ui.timezone` explicitly ([Time zone](timezone.md)).

## Google Cloud setup

1. Create an OAuth client (web application) and register the same value as
   `redirect_uri` under "Authorized redirect URIs"
2. Enable the APIs you use (the Google Calendar API for Calendar; the Google Drive API and
   the Google Sheets API for Drive)

Calendar requests a single scope, `https://www.googleapis.com/auth/calendar.events`
(read and write events). For the scopes Drive requests, see
[Drive / Sheets tools](#drive--sheets-tools).

## Authentication flow

1. When a tool calls a Google API without a valid token, the client starts the
   authorization flow and the tool returns the authorization URL to the LLM
   (`needs_auth`)
2. The user opens the URL in a browser and grants access; Google redirects to
   `redirect_uri`
3. The callback `GET /oauth/google-oauth/callback` is a public route on the
   observability dashboard port (`dashboard.port`). It checks `state`, exchanges the
   code for tokens, and stores them in MongoDB `credentials` per type
   (`CREDENTIAL_TYPE`)
4. From then on the refresh token keeps the access token fresh. When refreshing
   stops working, you are back at step 1

**The callback sits outside the dashboard's authentication.** It is a public GET that
the browser follows back from Google, so it cannot require a session cookie. Instead it
requires an exact match with the stored `state` and deletes the `state` right after the
match so it cannot be reused. See [Observability dashboard](dashboard.md) before exposing
the dashboard port.

## Calendar tools

The tool code lives in the `google-calendar` extension; the YAML lives in your
`${CONFIG_ROOT}/tools/` (the extension does not bundle YAML). `type` is resolved by file name.

```yaml
# ${CONFIG_ROOT}/tools/llm_calendar_get.yaml
type: llm_calendar_get
```

```yaml
# ${CONFIG_ROOT}/tools/llm_calendar_create.yaml
type: llm_calendar_create
```

To use them in normal conversations, also add these stems to `tools.sets.main` in
`lilla.yaml` ([Tool sets](tools.md#tool-sets-toolssets)).

| Tool | Function name | What it does |
|------|---------------|--------------|
| `llm_calendar_get` | `get_calendar_events` | Reads events by `date_range` (`today` / `last_7_days` / `2026-04-20/2026-04-26` and so on), `query`, and `max_results`. Attendees are never passed to the LLM, and IDs of registered calendars are replaced with their `friendly_name` |
| `llm_calendar_create` | `create_calendar_event` | Creates an event in a calendar given by a registered `friendly_name` (all-day or timed). It never writes to a calendar that is not registered |

When `calendars` is empty, the read tool falls back to `calendar_ids` in the tool
execution context (or `primary`) for backward compatibility.

## Drive / Sheets tools

The tool code lives in the `google-drive` extension; the YAML lives in your
`${CONFIG_ROOT}/tools/` (not bundled, as with Calendar). Add only the ones you use.

```yaml
# ${CONFIG_ROOT}/tools/llm_google_drive_search_text.yaml
type: llm_google_drive_search_text
```

```yaml
# ${CONFIG_ROOT}/tools/llm_google_drive_get_text.yaml
type: llm_google_drive_get_text
```

```yaml
# ${CONFIG_ROOT}/tools/llm_google_drive_write_text.yaml
type: llm_google_drive_write_text
```

```yaml
# ${CONFIG_ROOT}/tools/llm_google_sheets_get_range.yaml
type: llm_google_sheets_get_range
```

| Tool | What it does |
|------|--------------|
| `llm_google_drive_search_text` | Full-text searches file names and contents for `query` (required) and returns up to `limit` (default 10, max 50) `text/*` files under the whitelist. Each item has `id` / `name` / `mime_type` / `modified_time` / `writable` / `size` |
| `llm_google_drive_get_text` | Returns the text of the file `file_id` (cut off at `max_read_chars` with `truncated: true`) |
| `llm_google_drive_write_text` | With `file_id`, overwrites an existing text file with `content`; without it, creates a new file from `name` and `parent_folder_id` (both required). Only under writable folders |
| `llm_google_sheets_get_range` | Reads cell values of a spreadsheet by `spreadsheet_id` and `range` (A1 notation). Read-only; there is no search tool, so the user supplies the ID |

### Which files are covered

- The whitelisted folders (`read_only_folders` / `writable_folders`) and everything under them.
  The extension walks up the file's parents and decides by the nearest whitelisted folder (a
  read-only folder inside a writable folder makes everything under it read-only). Listing the
  same ID in both lists fails at startup
- A shared drive is covered when you list the drive's ID (no extra setting needed)
- Only files whose MIME type is `text/*` are treated as text files. Everything else (Google Docs,
  JSON, files Drive detected as `application/octet-stream`, ...) and files in the trash are
  treated as nonexistent, the same as files outside the whitelist
- The MIME type of a new file is chosen from its name (`.md` becomes `text/markdown`; names that
  do not map to `text/*` become `text/plain`). Overwriting keeps the name, location, and MIME type

### Scopes and `write_scope`

| Configuration | Requested scopes |
|---------------|------------------|
| `writable_folders` empty | `drive.readonly` and `spreadsheets.readonly` |
| `writable_folders` set, `write_scope: file` (default) | the above plus `drive.file` |
| `writable_folders` set, `write_scope: full` | `drive` and `spreadsheets.readonly` |

- With `file` (`drive.file`), Google only lets the extension **overwrite files it created
  itself**. Files that were already in a writable folder can be read, but overwriting them
  returns an error that says so. Creating a new file in a folder Lilla did not create (for example,
  one made in the Drive UI) may also be refused by Google (not verified; when it is refused, the
  error says this may be the cause). Use `full` if you need to create files in existing folders or
  overwrite existing files
- With `full` (`drive`), existing files under writable folders can be overwritten too. The token
  itself, however, can read and write the whole drive, and only this extension's whitelist narrows
  it down. Choose it knowing that a leaked token has a much larger impact
- When a configuration change needs more scopes (you added writable folders or switched to
  `full`), the next tool call asks for re-authentication (the extension checks the scopes granted
  to the stored token)

### Caveats

- File and cell contents are passed to the LLM as they are. If you whitelist a folder shared with
  other people, text written there may steer the LLM (prompt injection). Especially when you also
  have writable folders, whitelist only folders whose contents you trust
- Writes overwrite the file and do not detect conflicts. You can roll back from Drive's version history

## Adding other Google APIs (`GoogleOAuthClient`)

Add Google APIs the official extension pack does not cover (Tasks, Health, ...) in your
own extension. The client subclasses `GoogleOAuthClient` and defines only `CREDENTIAL_TYPE` and `SCOPES`.
Token handling, auth headers, starting the authorization flow, and the callback are
shared (you do not add another callback).

```python
from lilla_core.extensions.google_oauth.client import GoogleOAuthClient


class GoogleTasksClient(GoogleOAuthClient):
    CREDENTIAL_TYPE = "google_tasks"  # credential type in MongoDB; unique per API
    SCOPES = ["https://www.googleapis.com/auth/tasks"]

    async def list_tasklists(self) -> dict:
        return await self._request_json(
            "https://tasks.googleapis.com/tasks/v1/users/@me/lists"
        )


client = GoogleTasksClient.from_config()  # built from extensions.google_oauth and GOOGLE_CLIENT_SECRET
```

Because that extension reads the `google-oauth` section and secret, declare the
dependency.

```python
class GoogleTasksExtension(Extension):
    name = "google-tasks"
    requires = ("google-oauth",)

    def required_env_fields(self) -> list[str]:
        return ["google_client_secret"]
```

An expired token arrives as `ReauthenticationRequiredError`; in a tool you can return
the authorization URL with
`lilla_core.tool_support.tool_result.tool_reauth_required(client, ...)`.
