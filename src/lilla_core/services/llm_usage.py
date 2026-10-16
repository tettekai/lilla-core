"""LLM のトークン使用量を記録する。

`api/llm_client.py` の共通送信関数が応答のたびに `record_llm_usage()` を呼ぶ。
記録には応答の使用量（入力・出力・合計トークン数と生の `usage`）に、プロバイダー名・
モデル名・入口（`core/llm_call_origin.py` の文脈）・所要時間を添える。
メッセージ本文・プロンプト・ツール結果は記録しない。

書き込みは本処理を待たせないよう `asyncio` のタスクとして投げ、失敗しても
WARNING ログだけで LLM 呼び出しの結果には影響させない。記録の可否と保持日数は
`llm.usage_log`（`enabled` / `retention_days`）で決める。
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from lilla_core.core.config import get_config
from lilla_core.core.llm_call_origin import get_llm_call_origin
from lilla_core.utils.datetime_utils import utc_now

logger = logging.getLogger(__name__)

#: 生の `usage` を保存するときにたどる入れ子の深さの上限
_RAW_USAGE_MAX_DEPTH = 4

#: 書き込み中のタスク。参照を持たないと完了前にガベージコレクトされうるため保持する
_pending_writes: set[asyncio.Task] = set()


@dataclass(frozen=True)
class LlmUsage:
    """応答から取り出した使用量。応答に無かった項目は `None`（不明。推定はしない）。

    `raw` は応答の `usage`（Ollama ネイティブなら `prompt_eval_count` などの数値項目）を
    数値だけに絞ったもので、推論トークンやキャッシュトークンの内訳もここに残る。
    """

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    raw: dict[str, Any] | None = None


def _as_token_count(value: Any) -> int | None:
    """トークン数として使える値（0 以上の int。bool は除く）ならそのまま、違えば None。"""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _numeric_only(value: Any, depth: int = 0) -> Any:
    """dict の中から数値（と真偽値）だけを残した写しを返す。

    `usage` は数値の集まりのはずだが、プロバイダーが文字列を混ぜてきても本文に
    類するものを保存しないよう、数値以外は捨てる。空になった dict は None。
    """
    if isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, dict) and depth < _RAW_USAGE_MAX_DEPTH:
        result = {}
        for key, item in value.items():
            kept = _numeric_only(item, depth + 1)
            if kept is not None:
                result[str(key)] = kept
        return result or None
    return None


def build_usage(
    input_tokens: Any, output_tokens: Any, total_tokens: Any, raw: Any
) -> LlmUsage | None:
    """応答の値から `LlmUsage` を組み立てる（使用量らしき値が 1 つも無ければ None）。

    合計が無く入力・出力の両方が分かるときだけ、その和を合計にする。
    """
    inp = _as_token_count(input_tokens)
    out = _as_token_count(output_tokens)
    total = _as_token_count(total_tokens)
    if total is None and inp is not None and out is not None:
        total = inp + out
    raw_numeric = _numeric_only(raw) if isinstance(raw, dict) else None
    if inp is None and out is None and total is None and raw_numeric is None:
        return None
    return LlmUsage(inp, out, total, raw_numeric)


def estimate_cost(provider, usage: LlmUsage | None) -> float | None:
    """プロバイダーの単価から概算コストを返す（請求額とは一致しない目安）。

    単価が 1 つも設定されていなければ None。設定されている単価に対応するトークン数が
    不明なときも None（一部だけを足して少なく見せないため）。設定されていない側は 0 とみなす。
    """
    input_price = getattr(provider, "input_price_per_million_tokens", None)
    output_price = getattr(provider, "output_price_per_million_tokens", None)
    if input_price is None and output_price is None:
        return None
    if usage is None:
        return None
    cost = 0.0
    for price, tokens in ((input_price, usage.input_tokens), (output_price, usage.output_tokens)):
        if price is None:
            continue
        if tokens is None:
            return None
        cost += tokens * price / 1_000_000
    return cost


def build_usage_record(
    *,
    provider_name: str,
    provider,
    api: str,
    duration_ms: int,
    usage: LlmUsage | None,
) -> dict[str, Any]:
    """`llm_usage` コレクションへ保存する 1 件分の記録を組み立てる。

    入口は現在の `contextvars`（`get_llm_call_origin()`）から読む。保存するのは
    識別子と数値だけで、メッセージ本文・プロンプト・ツール結果は含めない。
    """
    config = get_config()
    origin = get_llm_call_origin()
    now = utc_now()
    return {
        "created_at": now,
        "expire_at": now + timedelta(days=config.llm.usage_log.retention_days),
        "provider": provider_name,
        "provider_type": provider.type,
        "model": provider.model,
        "api": api,
        "entry": origin.entry(),
        "client_type": origin.client_type,
        "discord_channel_id": origin.discord_channel_id,
        "task_name": origin.task_name,
        "tool_name": origin.tool_name,
        "source": origin.source,
        "duration_ms": duration_ms,
        "usage_known": usage is not None and usage.total_tokens is not None,
        "input_tokens": usage.input_tokens if usage else None,
        "output_tokens": usage.output_tokens if usage else None,
        "total_tokens": usage.total_tokens if usage else None,
        "raw_usage": usage.raw if usage else None,
        "cost": estimate_cost(provider, usage),
    }


async def _write_record(record: dict[str, Any]) -> None:
    """記録を保存する。失敗は WARNING ログだけにして例外を外へ出さない。"""
    try:
        from lilla_core.repository.llm_usage_repository import get_llm_usage_repo

        await get_llm_usage_repo().insert(record)
    except Exception as e:
        logger.warning("Failed to record LLM usage: %s", e)


def _schedule_write(record: dict[str, Any]) -> None:
    """記録の保存を実行中のイベントループへタスクとして投げ、完了を待たない。"""
    task = asyncio.get_running_loop().create_task(_write_record(record))
    _pending_writes.add(task)
    task.add_done_callback(_pending_writes.discard)


def record_llm_usage(
    *,
    provider_name: str,
    provider,
    api: str,
    duration_ms: int,
    usage: LlmUsage | None,
) -> None:
    """LLM 呼び出し 1 回分の使用量を記録する（`llm.usage_log.enabled` が偽なら何もしない）。

    記録の組み立て・投入のどこで失敗しても例外は投げず、WARNING ログだけを出す
    （LLM 呼び出しは成功扱いのまま）。

    Args:
        provider_name: 実際に送った具体プロバイダーのキー（resolver なら fallback 先）。
        provider: そのプロバイダーの設定（`LlmProviderConfig`）。
        api: API の種類（`chat` / `tools` / `responses`）。
        duration_ms: 送信から応答の解釈までの所要時間（ミリ秒）。
        usage: 応答から取り出した使用量。応答に無ければ None（トークン数は不明として残す）。
    """
    try:
        if not get_config().llm.usage_log.enabled:
            return
        record = build_usage_record(
            provider_name=provider_name,
            provider=provider,
            api=api,
            duration_ms=duration_ms,
            usage=usage,
        )
        _schedule_write(record)
    except Exception as e:
        logger.warning("Failed to record LLM usage: %s", e)
