"""失敗した LLM プロバイダーの一時記憶（クールダウン）。

具体プロバイダーへの送信が、同じプロバイダーへの再試行を使い切ったあと（または再試行
対象外のまま）別プロバイダーへ回す価値のある失敗で終わったら、そのプロバイダーを
しばらく記憶する。記憶は resolver スクリプトの `LlmResolveContext.unavailable_providers`
に載るだけで、コアは記憶中のプロバイダーへの送信を止めない（避けるかどうかはスクリプトが
決める）。保存先はプロセスメモリで、再起動すると消える。

どの失敗を対象にするか（`is_reselectable_failure`）は、失敗後の再選択
（`services/llm_resolver.py` の `reselect_llm_name`）と共有する。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from lilla_core.core.exceptions import (
    LLMError,
    LlmSendBlockedError,
    LlmSendGuardUnavailableError,
)
from lilla_core.utils.datetime_utils import utc_now

logger = logging.getLogger(__name__)

#: 別プロバイダーへ回す対象にする HTTP ステータス（5xx はすべて対象）
RESELECTABLE_HTTP_STATUSES = frozenset({404, 408, 429})

#: 別プロバイダーへ回す対象にする HTTP 以外の失敗の種類（`LLMError.error_kind`）
RESELECTABLE_ERROR_KINDS = frozenset({"connection", "timeout"})


@dataclass(frozen=True)
class LlmFailure:
    """具体プロバイダーへの送信の失敗（resolver スクリプトへ渡す形）。

    応答本文・リクエスト本文・例外のメッセージは載せない。
    """

    #: 失敗した具体プロバイダー名。
    provider: str
    #: 失敗の種類。`"http"` / `"connection"` / `"timeout"`。
    error_kind: str
    #: HTTP エラーならそのステータス。それ以外は `None`。
    status: int | None = None
    #: 応答の `Retry-After`（秒）。無い・読めなければ `None`。
    retry_after: float | None = None


@dataclass(frozen=True)
class UnavailableProvider:
    """記憶中の失敗したプロバイダー（resolver スクリプトへ渡す形）。"""

    #: 失敗した具体プロバイダー名。
    provider: str
    #: 記憶を解除する時刻（UTC。timezone-aware）。
    until: datetime
    #: 失敗の種類（`LlmFailure.error_kind` と同じ）。
    error_kind: str
    #: HTTP エラーならそのステータス。それ以外は `None`。
    status: int | None = None


# プロバイダー名 → 記憶中のエントリ
_unavailable: dict[str, UnavailableProvider] = {}


def is_reselectable_failure(error: BaseException) -> bool:
    """別プロバイダーへ回す（＝記憶する）対象の失敗かを返す。

    対象は HTTP 404 / 408 / 429 / 5xx・接続エラー・タイムアウト。送信前検査による拒否は
    プロバイダーの失敗ではないため対象にしない。それ以外の 4xx（`400` / `401` / `403` など）と
    応答の解釈の失敗も、リクエストや設定の問題である可能性が高いため対象にしない。
    """
    if not isinstance(error, LLMError):
        return False
    if isinstance(error, (LlmSendBlockedError, LlmSendGuardUnavailableError)):
        return False
    if error.provider_name is None:
        return False
    if error.error_kind == "http":
        status = error.status
        return status is not None and (status in RESELECTABLE_HTTP_STATUSES or 500 <= status <= 599)
    return error.error_kind in RESELECTABLE_ERROR_KINDS


def failure_from_error(error: LLMError) -> LlmFailure:
    """`LLMError` から resolver スクリプトへ渡す失敗の情報を作る。"""
    return LlmFailure(
        provider=error.provider_name or "",
        error_kind=error.error_kind,
        status=error.status,
        retry_after=error.retry_after,
    )


def record_failure(error: LLMError, cooldown_seconds: float | None) -> None:
    """失敗したプロバイダーを記憶する（対象外の失敗なら何もしない）。

    記憶する長さは、応答に `Retry-After` があればその秒数（同じプロバイダーへの再試行を
    `retry_max_wait_seconds` 超過で見送った回も含む）、無ければ `cooldown_seconds`。
    長さが 0 以下なら記憶しない（既にある記憶もそのまま）。同じプロバイダーを記憶中なら
    新しい失敗で置き換える。
    """
    if not is_reselectable_failure(error):
        return
    seconds = error.retry_after if error.retry_after is not None else cooldown_seconds
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or seconds <= 0:
        return
    name = error.provider_name or ""
    entry = UnavailableProvider(
        provider=name,
        until=utc_now() + timedelta(seconds=seconds),
        error_kind=error.error_kind,
        status=error.status,
    )
    _unavailable[name] = entry
    logger.info(
        "Marked LLM provider '%s' as unavailable for %.1fs (%s)",
        name, seconds, _describe(entry),
    )


def unavailable_providers(now: datetime | None = None) -> tuple[UnavailableProvider, ...]:
    """記憶中のプロバイダーを名前順に返す（解除時刻を過ぎたものは捨てる）。"""
    now = now or utc_now()
    for name in [name for name, entry in _unavailable.items() if entry.until <= now]:
        del _unavailable[name]
    return tuple(_unavailable[name] for name in sorted(_unavailable))


def clear() -> None:
    """記憶をすべて捨てる（テスト用）。"""
    _unavailable.clear()


def _describe(entry: UnavailableProvider) -> str:
    """ログ用に失敗の種類を短く表す。"""
    if entry.status is not None:
        return f"HTTP {entry.status}"
    return entry.error_kind
