# LLM プロバイダーの回しごと選択（`type: resolver`）

`llm.providers` のエントリに `type: resolver` を書くと、そのキーが選ばれた回しごとに、
ホストが置いた Python スクリプトが具体プロバイダー（`openai_compat` / `ollama`）の名前を
決めます。組み合わせそのもの（同じ `model` で `extra_params.reasoning_effort` だけ違う、
など）は具体プロバイダーのエントリで表し、resolver はそのキーを選ぶだけです。

```yaml
llm:
  default: router
  providers:
    router:
      type: resolver
      script: ${config_root}/llm_resolver.py
      fallback: deepseek-flash-high
      # timeout_seconds: 10   # async def resolve の待ち時間の上限（既定 10 秒）
    deepseek-flash-low:
      type: openai_compat
      url: "https://api.deepseek.com"
      model: "deepseek-flash"
      api_key_env: "DEEPSEEK_API_KEY"
      extra_params:
        reasoning_effort: low
    deepseek-flash-high:
      type: openai_compat
      url: "https://api.deepseek.com"
      model: "deepseek-flash"
      api_key_env: "DEEPSEEK_API_KEY"
      extra_params:
        reasoning_effort: high
```

## 解決順

使うキーの決め方は従来どおりです。

1. 呼び出し側が渡した `llm_name`（task YAML など）
2. `!model` による上書き
3. `llm.default`

そのキーが `type: resolver` ならスクリプトを呼び、返った名前で台帳を引き直します。
具体プロバイダーならそのまま LLM を呼びます。`!model router` は回しごとの自動選択、
`!model deepseek-flash-high` は固定です。task が `llm_name: router` と書けば回し、
具体名なら回しません。上書きを解除して `llm.default` に戻すのは `!model reset` で、
引数なしの `!model` は状態を変えずに定義済みのキーの一覧と今有効なキーを返すだけです。
`reset` は予約語のため `llm.providers` のキーには使えません（使うと起動時に失敗します）。

展開は `run_conversation` の中で、LLM を呼ぶたびにその直前で行います（履歴を
組み立てたあとの最初の呼び出しも、ツールを呼んだあとの続きの呼び出しも同じ）。
Discord・拡張のクライアント・task のどれから呼んでも同じです。キーが具体
プロバイダー（明示の `llm_name` や `!model` での固定）の回は、ツールループ中も
スクリプトを呼びません。
`run_conversation` を通らずに `chat_to_llm` を直接呼ぶ経路（`!selftest` や、
`chat_to_llm` を直に呼ぶタスクなど）に resolver 名が渡った場合は、文脈が無いので
スクリプトは呼ばずに `fallback` を使います。会話開始フックの
`ConversationContext.llm_name` には展開前のキーが載ります。

## 項目

| 項目 | 必須 | 内容 |
|------|------|------|
| `script` | 必須 | `resolve` を定義した Python ファイル。`${config_root}` を展開し、`file:` 接頭は任意、相対パスは `CONFIG_ROOT` 基準。`dir:` は不可 |
| `fallback` | 必須 | `llm.providers` にある具体プロバイダー名（resolver 型は不可） |
| `timeout_seconds` | 任意 | `async def resolve` の待ち時間の上限（秒。既定 10） |

resolver 型は HTTP を出さないため `url` / `model` / `api_key_env` / `wakeup_file` /
`extra_params` は書けません。逆に具体プロバイダーは `url` / `model` が必須で、`script` /
`fallback` は書けません。Ollama の起動待ち（WOL）も具体プロバイダー側だけに掛かります。

次は起動時に失敗します（fail-fast）。

- `script` の解決後のパスが `CONFIG_ROOT` の外（`..` や外を指すシンボリックリンクを含む）
- `script` のファイルが無い、import に失敗する、`resolve` が無い
- `fallback` が台帳に無い、または resolver 型
- 上記の項目の型ごとの不整合

`CONFIG_ROOT` は拡張モジュールと同じ信頼レベルです（[`SECURITY.md`](../../SECURITY.md)）。
スクリプトはボットと同じプロセスで動く信頼コードで、サンドボックスではありません。

## 関数の契約

```python
def resolve(ctx) -> str | None: ...
# または
async def resolve(ctx) -> str | None: ...
```

- 関数名は `resolve` で固定です。`def` はイベントループ上で直接呼ばれるので、重い処理
  （判定モデルの呼び出しなど）は `async def` で書いてください
- 具体プロバイダー名を返すとそれが使われます。`None` を返すと `fallback` です
- 未知の名前・別の resolver 名（深さは 1 まで）・文字列以外・例外・タイムアウトは、
  警告ログを出して `fallback` に落ちます。会話は止まりません

`ctx` は `lilla_core.services.llm_resolver.LlmResolveContext`（frozen dataclass）です。
フィールドの追加は非破壊です。

| フィールド | 内容 |
|------------|------|
| `client_type` | `"discord"` / `"task"` / 拡張が増やす種別 |
| `resolver_name` | 今回解決している resolver のキー名 |
| `fallback` | そのエントリの `fallback` |
| `provider_names` | `llm.providers` の名前一覧（resolver を含む） |
| `discord_channel_id` | Discord の会話ならチャンネル ID（無ければ `None`） |
| `channel_name` | `discord.channels` に登録されたチャンネルならその `name` |
| `user_text` | 直近のユーザー発話のテキスト（タイムスタンプを除き、500 文字で切り詰め） |
| `has_image` | 今回のユーザー発言に画像添付があり、LLM へ画像パートを渡すときだけ `True` |
| `called_tool_names` | その回しで既に呼んだツール名のタプル（出現順・重複あり）。最初の呼び出しでは空 |
| `previous_provider` | その回しで前回選んだ具体プロバイダー名。最初の呼び出しでは `None` |

システムプロンプト・ツールの引数や結果の全文、画像本体（data URL・バイト列）は渡しません。
過去ターンの履歴に画像があっただけでは `has_image` は `True` になりません。画像ありの
回しでどのキーを選ぶかはスクリプト側の方針で、プロバイダーごとの vision 対応表は
コアにはありません。

`user_text` はユーザーの発言そのものです。判定モデルに渡す場合は、指示ではなく
データとして扱うプロンプトにしてください（プロンプトインジェクション対策）。

## ツールループ中の切り替え

ツールを呼んだあとの LLM 呼び出しでも `resolve` がもう一度呼ばれます。前回と同じ
プロバイダーを続けるときは `ctx.previous_provider` をそのまま返し、切り替えるときだけ
別のキーを返してください（`None` は前回の続きではなく `fallback` です）。前回の選択は
コアが覚えるので、スクリプトに状態を持たせる必要はありません。

```python
def resolve(ctx):
    # 書き込み系のツールを呼んだあとだけ思考モードのプロバイダーへ切り替える
    if "calendar_create" in ctx.called_tool_names:
        return "deepseek-flash-high"
    return ctx.previous_provider or "deepseek-flash-low"
```

判定モデルの呼び出しのような重い処理は、`ctx.previous_provider` があれば省いて
前回の名前を返すと、ツールのたびに判定が走るのを避けられます。途中でプロバイダーを
変えたときにプロバイダー側が受け付けるか（思考モードの切り替えでエラーになるなど）は
コアでは扱いません。スクリプトとプロバイダー設定の側で合わせてください。

## テンプレ

コピーして使える見本を `lilla_core/templates/llm_resolver.py` に同梱しています。

```bash
cp "$(python -c 'import lilla_core, pathlib; print(pathlib.Path(lilla_core.__file__).parent / "templates" / "llm_resolver.py")')" \
   "$CONFIG_ROOT/llm_resolver.py"
```

既定では `None` を返して `fallback` に任せます。`CHANNEL_PROVIDERS`（登録チャンネル名 →
キー）と `VISION_PROVIDER`（画像ありのときのキー）を埋めると、それぞれの分岐が効きます。
判定モデルを呼ぶ例はコメントで載せています。
