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
"""
from __future__ import annotations

# 登録チャンネル（`discord.channels` の `name`）ごとに固定したいプロバイダー。
# 例: {"study": "deepseek-flash-high", "chat": "deepseek-flash-low"}
CHANNEL_PROVIDERS: dict[str, str] = {}

# 画像添付のある回しで使う、視覚対応のプロバイダー名。`None` なら特別扱いしない。
# 例: "gpt-vision"
VISION_PROVIDER: str | None = None


def resolve(ctx) -> str | None:
    """回しごとに使う具体プロバイダー名を返す（`None` なら `fallback` に任せる）。

    Args:
        ctx: `LlmResolveContext`。`client_type` / `discord_channel_id` /
            `channel_name` / `user_text` / `has_image` / `provider_names` /
            `resolver_name` / `fallback` を持つ。
    """
    # 画像添付があるときは視覚対応のキーへ寄せる
    if ctx.has_image and VISION_PROVIDER in ctx.provider_names:
        return VISION_PROVIDER

    # 登録チャンネル名でキーを固定する
    if ctx.channel_name is not None and ctx.channel_name in CHANNEL_PROVIDERS:
        return CHANNEL_PROVIDERS[ctx.channel_name]

    # 判定モデルに発話の性質を尋ねて選ぶこともできる（任意）。例:
    #
    #   async def resolve(ctx):
    #       from lilla_core.api.llm_client import chat_to_llm
    #       answer = await chat_to_llm(
    #           f"Reply with 'high' or 'low' only. How hard is this?\n{ctx.user_text}",
    #           system_prompt="You are a router.",
    #           llm_name="deepseek-flash-low",
    #       )
    #       return "deepseek-flash-high" if "high" in answer.lower() else "deepseek-flash-low"

    return None
