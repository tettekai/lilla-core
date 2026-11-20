# Dashboard contributions

> This page is a translation of the [Japanese original](../ja/extension-dashboard.md). If
> the two differ, the Japanese version is authoritative.

An extension may add one tab plus HTTP routes to [the observability dashboard](dashboard.md).
The only identifier is `Extension.name`: the core derives the hash, the API prefix, the
public callback prefix and the static URL from it, so there is no separate id field. For
`name = "google-oauth"`:

| Purpose | Value |
|---------|-------|
| Primary hash | `#/google-oauth` |
| Admin hash | `#/admin/google-oauth` |
| Session API | `/api/google-oauth` |
| Public callback | `/oauth/google-oauth/callback` |
| Static files | `/static/ext/google-oauth/` |
| JS module | `/static/ext/google-oauth/page.js` |

```python
class MyExtension(Extension):
    name = "my-pack"

    def dashboard_page(self) -> DashboardPage | None:
        return DashboardPage(label="My Pack", group="main")

    def dashboard_static_dir(self) -> Path | None:
        return Path(__file__).parent / "dashboard"

    def dashboard_routes(self) -> list[DashboardRoute]:
        return [DashboardRoute("GET", "/api/my-pack/items", handle_items)]
```

The collected declarations are read through `get_dashboard_pages()` (derived
`DashboardPageEntry` objects), `get_dashboard_static_mounts()`,
`get_dashboard_routes()` and `get_dashboard_public_routes()`, all in load order.
Mounting these is the core's own dashboard server (`handlers/dashboard_server.py`),
which `bot.py`'s `main()` starts after the extensions' `setup()` hooks.

## The tab's JS module

An extension that returns a `dashboard_page()` puts `page.js` at the root of its
`dashboard_static_dir()`. When the tab is opened, the SPA `import()`s it and calls
`mount(el, ctx)`; when the user leaves the screen it calls `unmount()` (exporting
`unmount` is optional). `ctx` carries `authedFetch` and `formatDate`.

## Display name and the link to settings

`Extension.display_name()` returns the display name shown on the dashboard's settings page (the
extension list and headings). The default is `None`, in which case `name` is used. If you return
one, it must be a non-empty string (anything else fails fast at load time). The label of the
navigation tab is still `DashboardPage.label`.

```python
    def display_name(self) -> str | None:
        return "My extension"
```

When an extension with a tab also has something editable on the settings page (on-screen
settings, a default prompt, or bundled scheduled tasks), its entry in `GET /api/dashboard/nav`
carries `settings_hash` (`#/settings/extensions/{name}`; `#/settings/{name}` for a built-in
feature), and the SPA shows a "⚙ Settings" link above the tab. Conversely, the settings page
links back to the tab with "Open <display name>".

## Name and path validation

Because `name` ends up verbatim in URLs, a name that does not match
`^[a-z0-9][a-z0-9-]*$`, or one of the core's reserved names (`api`, `oauth`,
`static`, `admin`, `dashboard`, `auth`, `login`, `logout`, `setup`, `home`,
`conversations`, `memos`, `skills`, `usage`, `logs`, `extensions`, `settings`), fails at load time. So does a route path
outside the extension's own prefix, or a tab declared without a
`dashboard_static_dir()`.

## Public routes

Routes declared through `dashboard_public_routes()` are reachable by **anyone**.
Even when an upstream access control only lets public callbacks through,
validating `state` is the extension's responsibility; anything that needs
authentication belongs in `dashboard_routes()`.
