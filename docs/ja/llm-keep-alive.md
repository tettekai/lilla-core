# Ollama のモデルを載せておく時間（`llm.providers.<キー>.keep_alive`）

Ollama は、呼び出しのあとモデルをメモリに載せておく時間をリクエストの `keep_alive` で
決めます（Ollama の既定は 5 分）。ローカル LLM を会話に使っていると、少し間が空いた
だけでモデルがアンロードされ、次の呼び出しで読み込み待ちが発生します。`type: ollama`
のエントリに `keep_alive` を書くと、そのプロバイダーへのすべての呼び出しで
リクエストに `keep_alive` を付けます。

```yaml
llm:
  providers:
    local:
      type: ollama
      url: http://localhost:11434
      model: example-model
      keep_alive: 30m
```

## 書き方

- 値は文字列（`30m`、`1h` など）か整数（秒）です。整数の `-1` は無期限、`0` は
  呼び出しのあとすぐにアンロードします。
- 書いた値をそのまま送ります。文字列の書式（`30m` など）は lilla-core では検証せず、
  不正な書式は Ollama 側のエラーになります。
- `true` / `false` や小数・リストなど、文字列・整数以外の値は起動時に `ValidationError`
  で失敗します。
- 書かなければ送りません（Ollama の既定のまま。従来どおり）。

## どこに付くか

リクエストボディのトップレベルに付けます。`extra_params` とは別の項目です。

- tools なしの呼び出し（`/api/chat`）: `extra_params` は `options` の中に入りますが、
  `keep_alive` は `options` には入れずトップレベルに付けます（`extra_params` に
  `keep_alive` を書いても `options` に入るため Ollama には効きません）。
- tools 付きの呼び出し（Ollama の OpenAI 互換エンドポイント `/v1/chat/completions`）:
  同じくトップレベルに付けます。

```json
{
  "model": "example-model",
  "messages": [{"role": "user", "content": "こんにちは"}],
  "stream": false,
  "options": {"temperature": 0.7},
  "keep_alive": "30m"
}
```

## Ollama 以外に書いた場合

`keep_alive` は `type: ollama` のエントリだけが持てます。`type: openai_compat` と
`type: resolver` のエントリに書くと、起動時に `ValidationError` で失敗します。
resolver 経由の回に効かせたい場合は、resolver が選ぶ Ollama のエントリのそれぞれに
書いてください（[LLM プロバイダーの回しごと選択](llm-resolver.md)）。
