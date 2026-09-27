# 判定ヘルパー（Jev）

`lilla_core.tool_support.jev` は、TypeSafe の System One モデル（Jev）を呼ぶための
任意のヘルパーです。Jev は文章を書かず、「状態」と「型のある質問」を受け取って
Choice / Score / Noul の答えを確率つきで返します。

## 位置づけ

- **opt-in**: コアのどこからも import しません。対話ループや起動は Jev に触れず、
  ヘルパーを import して `ask_jev()` を呼んだコードだけが外部へ出ます
- **依存しない**: TypeSafe 公式 SDK や新しいパッケージはコアの依存に入れていません。
  HTTP は `core/http_util.py` の `send_http_request`（プロキシ設定を含む）で POST を 1 本送るだけです
- **分岐は外**: 答えをプロバイダー名に変える・confidence の閾値を決める、といった方針は
  呼び出し側（ツールや LLM resolver のスクリプト）で書きます
- キャッシュ・プロセス全体のクライアント・YAML の設定節は持ちません

## 使い方

```python
from lilla_core.tool_support.jev import (
    DIFFICULTY_SCORE, NEEDS_TOOL_NOUL, JevError, ask_jev, choice_question,
)

try:
    result = await ask_jev(
        {"message": latest_text},
        {
            "difficulty": DIFFICULTY_SCORE,   # 0〜2 の Score
            "needs_tool": NEEDS_TOOL_NOUL,    # yes / no
            "tone": choice_question({"casual": None, "formal": None}),
        },
    )
except JevError:
    return None  # 既定の分岐へ逃げる

if result.answers["difficulty"].score >= 1.5:
    ...
```

- `state`: 判定の材料（文字列・dict・list など JSON にできる値）
- `questions`: 質問名 → 質問。`choice_question()` / `score_question()` / `noul_question()`
  で組み立てるか、同じ形の dict（`{"type": "noul", "instructions": ...}` など）を渡します
- `DIFFICULTY_SCORE` / `NEEDS_TOOL_NOUL` はよく使う質問の例です。そのまま使っても、
  自分の質問に替えても構いません（送る前に複製するので定数は書き換わりません）
- キーワード引数: `api_key`（省略時は環境変数 `TYPESAFE_API_KEY`）・`url`（既定
  `https://api.typesafe.ai/v1/systemone`）・`model`（既定 `jev-latest`）・`timeout`（既定 10 秒）

戻り値 `JevResult` の `answers` は API の `answers` と同じキー（送った質問名）で、値は
`ChoiceAnswer`（`choice` / `confidence` / `probabilities`）・`ScoreAnswer`（`score` /
`confidence` / `legend` / `probabilities`。段階のキーは整数）・`NoulAnswer`（`noul`）です。

## エラー

失敗はすべて `JevError` の派生で投げます。メッセージは英語固定です。

| 例外 | 条件 |
|------|------|
| `JevConfigError` | API キーが無い、`state` が `None`、質問の形が誤っている（送信前に検出） |
| `JevRequestError` | HTTP エラー応答（`status` 属性つき）・接続失敗・タイムアウト（`status` は `None`） |
| `JevResponseError` | 応答が JSON でない・形が違う・送った質問の答えが欠けている |

API キーは YAML に書かず、`.env` / OS 環境変数か引数で渡してください。
