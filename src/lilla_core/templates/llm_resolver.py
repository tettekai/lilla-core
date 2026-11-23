"""`llm.providers` の `type: resolver` 用スクリプトの見本。

このファイルを `${CONFIG_ROOT}` 配下（例: `${CONFIG_ROOT}/llm_resolver.py`）へ
コピーし、resolver エントリの `script` に指して使う。

```yaml
llm:
  default: router
  providers:
    router:
      type: resolver
      script: ${config_root}/llm_resolver.py
      fallback: deepseek-flash-high
    deepseek-flash-high:
      type: openai_compat
      url: "https://api.deepseek.com"
      model: "deepseek-flash"
      api_key_env: "DEEPSEEK_API_KEY"
```

契約:

- 関数名は `resolve` で固定。引数は `LlmResolveContext` 1 つ
  （`lilla_core.services.llm_resolver`）。`def` でも `async def` でもよい
  （`def` はイベントループ上で直接呼ばれるので、重い処理は `async def` で書く）
- `llm.providers` にある具体プロバイダー（`openai_compat` / `ollama`）の名前を返す。
  `None` を返すとそのエントリの `fallback` が使われる
- 未知の名前・別の resolver 名・例外・タイムアウト（`timeout_seconds`。既定 10 秒）も
  警告ログを出して `fallback` に落ちる。会話は止まらない
- ツールループ中は LLM を呼ぶたびに呼ばれる。`ctx.called_tool_names` にその回しで
  呼んだツール名、`ctx.previous_provider` に前回選んだ名前が載る（最初は空 / `None`）。
  同じを続けるなら `ctx.previous_provider` を返す（`None` は `fallback` になる）
- 具体プロバイダーへの送信が失敗すると（HTTP 404 / 408 / 429 / 5xx・接続エラー・
  タイムアウト。同じプロバイダーへの再試行を使い切ったあと）、もう一度呼ばれる。
  そのとき `ctx.failure` に失敗の情報（`provider` / `error_kind` / `status` /
  `retry_after`）、`ctx.reselection` に何回目の選び直しか（1 から）が載る。
  別の具体プロバイダー名を返すと同じリクエストをそちらへ送り直し、`None`・失敗した
  プロバイダーと同じ名前を返すと元のエラーで止まる（このときは `fallback` に落ちない）。
  回数の上限は resolver エントリの `max_reselections`（既定 2）
- `ctx.unavailable_providers` には、しばらく前に失敗したプロバイダーが載る
  （`provider` / `until` / `error_kind` / `status`）。コアは記憶するだけで送信は
  止めないため、避けるかどうかはここで決める
"""
from __future__ import annotations

# 登録チャンネル（`discord.channels` の `name`）ごとに固定したいプロバイダー。
# 例: {"study": "deepseek-flash-high", "chat": "deepseek-flash-low"}
CHANNEL_PROVIDERS: dict[str, str] = {}

# 画像添付のある回しで使う、視覚対応のプロバイダー名。`None` なら特別扱いしない。
# 例: "gpt-vision"
VISION_PROVIDER: str | None = None

# 失敗したとき・選んだプロバイダーがしばらく使えないときに、上から順に試す代わりの
# プロバイダー名。空なら特別扱いしない（失敗したらそのままエラーになる）。
# 例: ["deepseek-flash-low", "local"]
FAILOVER_PROVIDERS: list[str] = []


def resolve(ctx) -> str | None:
    """回しごとに使う具体プロバイダー名を返す（`None` なら `fallback` に任せる）。

    Args:
        ctx: `LlmResolveContext`。`client_type` / `discord_channel_id` /
            `channel_name` / `user_text` / `has_image` / `provider_names` /
            `resolver_name` / `fallback` / `called_tool_names` /
            `previous_provider` / `failure` / `reselection` /
            `unavailable_providers` を持つ。
    """
    unavailable = {entry.provider for entry in ctx.unavailable_providers}

    # 送信が失敗した直後は、失敗したもの・しばらく使えないもの以外の代わりへ回す
    if ctx.failure is not None:
        return _first_available(unavailable | {ctx.failure.provider})

    choice = _choose(ctx)
    # 選んだプロバイダーがしばらく使えないなら代わりへ回す（代わりが無ければそのまま）
    if FAILOVER_PROVIDERS and (choice or ctx.fallback) in unavailable:
        return _first_available(unavailable) or choice
    return choice


def _first_available(excluded: set[str]) -> str | None:
    """`FAILOVER_PROVIDERS` のうち `excluded` に無い最初の名前を返す（無ければ `None`）。"""
    return next((name for name in FAILOVER_PROVIDERS if name not in excluded), None)


def _choose(ctx) -> str | None:
    """失敗を考えずに使いたいプロバイダー名を返す（`None` なら `fallback`）。"""
    # 画像添付があるときは視覚対応のキーへ寄せる
    if ctx.has_image and VISION_PROVIDER in ctx.provider_names:
        return VISION_PROVIDER

    # 登録チャンネル名でキーを固定する
    if ctx.channel_name is not None and ctx.channel_name in CHANNEL_PROVIDERS:
        return CHANNEL_PROVIDERS[ctx.channel_name]

    # 判定モデルに発話の性質を尋ねて選ぶこともできる（任意）。例:
    #
    #   async def resolve(ctx):
    #       if ctx.previous_provider is not None:
    #           return ctx.previous_provider  # ツールループの続きでは判定し直さない
    #       from lilla_core.api.llm_client import chat_to_llm
    #       answer = await chat_to_llm(
    #           f"Reply with 'high' or 'low' only. How hard is this?\n{ctx.user_text}",
    #           system_prompt="You are a router.",
    #           llm_name="deepseek-flash-low",
    #       )
    #       return "deepseek-flash-high" if "high" in answer.lower() else "deepseek-flash-low"

    return None
