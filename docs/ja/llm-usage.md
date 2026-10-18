# LLM のトークン使用量の記録

コアは LLM を呼ぶたびに、応答に含まれるトークン使用量を MongoDB の `llm_usage`
コレクションへ 1 件ずつ記録します。観測用ダッシュボードの **Usage**（`#/usage`）で
日別・プロバイダー別・入口別に集計して見られ、Home にも直近 24 時間の合計が出ます。

定期タスクの `max_tool_call_iterations`、会話履歴の長さ、resolver によるプロバイダーの
使い分けなどを、実際の数字を見ながら調整するための機能です。

## 設定

`lilla.yaml` の `llm.usage_log` で決めます。節ごと省略すると「記録する・90 日保持」です。

```yaml
llm:
  usage_log:
    enabled: true        # false で記録を止める（既定 true）
    retention_days: 90   # 保持日数（1 以上。既定 90）
```

- `retention_days` は各記録の `expire_at` に書き込み、TTL インデックスが消します。
  日数を変えると、変更後に書いた記録から新しい日数が効きます（既存の記録は書いたときの
  日数のまま消えます）
- `enabled: false` にしても既存の記録は消えず、Usage 画面にはそれまでの分が出ます

### 概算コスト（任意）

具体プロバイダー（`ollama` / `openai_compat`）に 100 万トークンあたりの単価を書くと、
Usage 画面と Home に概算コストが出ます。通貨は決めていないので、書いた単価の単位で
表示されます。resolver 型のエントリには書けません（起動時に落ちます）。

```yaml
llm:
  providers:
    grok:
      type: openai_compat
      url: "https://api.x.ai/v1"
      model: "grok-4.3"
      api_key_env: "GROK_API_KEY"
      input_price_per_million_tokens: 1.25   # 入力 100 万トークンあたり（0 以上）
      output_price_per_million_tokens: 2.5   # 出力 100 万トークンあたり（0 以上）
```

- コストは **記録した時点の単価** で計算して記録に残します。単価を後から足しても、
  それより前の記録にはコストが付きません
- 片方の単価だけを書いた場合、書いていない側は 0 として計算します。書いた側の
  トークン数が応答に無かった記録はコスト不明になります
- **概算コストは設定した単価による目安で、実際の請求額とは一致しません**（キャッシュ割引・
  推論トークンの扱い・為替などはプロバイダーごとに異なります）

## 記録する内容

1 回の LLM 呼び出し（Chat Completions・tools 付き・Responses API・Ollama ネイティブの
どの経路でも）につき 1 件です。送信に失敗した呼び出しは記録しません。

| フィールド | 内容 |
|------------|------|
| `created_at` / `expire_at` | 記録した時刻と、TTL で消える時刻 |
| `provider` / `provider_type` / `model` | 実際に送った具体プロバイダーのキー（resolver 名で直接呼んだときは fallback 先）・種別・モデル名 |
| `api` | `chat` / `tools` / `responses` |
| `entry` | 入口のラベル（下記） |
| `client_type` / `discord_channel_id` / `task_name` / `tool_name` / `source` | 入口の内訳（分かったものだけ） |
| `duration_ms` | HTTP 送信から応答の解釈までの所要時間 |
| `input_tokens` / `output_tokens` / `total_tokens` | 入力・出力・合計トークン数。応答に無ければ `null`（推定はしない）。合計が無く入力・出力の両方があるときだけ和を合計にする |
| `usage_known` | 合計トークン数が分かったか |
| `raw_usage` | 応答の `usage`（Ollama ネイティブは `prompt_eval_count` などの数値項目）を数値だけに絞ったもの。推論トークン・キャッシュトークンの内訳もここに残る |
| `cost` | 概算コスト。単価が無い・計算できないときは `null` |

**メッセージ本文・プロンプト・ツールの引数や結果は記録しません。**

使用量を読む応答の項目は次のとおりです。

| 経路 | 入力 | 出力 | 合計 |
|------|------|------|------|
| Chat Completions（tools 付きを含む） | `usage.prompt_tokens` | `usage.completion_tokens` | `usage.total_tokens` |
| Responses API | `usage.input_tokens` | `usage.output_tokens` | `usage.total_tokens` |
| Ollama ネイティブ（`/api/chat`） | `prompt_eval_count` | `eval_count` | （入力 + 出力） |

## 入口（`entry`）

「どこからの呼び出しか」は公開関数の引数ではなく `contextvars`
（`lilla_core.core.llm_call_origin`）で受け渡します。ラベルは最も内側に近いものを使います。

| ラベル | 意味 |
|--------|------|
| `tool:<YAML stem>` | LLM ツールの中で起きた呼び出し（`llm_expert` など。入れ子なら最も内側のツール） |
| `task:<YAML stem>` | 定期タスク（APScheduler / `!runtask` / `POST /api/runtask`）の中の呼び出し |
| `selftest` / `toolresult` | `!selftest full` の LLM 疎通確認、`!toolresult` の返信生成 |
| `conversation:<client_type>` | 通常会話（`run_conversation`。`discord` や拡張が増やす種別） |
| `unknown` | 上のどれにも当てはまらない呼び出し |

拡張が自分の入口を区別したいときは、LLM を呼ぶ前に
`with llm_call_origin(source="my-ext"):` のように積めます（`None` を渡した項目は外側の
値を残します）。

## 書き込みと失敗時の扱い

記録の書き込みは本処理を待たせないよう非同期のタスクとして投げます。書き込みに失敗しても
WARNING ログ（`Failed to record LLM usage: ...`）を出すだけで、LLM 呼び出しは成功扱いの
ままです。

## ダッシュボード

- **Usage**（`#/usage`、`GET /api/usage?days=N`）: 今日を含む直近 N 日（既定 7、1〜366）を
  `ui.timezone` の暦日で数え、合計・日別・プロバイダー別（モデル名つき）・入口別の呼び出し数・
  入力 / 出力 / 合計トークン数・平均所要時間・概算コストを出します。トークン数が不明な
  呼び出しは「うち不明」として件数だけ数えます
- **Home**: 直近 24 時間の呼び出し数・合計トークン数（単価があれば概算コスト）を一行で出します

どちらも集計値だけを返し、記録 1 件ごとの行は返しません。
