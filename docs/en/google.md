# Google OAuth / Google Calendar (official extension pack)

> This page is a translation of the [Japanese original](../ja/google.md). If the two
> differ, the Japanese version is authoritative.

The core ships the Google OAuth2 and Google Calendar extensions as an official extension
pack. Both are written against the same `Extension` contract as any external extension,
and they are loaded only when you list their import paths in `LILLA_EXTENSIONS`. If you do not
list them, the core starts on its own as before (no separate package or extras needed).

| Import path | `name` | Role |
|-------------|--------|------|
| `lilla_core.extensions.google_oauth` | `google-oauth` | Obtaining and refreshing OAuth2 tokens, starting the authorization flow, and the authorization-code callback. Provides `GoogleOAuthClient`, which other Google API extensions subclass |
| `lilla_core.extensions.google_calendar` | `google-calendar` | The Google Calendar client and the LLM tools for reading and creating events. `requires = ("google-oauth",)` |

## Enabling

List `google_oauth` **before** any extension that `requires` it (a wrong order fails at
startup; the core does not reorder).

```bash
export LILLA_EXTENSIONS=lilla_core.extensions.google_oauth,lilla_core.extensions.google_calendar
export GOOGLE_CLIENT_SECRET=...
```

You can also load OAuth alone, without Calendar, just for your own Google API extension
(Tasks and so on). In that case list only `lilla_core.extensions.google_oauth` and put
your extension after it.

## Configuration

Write one section per extension under `extensions:` in `lilla.yaml`. OAuth settings and
the calendar list are kept apart. Every field in both sections has a default, so the
bot starts without them (but you cannot authenticate without `client_id`).

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
```

| Setting | Where | Meaning |
|---------|-------|---------|
| `extensions.google_oauth.client_id` | `lilla.yaml` | OAuth client ID from Google Cloud |
| `extensions.google_oauth.redirect_uri` | `lilla.yaml` | Where the authorization code comes back (default `http://localhost:8765/oauth/google-oauth/callback`) |
| `GOOGLE_CLIENT_SECRET` | `.env` / OS environment | Client secret (`cfg.env.google_client_secret`) |
| `extensions.google_calendar.calendars` | `lilla.yaml` | A list of `id` / `friendly_name` pairs |

If a section is left behind for an extension you did not load, the core treats it as a
leftover and fails at startup (when you drop `google_calendar`, remove
`extensions.google_calendar` too).

The time zone is the core's `ui.timezone`; the Calendar extension has no time zone
setting of its own. The same IANA name is used as `timeZone` for timed events, so it
is a good idea to set `ui.timezone` explicitly ([Time zone](timezone.md)).

## Google Cloud setup

1. Create an OAuth client (web application) and register the same value as
   `redirect_uri` under "Authorized redirect URIs"
2. Enable the APIs you use (the Google Calendar API for Calendar)

Calendar requests a single scope, `https://www.googleapis.com/auth/calendar.events`
(read and write events).

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

| Tool | Function name | What it does |
|------|---------------|--------------|
| `llm_calendar_get` | `get_calendar_events` | Reads events by `date_range` (`today` / `last_7_days` / `2026-04-20/2026-04-26` and so on), `query`, and `max_results`. Attendees are never passed to the LLM, and IDs of registered calendars are replaced with their `friendly_name` |
| `llm_calendar_create` | `create_calendar_event` | Creates an event in a calendar given by a registered `friendly_name` (all-day or timed). It never writes to a calendar that is not registered |

When `calendars` is empty, the read tool falls back to `calendar_ids` in the tool
execution context (or `primary`) for backward compatibility.

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
