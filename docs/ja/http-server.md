# 共有 HTTP サーバー

コアは起動時に、ホストのアプリ（モバイル / デスクトップのクライアントなど）やローカルの
スクリプトが HTTP で叩くための共有 HTTP サーバーを立ち上げます。人がパスワードで
ログインする [観測用ダッシュボード](dashboard.md) とは **別のポート・別の認証**
（Bearer トークン）です。有効無効のフラグはなく、設定は `lilla.yaml` の `http:` です。
節そのものを省略すると既定値になります。

```yaml
http:
  host: "0.0.0.0"          # listen するアドレス（既定）
  port: 8080               # listen するポート（既定）
  cors_allowed_origins: [] # CORS を許可するオリジン（既定は空＝CORS ヘッダーを付けない）
```

`cors_allowed_origins` に `"*"` を含めると全オリジンを許可します。それ以外はリクエストの
`Origin` と一致したときだけ、そのオリジンを返します。

## コアのエンドポイント

| メソッド・パス | 認証 | 内容 |
|----------------|------|------|
| `GET /` | 公開 | 生存確認。MongoDB 疎通だけを見て `200 {"status": "ok"}` / `503 {"status": "unhealthy"}` を返す（詳細は返さず、サーバーログにだけ出す） |
| `POST /api/tools/call` | Bearer | LLM ツールを直接呼ぶ。ボディは `{"tool": "<名前>", "parameters": {...}}`。名前は YAML の stem でも SCHEMA の関数名でもよい。結果は `{"success": true, "tool_name": ..., "data": ...}` か `{"success": false, "tool_name": ..., "error": ...}` |
| `POST /api/runtask` | Bearer | task ツールを手動実行する（`!runtask` 相当）。ボディは `{"tool": "<名前>", "params": {...}}`。実行は待たずに `202` を返す |

## 認証

既定は拒否です。ルートごとに次のどれかの認証方式を持ちます。

- `bearer`（既定）: `Authorization: Bearer <トークン>` 必須。ヘッダー無し・形式違い・
  未登録トークンはいずれも `401 {"error": "unauthorized"}`
- `public`: 認証なしで誰でも叩ける
- `deferred`: 認証ミドルウェアは素通しし、ハンドラ側が別方式で保護する
  （WebSocket の接続後認証など。「認証不要」ではありません）

どのルートにもマッチしないパスも Bearer 扱いです。トークン無しでは `404` ではなく `401` を
返すので、パスの有無は外から分かりません。

CORS のミドルウェアは認証の **外側** にあり、`cors_allowed_origins` を設定していれば
`OPTIONS` のプリフライトに認証より前に `200` で答えます（ブラウザはプリフライトに
`Authorization` を付けないため）。

### トークン

トークンは MongoDB の `client_tokens` コレクションに **SHA-256 ハッシュだけ** を保存し、
平文は保存しません。有効期限はなく、同じ `label` で再発行すると古いトークンは置き換わります
（＝失効）。インデックスはこのサーバーの起動時に作ります。

発行の CLI はコアには含めません。ホストが `lilla_core.repository.client_token_repository` の
関数で用意します。

```python
import secrets
from lilla_core.repository.client_token_repository import (
    get_client_token_repo,
    hash_client_token,
)

token = secrets.token_urlsafe(32)
repo = get_client_token_repo()
await repo.ensure_indexes()
await repo.replace(hash_client_token(token), "my-client")  # 同じ label の古いトークンは消える
print(token)  # 平文はこの場でしか見えない
```

照合は `verify_client_token(token)` です。拡張が `deferred` で載せたルート
（WebSocket など）も、同じ関数でトークンを照合できます。

> **セキュリティ上の注意**
>
> - 既定の `host` は全インターフェース（`0.0.0.0`）です。公開ルート（`GET /` と、拡張が
>   `public` で申告したもの）は誰でも叩けます。同一ホストからしか使わない運用なら
>   `host: 127.0.0.1` に絞ってください
> - `POST /api/tools/call` と `POST /api/runtask` はツールを実行できます。トークンは
>   パスワードと同じ扱いで保管し、漏れたら同じ `label` で再発行してください

## 拡張からルートを足す

拡張は `Extension.http_routes()` で `HttpRoute` のリストを返すと、このサーバーにルートを
足せます。ダッシュボードの `dashboard_routes()` と違って、パスに接頭辞の制約はありません。

```python
from aiohttp import web
from lilla_core.core.extension import Extension, HttpRoute


async def handle_status(request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


class MyExtension(Extension):
    name = "my-ext"

    def http_routes(self) -> list[HttpRoute]:
        return [
            HttpRoute("GET", "/my/status", handle_status),                    # Bearer 必須
            HttpRoute("GET", "/my/files/{name}", handle_file, auth="public"),  # 公開
            HttpRoute("GET", "/my/ws", handle_ws, auth="deferred"),            # ハンドラ側で認証
        ]


extension = MyExtension()
```

- ハンドラは aiohttp のハンドラ（`request` 1 つを受け取る非同期関数）です。コアのツール
  レジストリや Discord クライアントは渡しません
- パスには aiohttp のパステンプレート（`{name}` など）を書けます
- GET のルートには HEAD も同じ認証方式で自動的に載ります
- コアのルートや他の拡張と同じメソッド・パスを申告すると、ロード時に失敗します
  （HEAD は GET、`*` は全メソッドと重なるとみなします。パスはテンプレートの文字列として
  完全一致で比べます）
- `public` にするかどうかは申告する拡張が決めます。コアは「実行系のルートは Bearer 必須」の
  ような強制はしません（拡張は信頼境界の内側のコードです）
