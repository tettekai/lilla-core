"""LLM のツール呼び出しループの共通部品。

LLM を呼ぶ → `tool_calls` を実行して結果を `tool` メッセージで返す → 最終応答まで
繰り返す、という処理だけを受け持つ。会話（`services/conversation_service.py`）と
専門家ツール（`builtin_tools/llm_expert.py`）の両方がこれを使い、拡張が自前の
エージェント的なツールを作るときにも使える。

呼び出し元ごとに違う部分（各 LLM 呼び出しの前に使うプロバイダーの選び方、
`needs_auth` を検出したときに打ち切るか）は引数で渡す。resolver の再判定・
`final_reply_provider` による書き直し・META アクションの適用・再認証案内の付け足しは
呼び出し元の責任で、ここには入れない。
"""
from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from lilla_core.api.llm_client import chat_to_llm_with_tools
from lilla_core.loaders.llm_tool_loader import execute_tool_call
from lilla_core.utils.datetime_utils import ensure_utc

logger = logging.getLogger(__name__)

# 各 LLM 呼び出しの前に呼ばれ、その回に使うプロバイダー名を返すコールバック。
# 引数はこのループで既に実行したツール名（出現順・重複あり）と、前回選んだプロバイダー名
# （最初は None）。
ProviderSelector = Callable[[tuple[str, ...], str | None], Awaitable[str | None]]


def build_tool_message_content(result: dict) -> str:
    """tool 実行結果から、LLM へ返す tool メッセージの content 文字列を構築する。

    `data` を優先し、未設定なら `memory_entry` にフォールバックする。文字列以外は
    JSON シリアライズする。datetime は naive を UTC とみなしローカルタイムゾーンの
    ISO8601 文字列に変換する。
    """
    tool_content = result.get("data") or result.get("memory_entry") or ""
    if not isinstance(tool_content, str):
        def _default(obj):
            if isinstance(obj, datetime):
                return ensure_utc(obj).astimezone().isoformat()
            raise TypeError(
                f"Object of type {type(obj).__name__} is not JSON serializable"
            )
        tool_content = json.dumps(tool_content, ensure_ascii=False, default=_default)
    return tool_content


@dataclass
class ToolLoopResult:
    """`run_tool_loop` の結果。

    Attributes
    ----------
    content : str | None
        最終応答（tool_calls なし）の本文。上限到達・`needs_auth` での打ち切り時は None。
    provider : str | None
        最後に LLM を呼んだプロバイダー名（最終応答を返したプロバイダー）。
    limit_reached : bool
        最終応答の前に往復回数の上限に達したか。
    stopped_on_needs_auth : bool
        `stop_on_needs_auth=True` で `needs_auth` を検出して打ち切ったか。
    needs_auth_list : list[dict]
        ツール結果の `needs_auth_list` を出現順に連結したもの。
    called_tool_names : list[str]
        実行したツール名（出現順・重複あり。引数 JSON が壊れていて実行しなかったものは含まない）。
    iterations : int
        ツールを実行した往復の回数。
    """

    content: str | None = None
    provider: str | None = None
    limit_reached: bool = False
    stopped_on_needs_auth: bool = False
    needs_auth_list: list[dict] = field(default_factory=list)
    called_tool_names: list[str] = field(default_factory=list)
    iterations: int = 0


def _parse_arguments(tc: dict) -> tuple[Any, dict | None]:
    """tool_call の引数を解釈する。

    Returns
    -------
    tuple
        `(引数, None)`。引数 JSON が壊れていれば `(None, エラー結果の dict)`。
    """
    arguments = tc["function"]["arguments"]
    if not isinstance(arguments, str):
        return arguments, None
    try:
        return json.loads(arguments), None
    except json.JSONDecodeError as e:
        logger.warning(
            "Failed to parse tool call arguments as JSON (%s): %s",
            tc["function"]["name"],
            e,
        )
        error_message = f"引数の JSON が壊れています: {e}"
        return None, {
            "success": False,
            "tool_name": tc["function"]["name"],
            "memory_entry": error_message,
            "data": None,
            "error": error_message,
        }


async def run_tool_loop(
    messages: list[dict],
    *,
    system_prompt: str,
    tools: list,
    llm_tools: dict,
    context: dict,
    max_iterations: int,
    llm_name: str | None = None,
    select_provider: ProviderSelector | None = None,
    stop_on_needs_auth: bool = False,
) -> ToolLoopResult:
    """LLM のツール呼び出しループを最終応答か上限まで回す。

    ツール呼び出しかどうかは `finish_reason` ではなく `tool_calls` の有無で判定する。
    引数 JSON が壊れた呼び出しは実行せず、エラー内容を `tool` メッセージで返して続ける。
    `messages` は呼び出し元のリストをそのまま伸ばす（assistant の tool_calls と tool の結果を
    足す。最終応答は足さない）ため、呼び出し元は同じリストで続きを回せる。

    Parameters
    ----------
    messages : list[dict]
        LLM へ送るメッセージ列。その場で追記される。
    system_prompt : str
        システムプロンプト。
    tools : list
        LLM に渡す tools パラメータ（`build_tools_param` の戻り値）。
    llm_tools : dict
        ツールレジストリ（`load_llm_tools()` の戻り値）。
    context : dict
        `execute_tool_call` に渡すツール実行 context。
    max_iterations : int
        ツールを実行する往復の上限。
    llm_name : str | None
        `select_provider` を渡さないときに毎回使うプロバイダー名。
    select_provider : ProviderSelector | None
        各 LLM 呼び出しの前（最初を含む）に呼んでプロバイダー名を決めるコールバック。
    stop_on_needs_auth : bool
        True なら `needs_auth` の結果が出た時点で、残りの呼び出しを実行せずに打ち切る。
        False なら続けて、`needs_auth_list` を集めるだけにする。

    Returns
    -------
    ToolLoopResult
        ループの結果。
    """
    result = ToolLoopResult()
    provider: str | None = None

    while result.iterations < max_iterations:
        if select_provider is not None:
            provider = await select_provider(tuple(result.called_tool_names), provider)
        else:
            provider = llm_name
        result.provider = provider
        response = await chat_to_llm_with_tools(
            messages, system_prompt=system_prompt, tools=tools, llm_name=provider
        )

        if not response["tool_calls"]:
            result.content = response["content"] or ""
            return result

        messages.append(response["raw_message"])
        for tc in response["tool_calls"]:
            arguments, tool_result = _parse_arguments(tc)
            if tool_result is None:
                result.called_tool_names.append(tc["function"]["name"])
                tool_result = await execute_tool_call(
                    tc["function"]["name"],
                    arguments,
                    llm_tools,
                    context,
                )
                if tool_result.get("needs_auth"):
                    result.needs_auth_list.extend(tool_result.get("needs_auth_list", []))
                    if stop_on_needs_auth:
                        result.stopped_on_needs_auth = True
                        return result
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": build_tool_message_content(tool_result),
            })
        result.iterations += 1

    result.limit_reached = True
    return result
