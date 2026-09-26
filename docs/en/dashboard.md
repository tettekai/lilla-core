# The observability dashboard

> This page is a translation of the [Japanese original](../ja/dashboard.md). If the two
> differ, the Japanese version is authoritative.

At startup the core brings up an HTTP dashboard for reading conversation history, user
memos and logs. There is no enable/disable flag — if the bot runs, the terminal is open.
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

Only `/oauth/{extension name}` sits outside the auth middleware, for public GETs a
browser makes without a session (OAuth redirect targets and the like). An upstream access
control would bypass just that prefix.

If you forget the password, delete the record in MongoDB's `admin_credentials`
collection by hand; the setup screen then lets you set a new one. A new setup token is
printed to the log the next time the setup screen is opened (or at the next startup).

See [Dashboard contributions](extension-dashboard.md) for adding tabs and HTTP routes
from an extension.
