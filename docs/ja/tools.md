# ツール契約

ツールは `${CONFIG_ROOT}/tools/` の YAML 設定ファイルをもとに、`${TOOL_ROOT}/**/*.py`
から動的にロードされます。各 YAML のファイル名（stem）がツール名になり、`type`
フィールドから対応する `.py` ファイルを探します（詳細は
`loaders/llm_tool_loader.py` / `loaders/task_tool_loader.py` の実装を参照）。

## ツールの探索ルート

`type` をファイル名で書いたときの探索順は次のとおりです。

1. `paths.tool_root`（ホスト）
2. 各拡張の `tool_roots()`（拡張のロード順）
3. `lilla_core/builtin_tools`（[組み込みツール](#組み込みツール)。常に最後）

同名の `.py` が複数のルートにあるときは、どちらが使われるかが暗黙になるため起動時に
失敗します。ただし組み込みツールは常に負ける側で、ホストまたは拡張に同名ファイルが
あればそちらが使われ、起動は止まりません（同名で差し替えられます）。1 つのルートの
中に同名ファイルが複数ある場合は先頭マッチを使います。

探索ルートに入っていることと、ツールが有効になることは別です。YAML の無いツールは
どのルートにあってもロードされません。

## 拡張が同梱するツール YAML

拡張は `tool_config_roots()` で既定の YAML を同梱できます。ローダーはまずそれらの
ディレクトリ（拡張のロード順）から、次に `${CONFIG_ROOT}/tools` から YAML を集め、
同じ stem のファイルが `${CONFIG_ROOT}/tools` にあれば同梱分を丸ごと置き換えます
（内容のマージはしません）。利用者の設定が常に勝つ形です。2 つの拡張が同じ stem を
同梱していると起動時に失敗します。同梱ツールを止めるには、`${CONFIG_ROOT}/tools` に
同じ stem で `enabled: false` と書いた YAML を置いてください。`enabled: false` は
同梱かどうかに関わらず、どのツール YAML でも無効化に使えます。

## LLM ツール

`${CONFIG_ROOT}/tools/llm_*.yaml`、実装は `${TOOL_ROOT}/**/<type>.py` です。

- `.py` ファイルはモジュールレベルの `SCHEMA`（OpenAI の tools 形式の function
  schema dict）を定義するか、YAML の設定から動的にスキーマを組み立てたい場合は
  `build_schema(config: dict) -> dict` 関数を定義します（`SCHEMA` より先に確認
  されます）。
- `async def execute(tool_input: dict, context: dict) -> dict` も定義する必要があり、
  標準の結果 dict（`success` / `tool_name` / `data` / `error` など。ヘルパーは
  `tool_support/tool_result.py` を参照）を返します。
- YAML の `type` が `self` の場合、ローダーは `TOOL_ROOT` 配下の検索を行わず、
  YAML と同じディレクトリ・同名の `.py` をそのままロードします。
- YAML の `type` にドットが含まれる場合（`type: lilla_google_calendar.tools.calendar_get`）は
  **import パス** として扱い、ツールルートを探索する代わりに `importlib` でそのモジュールを
  読み込みます。インストール済みパッケージ（PyPI で配布する拡張など）がツールを同梱する
  ための経路です。通常の import なので、そのドット区切り名で `sys.modules` に登録され
  （stem で解決したファイルは独立したモジュールとして実行され、登録されません）、
  モジュール内の相対 import が使え、ディレクトリの検査も行いません。import に失敗した
  場合はファイルが見つからないときと同じく警告を出し、そのツールだけ読み飛ばします。
- YAML では `description`（スキーマの description を上書き）、
  `supported_client_type`（既定 `"all"`）、`cache` ブロック、その他ツール固有の
  キーを設定できます。ツール固有のキーは、下記の実行時コンテキストキーと衝突
  してはいけません（起動時に検証され、衝突時は fail-fast します）。

## task ツール

`${CONFIG_ROOT}/tools/task_*.yaml`、実装は `${TOOL_ROOT}/**/<type>.py` です。

- `.py` ファイルはクラスを定義し、`cls(config, name)` の形でインスタンス化されます
  （`config` は `_yaml_path` を加えた YAML の dict、`name` は YAML の stem）。
- インスタンスは `description` / `category` / `schedule`（cron 式の文字列）属性と、
  `async def execute(context: dict) -> None` を持つことが期待されます。
- `schedule` は省略可能です。未設定のツールはスケジューラには登録されませんが、
  `!runtask` による手動実行は可能です。
- `type` はこちらでも import パスを受け付けます（`type: some_package.tasks.daily_summary`）。
  クラスはファイルから読む場合と同じ規則で、import したモジュールから探します。
  トリガー種別は `type` ではなく YAML のファイル名から判定するため、import パス指定の
  task ツールも `task` ツールのままで、`!runtask` から実行できます。

## `execute` に渡される `context`

内容は呼び出し元によって異なります。LLM ツールでは常に `client_type` と、入れ子呼び出し
用のヘルパー `call_tool(tool_name, tool_input)` に加え、そのツールの YAML 固有のキーと、
拡張の `tool_context_providers()` が返すキー、そして呼び出し元クライアントが
`run_conversation()` に渡した場合は `client_state` が入ります。task ツールにも同じく
拡張が提供するキーが入り、加えてスケジュール実行時は `discord_client` / `now` /
`llm_tools` が、`!runtask` による手動実行時はさらに `params` が渡されます。
どちらのコア確定キーも予約済みで、`tool_context_providers()` が同じ名前を返す拡張は
静かに上書きされる代わりにロード時に失敗します。

## 組み込みツール

`lilla_core` は組み込みツール（`lilla_core/builtin_tools`）を同梱しています。拡張の
ツールと同じように探索ルートの一員なので、`type` にはファイル名を書きます。いずれも
既定では有効化されておらず、`${CONFIG_ROOT}/tools/` に YAML を置いた場合だけ opt-in で
有効になります。

| `type` | 種別 | 内容 |
|--------|------|------|
| `llm_current_datetime` | LLM | 現在日時を返すだけのサンプル |
| `llm_conversation_get` | LLM | [会話履歴の部屋名検索](history-search.md) |
| `task_channel_summary` | task | [部屋のノート（深夜要約）](channel-notes.md) |
| `task_scheduled_llm` | task | [スケジュール実行で LLM に委譲する通知](scheduled-llm.md) |
| `llm_expert` | LLM | 専門家サブエージェントへの委譲（後述） |

### `llm_expert`（専門家エージェント）

独自のシステムプロンプトとツールセットを持つサブエージェントへ処理を委譲するツールです。
実装はコアが持ち、プロンプト・使えるツール・LLM は YAML（`${CONFIG_ROOT}/tools/llm_*.yaml`）
に書きます。1 つの実装から YAML を増やして複数の専門家を作れます。`type` にはファイル名
`llm_expert` を書きます。

```yaml
type: llm_expert
description: 健康データの専門家。体調や運動についての質問を委譲する
prompt: dir:${config_root}/prompt/experts/health
llm_provider: grok            # 省略可
available_tools: [llm_health_get]   # `$main` は tools.main_available_tools に展開される
```

`api: responses` と `grok_tools` を組み合わせると、Responses API の組み込みツールを使った
単発呼び出しになります（`api: responses` と `available_tools` の併用は未対応です）。
専門家の中で再認証が必要になった場合は、処理を中断してその要求をそのまま伝えます。

サンプルを有効化する例（`${CONFIG_ROOT}/tools/llm_current_datetime.yaml`）:

```yaml
type: llm_current_datetime
```

import パス形式（`type: lilla_core.builtin_tools.llm_current_datetime`）も引き続き
使えます。既存の YAML はそのまま動きます。

## ツールを読み込むディレクトリの信頼

ツールを読み込むディレクトリ（`paths.tool_root`・各拡張の `tool_roots()`・
`lilla_core/builtin_tools`・`${CONFIG_ROOT}/tools`）へ書き込める者は、Bot のプロセス内で
コードを実行できます。
そのためツールパスの許可リストは別途持ちません。ローダーは、解決後のツールファイルが
これらのディレクトリの配下にあることだけを確認します（`..` を含む `type` や、外を指す
シンボリックリンクは読み込みません）。
