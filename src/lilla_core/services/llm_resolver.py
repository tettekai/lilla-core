"""`llm.providers` の resolver 型エントリを、LLM 呼び出しごとに具体プロバイダー名へ展開する。

使うキーの決め方（呼び出し側の `llm_name` → `!model` の上書き → `llm.default`）は
変えず、決まったキーが `type: resolver` のときだけ、ホストが `CONFIG_ROOT` 配下に
置いたスクリプトの `resolve(ctx)` を呼んで具体プロバイダー名を得る。展開は深さ 1 で、
`None`・未知の名前・別の resolver 名・例外・タイムアウトはいずれも警告ログを出して
そのエントリの `fallback` に落とし、会話は止めない。

`run_conversation` はツールループ内の各 LLM 呼び出しの前（最初の呼び出しを含む）に
展開をやり直し、その回しで既に呼んだツール名と前回選んだ具体プロバイダー名を
文脈に載せる。スクリプトは状態を持たず、前回と同じを続けるならその名前を返す。

LLM 呼び出しが別プロバイダーへ回す対象の失敗で終わったら、`run_conversation` は
`reselect_llm_name` で失敗の情報を載せて `resolve(ctx)` を呼び直し、別の具体
プロバイダー名が返れば同じリクエストをそちらへ送る（resolver エントリの
`max_reselections` 回まで）。呼び直しでは `fallback` に落とさず、別の具体プロバイダー名を
返さなかったら元のエラーで終わる。
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
from lilla_core.services.llm_provider_cooldown import (
    LlmFailure,
    UnavailableProvider,
    failure_from_error,
    is_reselectable_failure,
    unavailable_providers,
)

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
    全文、画像本体（data URL・バイト列）、ツールの引数は載せない。
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
    #: その回しで既に呼んだツール名（出現順・重複あり）。最初の LLM 呼び出しでは空。
    #: 引数や結果本文は載せない。
    called_tool_names: tuple[str, ...] = ()
    #: その回しで前回選んだ具体プロバイダー名。最初の LLM 呼び出しでは `None`。
    previous_provider: str | None = None
    #: 失敗したため記憶中の具体プロバイダー（名前順）。解除時刻を過ぎたものは含まない。
    #: コアは記憶中のプロバイダーへの送信を止めない（避けるかどうかはスクリプトが決める）。
    unavailable_providers: tuple[UnavailableProvider, ...] = ()
    #: 失敗後に呼び直したときだけ、直前の送信の失敗。通常の呼び出しでは `None`。
    failure: LlmFailure | None = None
    #: 失敗後に呼び直したときだけ、この LLM 呼び出しで何回目の選び直しか（1 始まり）。
    #: 通常の呼び出しでは 0。
    reselection: int = 0


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


def _build_context(
    name: str,
    provider,
    config: AppConfig,
    *,
    client_type: str,
    discord_channel_id: int | None,
    user_content: Any,
    has_image: bool,
    called_tool_names: tuple[str, ...],
    previous_provider: str | None,
    failure: LlmFailure | None = None,
    reselection: int = 0,
) -> LlmResolveContext:
    """resolver スクリプトへ渡す文脈を組み立てる。"""
    channel_name = None
    if discord_channel_id is not None:
        entry = config.discord.find_channel_by_id(discord_channel_id)
        channel_name = entry.name if entry is not None else None
    return LlmResolveContext(
        client_type=client_type,
        resolver_name=name,
        fallback=provider.fallback,
        provider_names=tuple(config.llm.providers),
        discord_channel_id=discord_channel_id,
        channel_name=channel_name,
        user_text=summarize_user_text(user_content),
        has_image=has_image,
        called_tool_names=tuple(called_tool_names),
        previous_provider=previous_provider,
        unavailable_providers=unavailable_providers(),
        failure=failure,
        reselection=reselection,
    )


async def _call_resolve(
    name: str, provider, config: AppConfig, ctx: LlmResolveContext, outcome: str
) -> tuple[bool, Any]:
    """スクリプトの `resolve(ctx)` を呼んで `(呼べたか, 戻り値)` を返す。

    `resolve` が無い・例外・タイムアウトは警告ログ（`outcome` は落とし先の説明）を出して
    `(False, None)` を返す。
    """
    try:
        func = _load_resolve_function(provider, config)
        if func is None:
            logger.warning("LLM resolver '%s' has no resolve function; %s", name, outcome)
            return False, None
        result = func(ctx)
        if inspect.isawaitable(result):
            result = await asyncio.wait_for(result, timeout=provider.timeout_seconds)
    except asyncio.TimeoutError:
        logger.warning(
            "LLM resolver '%s' timed out after %ss; %s", name, provider.timeout_seconds, outcome
        )
        return False, None
    except Exception as e:
        logger.warning("LLM resolver '%s' failed (%s); %s", name, e, outcome, exc_info=True)
        return False, None
    return True, result


def _invalid_choice(result: Any, config: AppConfig) -> str | None:
    """`resolve` の戻り値が具体プロバイダー名でなければ、その理由（ログ用）を返す。"""
    if not isinstance(result, str):
        return f"a non-string value ({result!r})"
    chosen = config.llm.providers.get(result)
    if chosen is None:
        return f"an unknown provider '{result}'"
    if chosen.type == "resolver":
        return f"another resolver '{result}' (nesting is not supported)"
    return None


async def resolve_llm_name(
    llm_name: str | None,
    *,
    client_type: str,
    discord_channel_id: int | None = None,
    user_content: Any = None,
    has_image: bool = False,
    called_tool_names: tuple[str, ...] = (),
    previous_provider: str | None = None,
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
        called_tool_names: その回しで既に呼んだツール名（出現順・重複あり）。
        previous_provider: その回しで前回選んだ具体プロバイダー名（最初は `None`）。
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
    ctx = _build_context(
        name,
        provider,
        config,
        client_type=client_type,
        discord_channel_id=discord_channel_id,
        user_content=user_content,
        has_image=has_image,
        called_tool_names=called_tool_names,
        previous_provider=previous_provider,
    )
    ok, result = await _call_resolve(
        name, provider, config, ctx, f"using fallback '{fallback}'"
    )
    if not ok:
        return fallback
    if result is None:
        logger.debug("LLM resolver '%s' returned None; using fallback '%s'", name, fallback)
        return fallback
    reason = _invalid_choice(result, config)
    if reason is not None:
        logger.warning("LLM resolver '%s' returned %s; using fallback '%s'", name, reason, fallback)
        return fallback
    logger.info("LLM resolver '%s' selected provider '%s'", name, result)
    return result


async def reselect_llm_name(
    llm_name: str | None,
    error: BaseException,
    *,
    failed_provider: str | None,
    reselection: int,
    client_type: str,
    discord_channel_id: int | None = None,
    user_content: Any = None,
    has_image: bool = False,
    called_tool_names: tuple[str, ...] = (),
    config: AppConfig | None = None,
) -> str | None:
    """LLM 呼び出しの失敗後に resolver を呼び直し、送り直す具体プロバイダー名を返す。

    選び直さない（`None` を返す）のは次のとき。呼び出し元は元のエラーで終わらせる。

    - キーが resolver 型でない（具体プロバイダーを直接指定した会話）
    - 失敗が別プロバイダーへ回す対象でない（`is_reselectable_failure`。送信前検査の拒否・
      `400` / `401` / `403` などの 4xx・応答の解釈の失敗など）
    - `reselection`（この LLM 呼び出しで何回目の選び直しか。1 始まり）が resolver エントリの
      `max_reselections` を超えた
    - スクリプトが `None`・失敗したのと同じ名前・未知の名前・別の resolver 名・文字列以外を
      返した、または例外・タイムアウト（通常の展開と違い `fallback` には落とさない）

    文脈には通常の項目に加え、`failure`（直前の失敗）と `reselection` を載せる。失敗した
    プロバイダーは送信の時点で記憶されているため、`unavailable_providers` にも入っている
    （`cooldown_seconds: 0` などで記憶しなかった場合を除く）。

    Args:
        llm_name: 呼び出し側が決めたキー（`run_conversation` の `llm_name`）。
        error: 直前の LLM 呼び出しの例外。
        failed_provider: 失敗した呼び出しで使った具体プロバイダー名。
        reselection: この LLM 呼び出しで何回目の選び直しか（1 始まり）。
        client_type: 会話の送信元クライアント種別。
        discord_channel_id: Discord からの会話ならそのチャンネル ID。
        user_content: 直近のユーザー発話の content。
        has_image: 今回のユーザー発言に画像パートがあるか。
        called_tool_names: その回しで既に呼んだツール名（出現順・重複あり）。
        config: 使う設定。省略時は `get_config()`。

    Returns:
        送り直す具体プロバイダー名。選び直さないなら `None`。
    """
    config = config or get_config()
    name = llm_name or config.llm.default
    provider = config.llm.providers.get(name)
    if provider is None or provider.type != "resolver":
        return None
    if not is_reselectable_failure(error):
        return None
    if reselection > (provider.max_reselections or 0):
        logger.warning(
            "LLM resolver '%s' reached max_reselections (%s); giving up",
            name, provider.max_reselections,
        )
        return None

    failure = failure_from_error(error)
    ctx = _build_context(
        name,
        provider,
        config,
        client_type=client_type,
        discord_channel_id=discord_channel_id,
        user_content=user_content,
        has_image=has_image,
        called_tool_names=called_tool_names,
        previous_provider=failed_provider,
        failure=failure,
        reselection=reselection,
    )
    giving_up = "not reselecting a provider"
    ok, result = await _call_resolve(name, provider, config, ctx, giving_up)
    if not ok:
        return None
    if result is None:
        logger.info(
            "LLM resolver '%s' returned None after provider '%s' failed; %s",
            name, failed_provider, giving_up,
        )
        return None
    reason = _invalid_choice(result, config)
    if reason is not None:
        logger.warning("LLM resolver '%s' returned %s; %s", name, reason, giving_up)
        return None
    if result == failed_provider:
        logger.info(
            "LLM resolver '%s' returned the failed provider '%s' again; %s",
            name, result, giving_up,
        )
        return None
    logger.warning(
        "LLM resolver '%s' reselected provider '%s' after provider '%s' failed (%s)",
        name, result, failed_provider, _describe(failure),
    )
    return result


def _describe(failure: LlmFailure) -> str:
    """ログ用に失敗の種類を短く表す。"""
    if failure.status is not None:
        return f"HTTP {failure.status}"
    return failure.error_kind
