# LLM 呼び出しの自動再試行（`llm.providers.<キー>.max_retries` など）

上流の API が一時的に高負荷（`503`）やレート制限（`429`）を返したり、接続が一瞬切れたり
しただけで、その回の会話・定期タスク・`llm_expert` が失敗しないよう、lilla-core は
**同じ具体プロバイダーへ同じリクエストを送り直します**。Chat Completions・Responses API・
Ollama ネイティブのすべての経路に効きます。

**既定で有効です**（2 回まで再試行）。従来どおり再試行しない挙動に戻すには、
プロバイダーごとに `max_retries: 0` を書いてください。

```yaml
llm:
  providers:
    grok:
      type: openai_compat
      url: https://api.x.ai/v1
      model: example-model
      api_key_env: GROK_API_KEY
      max_retries: 2              # 既定 2。0 で再試行しない
      retry_backoff_seconds: 1    # 既定 1 秒
      retry_max_wait_seconds: 30  # 既定 30 秒
      retry_on_timeout: false     # 既定 false
```

別のプロバイダーへ切り替える判断（死活判定やフォールバック）はここでは行いません。それは
resolver スクリプトの担当です（[LLM プロバイダーの回しごと選択](llm-resolver.md)）。

## 項目

| 項目 | 既定 | 内容 |
|------|------|------|
| `max_retries` | `2` | 一時的な失敗のとき送り直す回数（0 以上の整数）。最初の 1 回と合わせて最大 `max_retries + 1` 回送ります |
| `retry_backoff_seconds` | `1` | 再試行の前に待つ基準の秒数（正の数）。n 回目の再試行の前は「基準 × 2^(n-1)」に最大 5 割のジッターを足して待ちます（既定なら 1 回目は 1〜1.5 秒、2 回目は 2〜3 秒） |
| `retry_max_wait_seconds` | `30` | 1 回の待ち時間の上限（正の数）。計算した待ち時間はここで切ります。応答の `Retry-After` がこれを超える回は、再試行せずに失敗させます |
| `retry_on_timeout` | `false` | `timeout_seconds` を超えた回も再試行するか（`true` / `false`） |

値の範囲外（`max_retries` が負・秒数が 0 以下）や型違い（`max_retries` に小数や `true`、
`retry_on_timeout` に文字列など）は起動時に `ValidationError` で失敗します。
どの項目も具体プロバイダー（`type: ollama` / `type: openai_compat`）だけが持てます。
`type: resolver` のエントリに書くと起動時に失敗します。resolver 経由の回は、resolver が
選んだ具体プロバイダーの設定で再試行します。

## 再試行する失敗・しない失敗

再試行するのは、一時的と判断できる失敗だけです。

- HTTP ステータス `429` / `500` / `502` / `503` / `504`
- 接続エラー（接続できなかった、応答の途中で切れたなど）
- タイムアウト（`timeout_seconds` の超過）は `retry_on_timeout: true` のときだけ

次の失敗は再試行せず、すぐに失敗させます。

- 上記以外の `4xx`（`400` / `401` / `403` / `404` など）
- 応答 JSON の解釈の失敗
- [LLM 送信前の拒否リスト](llm-send-guard.md)による拒否（送信前検査は最初の 1 回だけ行い、再試行のたびには繰り返しません）

タイムアウトを既定で再試行しないのは、上流では最初のリクエストも処理されていて、料金が
二重にかかる場合があるためです。また、1 回の試行ごとに `timeout_seconds` まで待つため、
待ち時間が大きく膨らみます。

## 待ち時間と `Retry-After`

応答に `Retry-After` ヘッダーがあれば、計算した待ち時間の代わりにその秒数を待ちます
（秒数と HTTP 日付のどちらの形式でも読みます。読めない値なら計算した待ち時間を使います）。
指定が `retry_max_wait_seconds` を超えるときは、待たずにその回で失敗させます。

再試行で失敗時の応答が遅くなるのは、最大でおよそ「`retry_max_wait_seconds` × `max_retries`」に
各試行の所要時間を足した分です。

## ログと使用量の記録

- 再試行するたびに WARNING ログ（プロバイダー名・ステータスまたは例外の種類・次の試行が
  何回目か・待ち時間）を出します。
- 再試行の残りがある試行の HTTP エラーは、本文を含めて WARNING で記録します。最後の試行の
  HTTP エラーは従来どおり ERROR です。再試行のあとで諦めたときや、再試行の残りがある試行で
  再試行しない HTTP エラー（`401` など）が起きたときは、プロバイダー名と試行回数を含む ERROR ログを出します。
- 最終的に失敗した場合は、従来どおり `LLMError` を送出します。
- [トークン使用量の記録](llm-usage.md)は成功した 1 回分だけで、所要時間も成功した試行の分です。
  失敗した試行は記録しません。
