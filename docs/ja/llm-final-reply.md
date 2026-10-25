# ツールループの最終応答の書き直し（`llm.providers.<キー>.final_reply_provider`）

ツールループの途中から安いモデル（ローカルの Ollama など）に処理を引き継ぐと、
最終応答の口調や品質がメインのモデルと揃わないことがあります。具体プロバイダー
（`type: openai_compat` / `type: ollama`）のエントリに `final_reply_provider` を書くと、
そのプロバイダーがツールループの最終応答を返したとき、その本文を捨てて、指定した
プロバイダーに書き直させます。

```yaml
llm:
  providers:
    deepseek:
      type: openai_compat
      url: https://api.example.invalid/v1
      model: example-main
      api_key_env: EXAMPLE_API_KEY
    local:
      type: ollama
      url: http://localhost:11434
      model: example-local
      final_reply_provider: deepseek
```

どの回しで `local` に引き継ぐか（ツールを呼んだあとだけ・ローカル LLM が生きているとき
だけ・時間帯など）は、[resolver スクリプト](llm-resolver.md) の側で決めます。
`final_reply_provider` が受け持つのは書き直しだけです。

## 動作

- `run_conversation` のツールループで、tool_calls の無い応答（最終応答）を返した具体
  プロバイダーに `final_reply_provider` があると、その最終応答の本文は捨てます。
- 以降の応答は指定先のプロバイダーが担当します。元のツールループと同じツールと
  システムプロンプトを渡して問い合わせます（指定先の `prompt` があれば、いつもどおり
  その追記も付きます）。
- 入力は、捨てた最終応答を含まないメッセージ列（会話履歴・ツール呼び出し・ツール結果）
  そのままです。書き直しのための指示は足しません。
- 指定先が tool_calls の無い応答を返したら、それを最終応答にします。ツールを呼ばなければ
  問い合わせは 1 回で終わります。
- 指定先がツールを呼んだら、元のツールループと同じ手順でツールを実行し（引数 JSON が
  壊れているときのエラー結果・再認証の案内・ツール呼び出しの通知も同じ）、ツール結果を
  加えて指定先へ問い合わせを続けます。この間は [resolver スクリプト](llm-resolver.md)
  による再判定をせず、元のプロバイダーには戻りません。
- ツール呼び出しの往復回数は、元のツールループと通算で `llm.max_tool_call_iterations`
  （呼び出し時に上限を渡した場合はその値）までです。上限に達したら、通常のツール
  ループと同じく [上限に達したときのまとめ](tool-call-limit.md) を返します（まとめは
  書き直し先が書き、書き直しを重ねません）。
- 書き直し先の `final_reply_provider` は見ません。書き直しは 1 段で閉じ、連鎖しません。
- META ブロック（`set_session_memory` など）の処理は書き直したあとの本文に対して行います。
- 書き直しの呼び出しが失敗したときは、通常の LLM 呼び出しの失敗と同じ扱いです
  （捨てた本文には戻しません）。

次の経路では書き直しません。

- ツールを渡さない会話（ツールが無い・`!disable_tools` 中・許可リストが空など）
- `llm_expert` の内側のループ、`chat_to_llm` を直接呼ぶタスクやコマンド
- ツール呼び出しの往復上限に達して中断した回（`llm.on_tool_call_limit: summarize` の
  まとめは、最後のプロバイダーに `final_reply_provider` があれば指定先が書きます）

## 書き方

- 値は `llm.providers` にある具体プロバイダーの名前です。
- 次は起動時に失敗します（fail-fast）。
  - 台帳に無い名前を指す
  - resolver 型を指す
  - resolver 型のエントリに `final_reply_provider` を書く
- 書かなければ書き直しません（従来どおり）。

書き直しの分、LLM の呼び出しが 1 回（指定先がツールを呼べば、その往復の分さらに）増えます。
使用量の記録（[LLM のトークン使用量の記録](llm-usage.md)）にも、書き直し先のプロバイダーで
呼び出しごとに 1 件ずつ残ります。
