# プロバイダーごとのシステムプロンプト追記（`llm.providers.<キー>.prompt`）

システムプロンプト（`prompt.system` など）は会話全体で共通です。モデルによって指示の
効き方が違うときは、`llm.providers` の具体プロバイダー（`type: openai_compat` /
`type: ollama`）のエントリに `prompt` を書くと、そのプロバイダーへ送る回だけ、
システムプロンプトの末尾に短い追記を重ねられます。共通の人格は `prompt.system` のまま
にし、モデルごとの補いだけをここへ置きます。

```yaml
llm:
  providers:
    mimo:
      type: openai_compat
      url: https://api.example.invalid/v1
      model: example-model
      api_key_env: EXAMPLE_API_KEY
      prompt: file:prompts/mimo.md
```

## 書き方

- 値は `prompt.system` と同じ source spec です。`file:`（1 ファイル）か `dir:`
  （`.md` / `.txt` をファイル名昇順）で始め、リストで複数並べることもできます。
  `${config_root}` を展開し、相対パスは `CONFIG_ROOT` 基準で解決します。
- 本文を YAML に直書きすることはできません。`file:` / `dir:` で始まらない値（本文や
  接頭辞の無いパス）と空の指定は、起動時に `ValidationError` で失敗します。
- 書かなければ追記しません（従来どおり）。
- 内容は既存のプロンプトと同じく、呼び出しのたびにディスクから読みます（再起動なしで
  書き換えが効きます）。ファイルが無ければ追記なしとして扱います。

## どこに足されるか

追記は置き換えではなく連結です。共通のシステムプロンプトのあとに空行区切りで足します。
`client_type` ごとの追記（`prompt.discord` や拡張の `client_prompt_providers()`）が
ある場合は、それらを含めたシステムプロンプトのあとにプロバイダーの追記が来ます。

```
（共通のシステムプロンプト）
（client_type ごとの追記）

（実際に送る具体プロバイダーの prompt）
```

## どのプロバイダーの追記が使われるか

見るのは `model` の文字列ではなく、`llm.providers` のキーです。その回に実際に送る
具体プロバイダーが決まったあと、そのエントリの `prompt` を読みます。キーの決め方は
従来どおりです。

- 明示の `llm_name`（task YAML など）・`!model` での固定は、そのキーの `prompt`
- `type: resolver` のキーは、スクリプトが返したキーの `prompt`
  （[LLM プロバイダーの回しごと選択](llm-resolver.md)）。`fallback` に落ちた回は
  `fallback` 先の `prompt`
- `run_conversation` を通らずに `chat_to_llm` / `chat_to_llm_with_tools` /
  `chat_to_llm_responses` を直接呼ぶ経路で resolver 名を渡した場合も、`fallback` 先の
  `prompt`

ツールループの途中で resolver の判定によって具体プロバイダーが変わる回は、各 LLM 呼び出しで
そのとき実際に使うプロバイダーの追記に切り替わります（前の呼び出しの追記は持ち越しません）。

直接呼び出しで `messages` に system メッセージを含めて渡した場合は、最初の system
メッセージ（本文が文字列のもの）の末尾へ同じく空行区切りで足します。Responses API
（`chat_to_llm_responses`）では `instructions` の末尾へ足します。

## resolver に書いた場合

`type: resolver` のエントリに `prompt` を書いても起動は失敗せず、**無視されます**
（値の型や形の検証もしません）。resolver 自身は LLM へ送信しないため、追記の持ち主にはしません。
resolver 経由の回に追記したい場合は、resolver が選ぶ具体プロバイダーのエントリの
それぞれに `prompt` を書いてください。
