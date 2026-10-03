# The shared HTTP server

> This page is a translation of the [Japanese original](../ja/http-server.md). If the two
> differ, the Japanese version is authoritative.

At startup the core brings up a shared HTTP server for host apps (mobile / desktop
clients and the like) and local scripts to call over HTTP. It runs on a **separate port
with separate authentication** (Bearer tokens) from the
[observability dashboard](dashboard.md), where a person logs in with a password. There is
no enable/disable flag; it is configured under `http:` in `lilla.yaml`. Omit the section
entirely to get the defaults.

```yaml
http:
  host: "0.0.0.0"          # address to listen on (default)
  port: 8080               # port to listen on (default)
  cors_allowed_origins: [] # origins allowed for CORS (default empty = no CORS headers)
```

Including `"*"` in `cors_allowed_origins` allows every origin. Otherwise the request's
`Origin` is echoed back only when it matches an entry.

## Core endpoints

| Method and path | Auth | What it does |
|-----------------|------|--------------|
| `GET /` | public | Liveness. Checks MongoDB connectivity only and returns `200 {"status": "ok"}` / `503 {"status": "unhealthy"}` (no details; those go to the server log only) |
| `GET /api/selftest` | Bearer | Self-diagnosis (the `!selftest` equivalent). Add `?full=true` to also check LLM connectivity (which costs money). See the section below |
| `POST /api/tools/call` | Bearer | Calls an LLM tool directly. Body: `{"tool": "<name>", "parameters": {...}}`. The name may be the YAML stem or the SCHEMA function name. Returns `{"success": true, "tool_name": ..., "data": ...}` or `{"success": false, "tool_name": ..., "error": ...}` |
| `POST /api/runtask` | Bearer | Runs a task tool by hand (the `!runtask` equivalent). Body: `{"tool": "<name>", "params": {...}}`. Returns `202` without waiting for the run |

## Self-diagnosis (`GET /api/selftest`)

Runs the same checks as Discord's `!selftest` over HTTP. It is meant for mechanical smoke
tests run locally and **requires authentication** (Bearer). The set of checks lives in one
place only — `run_selftest_checks()` in `services/system_checks.py` — so Discord and HTTP
can never disagree.

- Normal (no query): process liveness, MongoDB connectivity, command registry count,
  task tool count
- `?full=true`: all of the above plus an LLM connectivity check. **This costs one LLM API
  round trip every time.** (Truthy values for `full` are `true` / `1` / `yes` / `on`, plus a
  bare `?full` with no value; case does not matter. Anything else means normal mode.)

This endpoint does nothing to the container or the process; it only reports.

```console
$ curl -sS -H "Authorization: Bearer $TOKEN" http://localhost:8080/api/selftest
```

```json
{
  "ok": true,
  "mode": "normal",
  "summary": {"ok": 4, "total": 4},
  "checks": [
    {"name": "process_alive", "ok": true, "detail": "...", "elapsed_ms": 0.0},
    {"name": "mongodb", "ok": true, "detail": "...", "elapsed_ms": 2.3},
    {"name": "command_registry", "ok": true, "detail": "...", "elapsed_ms": 0.0},
    {"name": "task_tools", "ok": true, "detail": "...", "elapsed_ms": 0.0}
  ]
}
```

`checks` corresponds to the `!selftest` attachment (`selftest_result.txt`) and `summary` to
the summary in the Discord message body. The `detail` strings come from the `ui.locale`
catalog.

The status code is `200` when every check passes and `503` when at least one fails (the
body has the same shape either way, so both `curl -f` and `jq -e .ok` work).

> This has a different job from the `GET /` liveness check. `GET /` only looks at MongoDB
> connectivity, as material for deciding whether to restart, and returns no details because
> it is unauthenticated. Diagnostic details (`detail`) are only returned by this endpoint,
> which sits inside authentication.

## Authentication

The default is deny. Every route has one of these auth modes:

- `bearer` (default): `Authorization: Bearer <token>` is required. A missing header, a
  different scheme, or an unknown token all get `401 {"error": "unauthorized"}`
- `public`: anyone can call it without authentication
- `deferred`: the auth middleware lets the request through and the handler protects it
  some other way (e.g. a WebSocket that authenticates after connecting). This does not
  mean "no auth"

A path that matches no route is treated as Bearer too. Without a token it gets `401`
rather than `404`, so outsiders cannot probe which paths exist.

The CORS middleware sits **outside** authentication. When `cors_allowed_origins` is set,
it answers `OPTIONS` preflights with `200` before authentication (browsers do not send
`Authorization` on a preflight).

### Tokens

Tokens are stored in the MongoDB `client_tokens` collection as **SHA-256 hashes only**;
the plaintext is never stored. They do not expire. Reissuing with the same `label`
replaces (and so revokes) the old token. The indexes are created when this server starts.

Issue tokens with the bundled CLI. Run it with the same environment variables as the bot
(`CONFIG_ROOT` and so on).

```bash
python -m lilla_core.scripts.issue_client_token
# inside a container (-it is required: the overwrite prompt reads stdin)
docker exec -it <container> python -m lilla_core.scripts.issue_client_token
```

- The default issue target name (`--label`) is `default`. Use `--label my-client` for another name
- If a token with the same `label` already exists, you are asked to confirm (`y/N`). On
  yes the old token is replaced (1 label = 1 token); `-y` / `--yes` skips the prompt
- The plaintext token is shown **only once**, at issue time (only the hash is stored)
- If you already issued a token under a different `label` (a host-specific name, say), pass the
  same name with `--label`, or reissue as `default` and update the client's settings

To issue from code, use `get_client_token_repo()`, `hash_client_token()` and `replace()` in
`lilla_core.repository.client_token_repository` directly.

Verification is `verify_client_token(token)`. Routes an extension mounts as `deferred`
(WebSockets and the like) can check tokens with the same function.

> **Security notes**
>
> - The default `host` is every interface (`0.0.0.0`). Public routes (`GET /` and anything
>   an extension declares `public`) can be called by anyone. If you only use it from the
>   same host, narrow it to `host: 127.0.0.1`
> - `POST /api/tools/call` and `POST /api/runtask` can run tools. Keep tokens as safe as
>   passwords and reissue with the same `label` if one leaks

## Adding routes from an extension

An extension adds routes to this server by returning a list of `HttpRoute` from
`Extension.http_routes()`. Unlike the dashboard's `dashboard_routes()`, paths have no
prefix restriction.

```python
from aiohttp import web
from lilla_core.core.extension import Extension, HttpRoute


async def handle_status(request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


class MyExtension(Extension):
    name = "my-ext"

    def http_routes(self) -> list[HttpRoute]:
        return [
            HttpRoute("GET", "/my/status", handle_status),                    # Bearer required
            HttpRoute("GET", "/my/files/{name}", handle_file, auth="public"),  # public
            HttpRoute("GET", "/my/ws", handle_ws, auth="deferred"),            # handler authenticates
        ]


extension = MyExtension()
```

- Handlers are aiohttp handlers (async functions taking one `request`). The core does not
  pass its tool registries or the Discord client
- Paths may use aiohttp path templates (`{name}` and so on)
- A GET route automatically gets a HEAD route with the same auth mode
- Declaring the same method and path as a core route or another extension fails at load
  time (HEAD overlaps GET, and `*` overlaps every method; paths are compared as template
  strings, exact match)
- Whether a route is `public` is up to the declaring extension. The core does not force
  rules like "routes that run things must require Bearer" (extensions are code inside the
  trust boundary)
