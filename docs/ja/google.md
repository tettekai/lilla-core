# Google OAuth / Calendar / Drive（公式拡張パック）

コアには公式拡張パックとして、Google OAuth2・Google Calendar・Google Drive / Sheets の拡張を同梱しています。
どれも外部の拡張と同じ `Extension` 契約だけで書かれており、`LILLA_EXTENSIONS` に
import パスを並べたときだけ読み込まれます。並べなければ今までどおりコア単体で起動します
（別パッケージや extras は不要です）。

| import パス | `name` | 役割 |
|-------------|--------|------|
| `lilla_core.extensions.google_oauth` | `google-oauth` | OAuth2 のトークン取得・更新、認可フローの開始、認可コードの戻り先（コールバック）。他の Google API 拡張が継承する `GoogleOAuthClient` を提供する |
| `lilla_core.extensions.google_calendar` | `google-calendar` | Google Calendar のクライアントと、予定の取得・作成の LLM ツール。`requires = ("google-oauth",)` |
| `lilla_core.extensions.google_drive` | `google-drive` | Google Drive のテキストファイルの検索・取得・作成・上書きと、Google Sheets の範囲の読み取りの LLM ツール。`requires = ("google-oauth",)` |

## 有効化

`google_oauth` を、それを `requires` する拡張より **前** に並べます（順序違反は起動時に
落ちます。コアは並べ替えません）。

```bash
export LILLA_EXTENSIONS=google_oauth,google_calendar,google_drive
export GOOGLE_CLIENT_SECRET=...
```

使わない拡張は並べなくて構いません（Drive だけなら `google_oauth,google_drive`）。

公式拡張パックはモジュール名（`google_oauth` / `google_calendar` / `google_drive`）だけの短縮記法で
指定できます。上は次と同じ意味です（フルパスの指定は今までどおり使えます）。

```bash
export LILLA_EXTENSIONS=lilla_core.extensions.google_oauth,lilla_core.extensions.google_calendar,lilla_core.extensions.google_drive
```

短縮記法が効くのは `.` を含まない名前だけです。`.` を含む書き方（第三者パックや、
すでにフルパスで書いた公式拡張）はそのままのパスとして扱われ、公式拡張パックへの
補完は行いません。

Calendar を使わず、自作の Google API 拡張（Tasks など）のためだけに OAuth を載せることも
できます。その場合は `lilla_core.extensions.google_oauth` だけを並べ、自作の拡張を
その後ろに置きます。

## 設定

`lilla.yaml` の `extensions:` の下に、拡張ごとに節を分けて書きます。OAuth の項目と
カレンダー一覧・Drive のホワイトリストは混ぜません。どの節も全項目に既定値があるため、
省略しても起動はします（ただし `client_id` が無いと認証できず、Drive はホワイトリストが
空だとすべて拒否します）。

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
  google_drive:
    # アクセスを許すフォルダの ID（URL の /folders/ の後ろ）。配下すべてが対象になる。
    read_only_folders:
      - "1AbCdEfGhIjKlMnOpQrStUvWxYz"
    writable_folders:
      - "1ZyXwVuTsRqPoNmLkJiHgFeDcBa"
```

| 設定 | 置き場所 | 内容 |
|------|----------|------|
| `extensions.google_oauth.client_id` | `lilla.yaml` | Google Cloud の OAuth クライアント ID |
| `extensions.google_oauth.redirect_uri` | `lilla.yaml` | 認可コードの戻り先（既定 `http://localhost:8765/oauth/google-oauth/callback`） |
| `GOOGLE_CLIENT_SECRET` | `.env` / OS 環境変数 | クライアントシークレット（`cfg.env.google_client_secret`） |
| `extensions.google_calendar.calendars` | `lilla.yaml` | `id` と `friendly_name` の組のリスト |
| `extensions.google_drive.read_only_folders` | `lilla.yaml` | 読み取りだけを許すフォルダ ID のリスト（既定は空） |
| `extensions.google_drive.writable_folders` | `lilla.yaml` | 読み取りと書き込みを許すフォルダ ID のリスト（既定は空） |
| `extensions.google_drive.write_scope` | `lilla.yaml` | 書き込みのスコープ。`file`（既定）か `full`（[Drive / Sheets のツール](#drive--sheets-のツール)を参照） |
| `extensions.google_drive.max_read_chars` | `lilla.yaml` | テキストの本文・Sheets の値を LLM へ返す上限の文字数（既定 `100000`。超えた分は切り詰める） |

拡張をロードしていないのに節が残っていると、コアは払い残しとして起動時に落とします
（`google_calendar` を外すなら `extensions.google_calendar` も、`google_drive` を外すなら
`extensions.google_drive` も消します）。

タイムゾーンはコアの `ui.timezone` をそのまま使います。Calendar 拡張は独自の TZ 設定を
持ちません。時間指定の予定を作るときの `timeZone` にもこの IANA 名が入るため、
`ui.timezone` を明示しておくことをおすすめします（[タイムゾーン](timezone.md)）。

## Google Cloud 側の準備

1. OAuth クライアント（ウェブアプリケーション）を作成し、「承認済みのリダイレクト URI」に
   `redirect_uri` と同じ値を登録します
2. 使う API（Calendar なら Google Calendar API、Drive なら Google Drive API と
   Google Sheets API）を有効にします

Calendar が要求するスコープは `https://www.googleapis.com/auth/calendar.events`
（予定の読み書き）だけです。Drive が要求するスコープは
[Drive / Sheets のツール](#drive--sheets-のツール) を参照してください。

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

ツール本体は `google-calendar` 拡張の中にあり、YAML は利用者の `${CONFIG_ROOT}/tools/` に
置きます（拡張は YAML を同梱しません）。`type` はファイル名で解決されます。

```yaml
# ${CONFIG_ROOT}/tools/llm_calendar_get.yaml
type: llm_calendar_get
```

```yaml
# ${CONFIG_ROOT}/tools/llm_calendar_create.yaml
type: llm_calendar_create
```

通常会話で使うには、`lilla.yaml` の `tools.sets.main` にもこれらの stem を足します
（[ツールセット](tools.md#ツールセットtoolssets)）。

| ツール | 関数名 | 内容 |
|--------|--------|------|
| `llm_calendar_get` | `get_calendar_events` | `date_range`（`today` / `last_7_days` / `2026-04-20/2026-04-26` など）・`query`・`max_results` で予定を取得する |
| `llm_calendar_create` | `create_calendar_event` | `calendars` に登録済みの `friendly_name` を指定して予定を作る（終日・時間指定の両方）。登録に無いカレンダーへは書き込まない |

`calendars` が空のとき、取得ツールは後方互換としてツール実行 context の `calendar_ids`
（無ければ `primary`）を使います。

### ツールの戻り値

どちらのツールも、Calendar API の予定をそのまま LLM へ渡さず、次の項目だけに絞って返します
（取得ツールは予定ごとに 1 つ、作成ツールは作成した予定 1 つ）。

| 項目 | 内容 |
|------|------|
| `calendar` | 取得元（作成先）のカレンダーの `friendly_name`。`calendars` が空の後方互換モードでは、`calendar_ids` の並び順に `calendar_1` / `calendar_2` … |
| `summary` | タイトル（無ければ空文字） |
| `start` / `end` | 終日なら `YYYY-MM-DD`、時間指定なら ISO 8601 の日時。終日の `end` は最終日を含む日付（作成ツールの `end` と同じ） |
| `all_day` | 終日の予定かどうか |
| `status` / `location` / `description` | 値があるときだけ |

主催者・作成者・参加者（`organizer` / `creator` / `attendees`）や `htmlLink` / `iCalUID` / `id`
などそれ以外の項目は返しません。これらにはアカウントのメールアドレスやカレンダー ID が入って
いるためです。`location` / `description` の本文は利用者が書いたものとしてそのまま返します
（本文中のアドレスは伏せません）。

## Drive / Sheets のツール

ツール本体は `google-drive` 拡張の中にあり、YAML は利用者の `${CONFIG_ROOT}/tools/` に
置きます（Calendar と同じく同梱しません）。使うものだけ置けば十分です。

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

| ツール | 内容 |
|--------|------|
| `llm_google_drive_search_text` | `query`（必須）でファイル名と本文を全文検索し、ホワイトリスト配下の `text/*` だけを最大 `limit` 件（既定 10・上限 50）返す。各要素は `id` / `name` / `mime_type` / `modified_time` / `writable` / `size` |
| `llm_google_drive_get_text` | `file_id` のテキストファイルの本文を返す（`max_read_chars` を超えたら切り詰めて `truncated: true`） |
| `llm_google_drive_write_text` | `file_id` を指定すると既存のテキストファイルを `content` で丸ごと上書きし、指定しなければ `name` と `parent_folder_id`（どちらも必須）で新規作成する。書き込み可能フォルダの配下だけ |
| `llm_google_sheets_get_range` | `spreadsheet_id` と `range`（A1 記法）でスプレッドシートのセルの値を読む（読み取り専用。検索ツールは無いので ID は利用者が伝える） |

### 扱うファイルの範囲

- 対象はホワイトリストのフォルダ（`read_only_folders` / `writable_folders`）とその配下すべてです。
  判定はファイルの親を辿り、最も近いホワイトリストのフォルダで決めます（書き込み可能フォルダの
  中に読み取り専用フォルダを置けば、その配下は読み取り専用になります）。同じ ID を両方に書くと
  起動時に落ちます
- 共有ドライブは、ドライブの ID をホワイトリストに書けば対象になります（特別な設定は不要です）
- テキストファイルとして扱うのは MIME タイプが `text/*` のものだけで、それ以外（Google ドキュメント・
  JSON・Drive が `application/octet-stream` と判定したファイルなど）とゴミ箱のファイルは、
  ホワイトリスト外と同じく「存在しない」として扱います
- 新規作成するファイルの MIME タイプは名前から決めます（`.md` は `text/markdown`、`text/*` に
  ならない名前は `text/plain`）。上書きでは名前・置き場所・MIME タイプを変えません

### スコープと `write_scope`

| 構成 | 要求するスコープ |
|------|------------------|
| `writable_folders` が空 | `drive.readonly` と `spreadsheets.readonly` |
| `writable_folders` あり・`write_scope: file`（既定） | 上に `drive.file` を足す |
| `writable_folders` あり・`write_scope: full` | `drive` と `spreadsheets.readonly` |

- `file`（`drive.file`）では、Google 側の制限で **この拡張が作ったファイルしか上書きできません**。
  書き込み可能フォルダに元からあるファイルは読めますが、上書きしようとするとその旨のエラーになります。
  また、Lilla が作っていないフォルダ（Drive の画面で作ったフォルダなど）への新規作成も、Google 側で
  拒否される可能性があります（未検証です。拒否されたときはその可能性を示すエラーを返します）。
  既存のフォルダへの作成や既存ファイルの上書きが必要なら `full` を使ってください
- `full`（`drive`）では書き込み可能フォルダ配下の既存ファイルも上書きできます。ただしトークン自体は
  ドライブ全体を読み書きできる権限を持ち、範囲を絞っているのはこの拡張のホワイトリストだけです。
  トークンが漏れた場合の影響が大きくなる点を理解したうえで選んでください
- 設定を変えて必要なスコープが増えると（書き込み可能フォルダを足した・`full` にした）、
  次にツールを使ったときに再認証を求めます（保存済みのトークンが許可されたスコープを確かめるため）

### 注意

- 読んだファイルやセルの内容はそのまま LLM へ渡ります。他人と共有しているフォルダをホワイトリストに
  入れると、その中に書かれた文章で LLM の振る舞いを誘導される（プロンプトインジェクション）おそれが
  あります。書き込み可能フォルダを組み合わせる場合は特に、信頼できる内容のフォルダだけを指定してください
- 書き込みは上書き保存で、競合の検出はしません。Drive の版の履歴から戻せます

## 他の Google API を足す（`GoogleOAuthClient`）

Tasks や Health など、公式拡張パックに無い Google API は自分の拡張で足します。クライアントは
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
