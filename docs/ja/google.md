# Google OAuth / Google Calendar（公式パック）

コアには公式パックとして、Google OAuth2 と Google Calendar の拡張を同梱しています。
どちらも外部の拡張と同じ `Extension` 契約だけで書かれており、`LILLA_EXTENSIONS` に
import パスを並べたときだけ読み込まれます。並べなければ今までどおりコア単体で起動します
（別パッケージや extras は不要です）。

| import パス | `name` | 役割 |
|-------------|--------|------|
| `lilla_core.extensions.google_oauth` | `google-oauth` | OAuth2 のトークン取得・更新、認可フローの開始、認可コードの戻り先（コールバック）。他の Google API 拡張が継承する `GoogleOAuthClient` を提供する |
| `lilla_core.extensions.google_calendar` | `google-calendar` | Google Calendar のクライアントと、予定の取得・作成の LLM ツール。`requires = ("google-oauth",)` |

## 有効化

`google_oauth` を、それを `requires` する拡張より **前** に並べます（順序違反は起動時に
落ちます。コアは並べ替えません）。

```bash
export LILLA_EXTENSIONS=lilla_core.extensions.google_oauth,lilla_core.extensions.google_calendar
export GOOGLE_CLIENT_SECRET=...
```

Calendar を使わず、自作の Google API 拡張（Tasks など）のためだけに OAuth を載せることも
できます。その場合は `lilla_core.extensions.google_oauth` だけを並べ、自作の拡張を
その後ろに置きます。

## 設定

`lilla.yaml` の `extensions:` の下に、拡張ごとに節を分けて書きます。OAuth の項目と
カレンダー一覧は混ぜません。どちらの節も全項目に既定値があるため、省略しても起動は
します（ただし `client_id` が無いと認証できません）。

```yaml
extensions:
  google_oauth:
    client_id: "xxxxxxxx.apps.googleusercontent.com"
    # Google Cloud の「承認済みのリダイレクト URI」と一致させる。既定値は下記。
    redirect_uri: http://localhost:8765/oauth/google-oauth/callback
  google_calendar:
    # 予定の取得・作成の対象。作成はここに登録したカレンダーにしか書き込まない。
    calendars:
      - id: primary
        friendly_name: 個人のカレンダー
      - id: xxxxxxxx@group.calendar.google.com
        friendly_name: 家族
```

| 設定 | 置き場所 | 内容 |
|------|----------|------|
| `extensions.google_oauth.client_id` | `lilla.yaml` | Google Cloud の OAuth クライアント ID |
| `extensions.google_oauth.redirect_uri` | `lilla.yaml` | 認可コードの戻り先（既定 `http://localhost:8765/oauth/google-oauth/callback`） |
| `GOOGLE_CLIENT_SECRET` | `.env` / OS 環境変数 | クライアントシークレット（`cfg.env.google_client_secret`） |
| `extensions.google_calendar.calendars` | `lilla.yaml` | `id` と `friendly_name` の組のリスト |

拡張をロードしていないのに節が残っていると、コアは払い残しとして起動時に落とします
（`google_calendar` を外すなら `extensions.google_calendar` も消します）。

タイムゾーンはコアの `ui.timezone` をそのまま使います。Calendar 拡張は独自の TZ 設定を
持ちません。時間指定の予定を作るときの `timeZone` にもこの IANA 名が入るため、
`ui.timezone` を明示しておくことをおすすめします（[タイムゾーン](timezone.md)）。

## Google Cloud 側の準備

1. OAuth クライアント（ウェブアプリケーション）を作成し、「承認済みのリダイレクト URI」に
   `redirect_uri` と同じ値を登録します
2. 使う API（Calendar なら Google Calendar API）を有効にします

Calendar が要求するスコープは `https://www.googleapis.com/auth/calendar.events`
（予定の読み書き）だけです。

## 認証の流れ

1. ツールが Google API を呼んだときに有効なトークンが無ければ、クライアントが認可フローを
   開始し、ツールは認可 URL を LLM へ返します（`needs_auth`）
2. 利用者がその URL をブラウザで開いて許可すると、Google が `redirect_uri` へ戻します
3. 戻り先の `GET /oauth/google-oauth/callback` は観測用ダッシュボードのポート
   （`dashboard.port`）に載る公開ルートです。`state` を照合してからトークンを交換し、
   MongoDB の `credentials` に種別（`CREDENTIAL_TYPE`）ごとに保存します
4. 以後はリフレッシュトークンで自動更新します。更新できなくなったら 1 に戻ります

**戻り先はダッシュボードの認証の外側に載ります。** ブラウザが Google から戻ってくる
公開 GET なのでセッション Cookie を要求できないためです。代わりに「保存済みの `state` と
完全に一致すること」を必須にし、一致した直後に `state` を消して使い回しを防いでいます。
ダッシュボードのポートを外へ公開する場合の注意は [観測用ダッシュボード](dashboard.md) を
参照してください。

## カレンダーのツール

ツール本体はパック内にあり、YAML は利用者の `${CONFIG_ROOT}/tools/` に置きます
（パックは YAML を同梱しません）。`type` はファイル名で解決されます。

```yaml
# ${CONFIG_ROOT}/tools/llm_calendar_get.yaml
type: llm_calendar_get
```

```yaml
# ${CONFIG_ROOT}/tools/llm_calendar_create.yaml
type: llm_calendar_create
```

| ツール | 関数名 | 内容 |
|--------|--------|------|
| `llm_calendar_get` | `get_calendar_events` | `date_range`（`today` / `last_7_days` / `2026-04-20/2026-04-26` など）・`query`・`max_results` で予定を取得する。参加者（`attendees`）は LLM へ渡さず、登録済みカレンダーの ID は `friendly_name` に置き換える |
| `llm_calendar_create` | `create_calendar_event` | `calendars` に登録済みの `friendly_name` を指定して予定を作る（終日・時間指定の両方）。登録に無いカレンダーへは書き込まない |

`calendars` が空のとき、取得ツールは後方互換としてツール実行 context の `calendar_ids`
（無ければ `primary`）を使います。

## 他の Google API を足す（`GoogleOAuthClient`）

Tasks や Health など、パックに無い Google API は自分の拡張で足します。クライアントは
`GoogleOAuthClient` を継承し、`CREDENTIAL_TYPE` と `SCOPES` だけを定義します。
トークンの取得・更新、認証ヘッダ、認可フローの開始、コールバックは共有されます
（戻り先は増やしません）。

```python
from lilla_core.extensions.google_oauth.client import GoogleOAuthClient


class GoogleTasksClient(GoogleOAuthClient):
    CREDENTIAL_TYPE = "google_tasks"  # MongoDB の認証情報の種別。API ごとに一意にする
    SCOPES = ["https://www.googleapis.com/auth/tasks"]

    async def list_tasklists(self) -> dict:
        return await self._request_json(
            "https://tasks.googleapis.com/tasks/v1/users/@me/lists"
        )


client = GoogleTasksClient.from_config()  # extensions.google_oauth と GOOGLE_CLIENT_SECRET から組み立てる
```

その拡張は `google-oauth` の節と秘匿情報を読むため、依存を申告します。

```python
class GoogleTasksExtension(Extension):
    name = "google-tasks"
    requires = ("google-oauth",)

    def required_env_fields(self) -> list[str]:
        return ["google_client_secret"]
```

トークン切れは `ReauthenticationRequiredError` で届くので、ツールでは
`lilla_core.tool_support.tool_result.tool_reauth_required(client, ...)` で認可 URL を
返せます。
