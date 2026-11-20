# ダッシュボードへの差し込み

拡張は [観測用ダッシュボード](dashboard.md) にタブを 1 つと、HTTP ルートを足せます。
経路の識別子は `Extension.name` ただ 1 つで、ハッシュ・API 接頭辞・公開コールバック・
静的 URL はすべてコアが `name` から導出します（新しい ID 欄はありません）。
`name = "google-oauth"` のとき次のようになります。

| 用途 | 値 |
|------|-----|
| 常用ハッシュ | `#/google-oauth` |
| 管理ハッシュ | `#/admin/google-oauth` |
| セッション API | `/api/google-oauth` |
| 公開コールバック | `/oauth/google-oauth/callback` |
| 静的ファイル | `/static/ext/google-oauth/` |
| JS モジュール | `/static/ext/google-oauth/page.js` |

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

コアが集めた結果は `get_dashboard_pages()`（導出済みの `DashboardPageEntry`）・
`get_dashboard_static_mounts()`・`get_dashboard_routes()`・`get_dashboard_public_routes()`
から、いずれもロード順で読めます。これらを実際に載せるのはコアのダッシュボード
サーバー（`handlers/dashboard_server.py`）で、`bot.py` の `main()` が拡張の `setup()` の
あとに起こします。

## タブの JS モジュール

`dashboard_page()` を返す拡張は、`dashboard_static_dir()` の直下に `page.js` を置きます。
SPA はタブを開いたときにこれを `import()` し、`mount(el, ctx)` を呼びます。画面を
離れるときは `unmount()` を呼びます（`unmount` の export は任意）。`ctx` には
`authedFetch` と `formatDate` が入ります。

## 表示名と設定へのリンク

`Extension.display_name()` で、ダッシュボードの設定画面（拡張機能の一覧・見出し）に出す表示名を
返せます（既定は `None` で、そのときは `name` を使います）。返すなら空でない文字列にしてください
（それ以外はロード時に fail-fast します）。ナビのタブのラベルは従来どおり `DashboardPage.label` です。

```python
    def display_name(self) -> str | None:
        return "マイ拡張"
```

タブを持つ拡張が設定画面で編集できるもの（画面管理の設定・デフォルトのプロンプト・同梱した
定期タスク）を持つときは、`GET /api/dashboard/nav` のその項目に `settings_hash`
（`#/settings/extensions/{name}`。標準機能なら `#/settings/{name}`）が付き、SPA がタブの上に
「⚙ 設定」のリンクを出します。逆に設定画面からは「〈表示名〉を開く」でタブへ戻れます。

## 名前とパスの検証

`name` は URL にそのまま埋まるため、`^[a-z0-9][a-z0-9-]*$` に合わない名前と、
コアが押さえている予約名（`api` / `oauth` / `static` / `admin` / `dashboard` /
`auth` / `login` / `logout` / `setup` / `home` / `conversations` / `memos` / `skills` / `usage` / `logs` / `extensions` / `settings`）は
ロード時に fail-fast します。ルートのパスが自分の接頭辞の外にある場合、タブを出すのに
`dashboard_static_dir()` を返していない場合も同様です。

## 公開ルートの注意

`dashboard_public_routes()` に載せたルートは **誰でも叩けます**。ホスト前段の
アクセス制御で公開コールバックだけを通す構成でも、`state` の検証は拡張側の責任です。
認証が要る処理は `dashboard_routes()` へ置いてください。
