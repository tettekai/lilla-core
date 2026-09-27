"""`llm.providers` の resolver 型エントリを、回しごとに具体プロバイダー名へ展開する。

使うキーの決め方（呼び出し側の `llm_name` → `!model` の上書き → `llm.default`）は
変えず、決まったキーが `type: resolver` のときだけ、ホストが `CONFIG_ROOT` 配下に
置いたスクリプトの `resolve(ctx)` を呼んで具体プロバイダー名を得る。展開は深さ 1 で、
`None`・未知の名前・別の resolver 名・例外・タイムアウトはいずれも警告ログを出して
そのエントリの `fallback` に落とし、会話は止めない。
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lilla_core.core.config import AppConfig, get_config, resolve_llm_resolver_script_path
from lilla_core.loaders.script_loader import load_script_function

logger = logging.getLogger(__name__)

#: resolver スクリプトから取り出す関数名（固定）。
RESOLVE_FUNCTION_NAME = "resolve"

#: `LlmResolveContext.user_text` に載せる直近ユーザー発話の最大文字数。
USER_TEXT_MAX_CHARS = 500

# 読み込んだ `resolve` 関数のキャッシュ（キーは解決後のスクリプトパス）。
_resolve_functions: dict[Path, Callable[..., Any]] = {}


@dataclass(frozen=True)
class LlmResolveContext:
    """resolver スクリプトの `resolve(ctx)` に渡す文脈。

    位置引数を並べず 1 つのオブジェクトで渡す。フィールドの追加は非破壊、既存
    フィールドの削除・改名は破壊的変更として扱う。システムプロンプトやツール結果の
    全文、画像本体（data URL・バイト列）は載せない。
    """

    #: 会話の送信元クライアント種別（`"discord"` / `"task"` / 拡張が増やす種別）。
    client_type: str
    #: 今回解決している resolver のキー名。
    resolver_name: str
    #: そのエントリの `fallback`（`None` を返したときに使われる具体プロバイダー名）。
    fallback: str
    #: `llm.providers` の名前一覧（resolver 型を含む）。
    provider_names: tuple[str, ...] = ()
    #: Discord からの会話ならそのチャンネル ID。
    discord_channel_id: int | None = None
    #: `discord.channels` に登録されたチャンネルならその `name`。
    channel_name: str | None = None
    #: 直近のユーザー発話のテキスト（`USER_TEXT_MAX_CHARS` 文字で切り詰める）。
    user_text: str = ""
    #: 今回のユーザー発言に画像添付があり、LLM へ画像パートを渡すときだけ `True`。
    has_image: bool = False


def _script_path(provider, config: AppConfig) -> Path:
    """resolver エントリの `script` を実ファイルパスへ解決する。"""
    return resolve_llm_resolver_script_path(provider.script, config.env.config_root)


def _load_resolve_function(provider, config: AppConfig) -> Callable[..., Any] | None:
    """resolver スクリプトから `resolve` 関数を読み込んで返す（キャッシュつき）。

    ロードしてよい範囲は `CONFIG_ROOT` 配下だけ（`load_script_function` の
    `tool_dirs` に `config_root` を渡して検査する）。読めなければ `None`。
    """
    path = _script_path(provider, config)
    cached = _resolve_functions.get(path)
    if cached is not None:
        return cached
    func = load_script_function(path, RESOLVE_FUNCTION_NAME, tool_dirs=[Path(config.env.config_root)])
    if func is not None and callable(func):
        _resolve_functions[path] = func
        return func
    return None


def clear_cache() -> None:
    """読み込み済みの `resolve` 関数のキャッシュを捨てる（テスト用）。"""
    _resolve_functions.clear()


def validate_llm_resolvers(config: AppConfig | None = None) -> None:
    """全 resolver エントリのスクリプトを読み込み、`resolve` 関数があることを確かめる。

    起動時に 1 度呼び、読み込めない・`resolve` が無いものは fail-fast させる。
    パスが `CONFIG_ROOT` 配下の実在ファイルであることと `fallback` の検査は、
    設定の読み込み時（`AppConfig` / `LlmConfig` のバリデータ）で済んでいる。

    Raises:
        ValueError: スクリプトを読み込めない、または `resolve` 関数が無い場合。
    """
    config = config or get_config()
    for name, provider in config.llm.providers.items():
        if provider.type != "resolver":
            continue
        try:
            func = _load_resolve_function(provider, config)
        except Exception as e:
            raise ValueError(f"Failed to load llm resolver script for '{name}': {e}") from e
        if func is None:
            raise ValueError(
                f"llm resolver script for '{name}' does not define a callable "
                f"'{RESOLVE_FUNCTION_NAME}': {provider.script}"
            )


def _extract_text(content: Any) -> str:
    """user メッセージの content（str / content_parts）からテキスト部分を取り出す。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = [
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        ]
        return "\n".join(t for t in texts if t)
    return ""


def summarize_user_text(content: Any, max_chars: int = USER_TEXT_MAX_CHARS) -> str:
    """直近ユーザー発話のテキストを、resolver へ渡せる長さに切り詰めて返す。

    タイムスタンプ prefix（`[Apr 28 11:22] `）は取り除く。
    """
    from lilla_core.services.message_util import strip_timestamp_prefix

    text = strip_timestamp_prefix(_extract_text(content)).strip()
    if len(text) > max_chars:
        text = text[:max_chars] + "…"
    return text


def content_has_image(content: Any) -> bool:
    """content_parts に画像パート（`image_url`）が含まれるかを返す。"""
    if not isinstance(content, list):
        return False
    return any(isinstance(part, dict) and part.get("type") == "image_url" for part in content)


async def resolve_llm_name(
    llm_name: str | None,
    *,
    client_type: str,
    discord_channel_id: int | None = None,
    user_content: Any = None,
    has_image: bool = False,
    config: AppConfig | None = None,
) -> str:
    """使うキーを決め、それが resolver 型なら具体プロバイダー名へ展開して返す。

    `llm_name` が `None` なら `llm.default` を使う。具体プロバイダーならそのまま返し、
    スクリプトは呼ばない。resolver 型ならスクリプトの `resolve(ctx)` を呼び、
    返った名前が台帳にある具体プロバイダーならそれを返す。`None`・未知の名前・
    別の resolver 名・例外・タイムアウトは警告ログを出して `fallback` を返す
    （`None` は正規の「任せる」なので DEBUG ログのみ）。

    Args:
        llm_name: 呼び出し側が決めたキー（`run_conversation` の `llm_name`）。
        client_type: 会話の送信元クライアント種別。
        discord_channel_id: Discord からの会話ならそのチャンネル ID。
        user_content: 直近のユーザー発話の content（str / content_parts）。テキスト部分だけを
            切り詰めて渡す。
        has_image: 今回のユーザー発言に画像パートがあるか（過去の履歴は含めない）。
        config: 使う設定。省略時は `get_config()`。

    Returns:
        LLM 呼び出しに使う具体プロバイダー名。
    """
    config = config or get_config()
    name = llm_name or config.llm.default
    provider = config.llm.providers.get(name)
    if provider is None or provider.type != "resolver":
        # 未知の名前はここで握りつぶさず、従来どおり LLM 呼び出し側の検査に任せる
        return name

    fallback = provider.fallback
    channel_name = None
    if discord_channel_id is not None:
        entry = config.discord.find_channel_by_id(discord_channel_id)
        channel_name = entry.name if entry is not None else None
    ctx = LlmResolveContext(
        client_type=client_type,
        resolver_name=name,
        fallback=fallback,
        provider_names=tuple(config.llm.providers),
        discord_channel_id=discord_channel_id,
        channel_name=channel_name,
        user_text=summarize_user_text(user_content),
        has_image=has_image,
    )

    try:
        func = _load_resolve_function(provider, config)
        if func is None:
            logger.warning("LLM resolver '%s' has no resolve function; using fallback '%s'", name, fallback)
            return fallback
        result = func(ctx)
        if inspect.isawaitable(result):
            result = await asyncio.wait_for(result, timeout=provider.timeout_seconds)
    except asyncio.TimeoutError:
        logger.warning(
            "LLM resolver '%s' timed out after %ss; using fallback '%s'",
            name, provider.timeout_seconds, fallback,
        )
        return fallback
    except Exception as e:
        logger.warning("LLM resolver '%s' failed (%s); using fallback '%s'", name, e, fallback, exc_info=True)
        return fallback

    if result is None:
        logger.debug("LLM resolver '%s' returned None; using fallback '%s'", name, fallback)
        return fallback
    if not isinstance(result, str):
        logger.warning(
            "LLM resolver '%s' returned a non-string value (%r); using fallback '%s'",
            name, result, fallback,
        )
        return fallback
    chosen = config.llm.providers.get(result)
    if chosen is None:
        logger.warning(
            "LLM resolver '%s' returned an unknown provider '%s'; using fallback '%s'",
            name, result, fallback,
        )
        return fallback
    if chosen.type == "resolver":
        logger.warning(
            "LLM resolver '%s' returned another resolver '%s' (nesting is not supported); "
            "using fallback '%s'",
            name, result, fallback,
        )
        return fallback
    logger.info("LLM resolver '%s' selected provider '%s'", name, result)
    return result
