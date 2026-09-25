# 拡張の基本

拡張は `lilla_core.core.extension.Extension` を継承し、必要なメソッドだけを
オーバーライドして、モジュールから `extension` 属性として 1 つだけ export します。
各メソッドは「何も貢献しない」デフォルトを持つため、コアは単体でも動き続けます。

```python
from lilla_core.core.extension import Extension


class MyExtension(Extension):
    name = "my-extension"

    def tool_context_providers(self):
        return {"my_client": get_my_client}

    async def setup(self, ctx):
        await start_my_http_server(ctx.llm_tools, ctx.tools, ctx.bot)


extension = MyExtension()
```

作ったモジュールは `LILLA_EXTENSIONS` に並べて読み込みます
（[インストールと起動](getting-started.md#拡張を読み込んで起動する)）。

## メソッド一覧

| メソッド | 用途 |
|----------|------|
| `startup_repos` | `on_ready` で初期化する追加リポジトリファクトリ |
| `on_message` | `on_message` の冒頭で呼ばれる。`True` を返すと以降の処理を止める |
| `setup` | `bot.start()` の前に await される起動処理（HTTP サーバー等）。引数は `SetupContext` 1 つ（`tools` / `llm_tools` / `bot` / `config`）で、フィールドの追加は既存の拡張を壊さない |
| `result_deliveries` | `!toolresult` の配送先を `client_type` ごとに指定 |
| `client_prompt_providers` | `client_type` ごとのシステムプロンプト追記。形は `{client_type: [プロバイダ, ...]}` の加算式で、複数の拡張が同じ `client_type` に足せる。コアがロード順に空行区切りで連結する |
| `conversation_start_hooks` | 会話処理開始前に await される非同期フック。形は `{client_type: [フック, ...]}` でプロンプトと同じく加算式。各フックは `ConversationContext`（`client_type` / `client_state` / `discord_channel_id` / `llm_name`）1 つを受け取る |
| `tool_context_providers` | ツール実行 context への値の注入 |
| `tool_roots` | ツールの `.py` を探す追加ディレクトリ |
| `tool_config_roots` | 拡張が同梱する既定のツール YAML（`llm_*.yaml` / `task_*.yaml`）のディレクトリ。`${CONFIG_ROOT}/tools` に同じ stem の YAML があればそちらが丸ごと勝つ。同梱ツールを止めるにはそこに `enabled: false` の YAML を置く（[ツール契約](tools.md)） |
| `locale_dirs` | 拡張が同梱する UI 文言カタログ（`{locale}.yaml`）のディレクトリ。カタログのトップレベルキーはその拡張の `name` ただ 1 つでなければならない（[下記](#ロケールカタログ)） |
| `command_packages` | `@register_command` を探す追加パッケージ |
| `dashboard_page` | 観測用ダッシュボードへ足すタブ 1 つ（`DashboardPage(label, group)`）。`group` は `main`（常用ナビ）か `admin`（管理メニュー）。経路は申告せず `name` から導出する（[ダッシュボードへの差し込み](extension-dashboard.md)） |
| `dashboard_static_dir` | `/static/ext/{name}/` に載せる静的ファイルのディレクトリ。タブを出すなら直下に `page.js` を置く |
| `dashboard_routes` | セッション認証の内側に足す HTTP ルート（`DashboardRoute`）。パスは `/api/{name}` 配下のみ |
| `dashboard_public_routes` | 認証の外側に載せる公開ルート（OAuth の戻り先など）。パスは `/oauth/{name}` 配下のみ。`state` の検証は拡張側の責任 |
| `config_model` | この拡張が足す YAML セクションのモデル 1 つ（足さないなら `None`）。置き場は `extensions.<name のハイフンをアンダースコアにしたもの>`（`google-oauth` なら `cfg.extensions.google_oauth`）（[設定の合成](extension-config.md)） |
| `env_fields` | この拡張が `cfg.env` に足す秘匿フィールド |
| `required_env_fields` | 自分では提供しないが読む `cfg.env` のフィールド |
| `required_tool_context_keys` | 自分では提供しないが、自分のツールが読むツール context のキー |
| `requires`（クラス属性） | 依存する拡張の名前。ロード済みで、かつ `LILLA_EXTENSIONS` で自分より前に並んでいる必要がある（[下記](#拡張どうしの依存)） |
| `api_version`（クラス属性） | この拡張が書かれた `Extension` 契約のバージョン。既定はコアの現在の `EXTENSION_API_VERSION` で、受け付けない値はロード時に失敗する（[下記](#拡張契約の互換性)） |

## 衝突ルール

貢献キーは **拡張どうし** で衝突してはいけません。`Extension.name`・YAML セクション名・
env フィールド名・ツール context のキー・結果配送の `client_type`・コマンド名・複数ルートに
またがる同名ツールファイルのいずれも、静かに勝者を決めず起動時に fail-fast します。
クライアント固有プロンプトと会話開始フックだけは設計上の例外で、`client_type` ごとの
リストをロード順に連結するため、複数の拡張が同じクライアントへ足せます。

`client_type="discord"` だけは扱いが 2 点異なります。システムプロンプトはコアが内蔵
デフォルトを持ち、拡張が 1 つも出していないときだけそれを使います。`!toolresult` の
配送はコアが持つため拡張は登録できません。

## ロケールカタログ

拡張は `locale_dirs()` で Discord に見せる文言のカタログも同梱できます。各ディレクトリには
コアと同じ命名の `{locale}.yaml`（`ja.yaml` / `en.yaml` など）を置き、カタログの
**トップレベルのキーはその拡張の `name` ただ 1 つ** でなければなりません。

```yaml
# ja.yaml（name = "lilla-habits" の拡張）
lilla-habits:
  notify:
    title: 今日の習慣
```

呼び出しは `t("lilla-habits.notify.title")` になります。コアが自動で prefix を付ける
ことはせず、YAML 上のキーと `t()` に書くキーは同じ文字列です。

- コアがコア自身のカタログへ拡張のカタログをロード順に重ねるため、解決順は従来どおり
  「`ui.locale` → `ja` → キー名そのもの」です。`ja.yaml` しか同梱していない拡張でも
  `ui.locale: en` で例外になりません
- 1 つの拡張が複数のディレクトリを返した場合は、その拡張のノードをロード順に浅く
  マージします（同じキーは後のディレクトリが勝ちます）
- トップレベルキーが拡張名と異なるカタログや、拡張名がコアのトップレベルキー
  （`selftest` など）と衝突している場合は、拡張の登録時に `ValueError` で fail-fast
  します（`t()` 自体は従来どおり例外を投げません）
- 存在しないディレクトリは WARNING を出して（ロケールごとに 1 回）読み飛ばし、
  壊れた YAML は ERROR ログを出してそのロケール分だけ空として扱います

## `client_state`

`run_conversation()` を自分で呼ぶクライアント拡張は、任意のオブジェクトを
`client_state` として渡せます（接続中ソケットの集合など）。コアは中身を解釈せず、
フックには `ConversationContext.client_state` として、LLM ツールには context の
`client_state` キーとしてそのまま渡します。

## 拡張どうしの依存

依存する拡張は、クラス属性 `requires` に名前を並べて宣言します。コアはロード時に、
それらが **ロード済みで、かつ `LILLA_EXTENSIONS` で自分より前に並んでいる** ことを
検証します。メッセージフック・`setup()`・ツールルートはロード順に処理されるため、
コアが並べ替えることはありません。

```python
class GoogleCalendarExtension(Extension):
    name = "lilla-google-calendar"
    requires = ("lilla-google-oauth",)  # extensions.lilla_google_oauth を読む依存もこれで表す

    def required_env_fields(self):
        return ["google_client_secret"]

    def required_tool_context_keys(self):
        return ["google_client"]
```

`requires` は「相手のパックが正しい順でロードされているか」を、`required_*` の各メソッドは
「自分が読む具体的なものを誰かが提供しているか」を検証します。汎用の `validate()` フックは
持たず、実行時の検査は `setup()` で行ってください。

## 信頼境界

`LILLA_EXTENSIONS` に並べたモジュールは同一プロセスで動く **信頼コード** です。
サンドボックスではありません。ツールを読み込むディレクトリも同じ扱いです
（[ツール契約](tools.md#ツールを読み込むディレクトリの信頼)）。

## 拡張契約の互換性

`lilla_core.core.extension.EXTENSION_API_VERSION` はこのコアが提供する `Extension`
契約のバージョン、`SUPPORTED_EXTENSION_API_VERSIONS` はロード時に受け付けるバージョンの
集合です。拡張は自分が書かれたバージョンを固定できます。

```python
from lilla_core.core.extension import EXTENSION_API_VERSION, Extension


class MyExtension(Extension):
    name = "my-extension"
    api_version = 2  # 省略すると EXTENSION_API_VERSION
```

宣言したバージョンを受け付けない場合、`load_extensions()` は拡張名と両方のバージョンを
示して失敗します。古い契約で書かれた拡張をそのまま読み込んで、起動後に壊れるのを
防ぐためです。現在のバージョンは 2 で、`config_models()` と `required_config_sections()` を
`config_model()` に置き換えました。廃止したメソッドのどちらかを定義したままの拡張は、
`api_version` を宣言していなくても、代わりの経路を示してロード時に失敗します。

契約を変えるときの方針:

- **非破壊（バージョンは上げない）**: `Extension` へのメソッド追加（必ず「何も貢献しない」
  既定を持たせる）、`*Context` データクラス（`SetupContext` / `ConversationContext`）への
  フィールド追加、参照関数やコア確定の context キーの追加
- **破壊的（`EXTENSION_API_VERSION` を上げる）**: メソッドのシグネチャや戻り値の形の変更、
  メソッド・`*Context` のフィールド・context キー・参照関数の削除や改名、フックが呼ばれる
  タイミングの変更。こうした変更は `CHANGELOG.md` に **BREAKING** として記録します
- 拡張パッケージは `lilla-core` をバージョン範囲で依存指定してください
  （例: `lilla-core>=0.3,<0.4`）。破壊的なコアのリリースを気付かず取り込まないためです

## 関連ページ

- [設定の合成](extension-config.md)
- [ダッシュボードへの差し込み](extension-dashboard.md)
- [拡張のテストの書き方](extension-testing.md)
