"""LLM のツール呼び出しループの共通部品。

LLM を呼ぶ → `tool_calls` を実行して結果を `tool` メッセージで返す → 最終応答まで
繰り返す、という処理だけを受け持つ。会話（`services/conversation_service.py`）と
専門家ツール（`builtin_tools/llm_expert.py`）の両方がこれを使い、拡張が自前の
エージェント的なツールを作るときにも使える。

呼び出し元ごとに違う部分（各 LLM 呼び出しの前に使うプロバイダーの選び方、
`needs_auth` を検出したときに打ち切るか）は引数で渡す。resolver の再判定・
`final_reply_provider` による書き直し・META アクションの適用・再認証案内の付け足しは
呼び出し元の責任で、ここには入れない。

往復回数が上限に達したときの「打ち切り時のまとめ」（`llm.on_tool_call_limit: summarize`）
だけはここで行う。会話と専門家ツールのどちらで上限に達しても同じ挙動にするため。

呼び出し元が決めたツールをループの前に実行して、その呼び出しと結果を `messages` へ積む
事前実行（`run_pre_tool_calls`）もここに置く。LLM が自分でツールを呼んだときと同じ形で
積むため。
"""
from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from lilla_core.api.llm_client import chat_to_llm_with_tools
from lilla_core.core.config import get_config
from lilla_core.core.exceptions import LlmSendBlockedError, LlmSendGuardUnavailableError
from lilla_core.loaders.llm_tool_loader import execute_tool_call
from lilla_core.utils.datetime_utils import ensure_utc

logger = logging.getLogger(__name__)

# 事前実行の呼び出し ID の接頭辞。LLM が返す ID と区別できるよう、こちらで採番する。
PRE_TOOL_CALL_ID_PREFIX = "pre_tool_call_"
# 事前実行の要素からツール名を読み取れなかったときに、積む呼び出しへ使う関数名。
# 空の関数名を送ると API に弾かれうるため、空にはしない。
INVALID_PRE_TOOL_NAME = "invalid_pre_tool_call"

# 各 LLM 呼び出しの前に呼ばれ、その回に使うプロバイダー名を返すコールバック。
# 引数はこのループで既に実行したツール名（出現順・重複あり）と、前回選んだプロバイダー名
# （最初は None）。
ProviderSelector = Callable[[tuple[str, ...], str | None], Awaitable[str | None]]

# ツール呼び出しの上限に達したとき、ツールなしで 1 回だけ LLM を呼ぶ際に末尾へ足す指示。
# 打ち切りの伝達は固定文言を後ろに付けず、LLM 自身に普段の口調で書かせる。
LIMIT_SUMMARY_INSTRUCTION = (
    "The tool call limit for this request has been reached, so processing was stopped partway "
    "through. You cannot use any more tools. Write your reply to the user now, in your usual voice "
    "and tone as defined in the system prompt, in the same language as the conversation. "
    "At the very beginning of the reply, you must tell the user that processing stopped partway "
    "because the tool call limit was reached. Then briefly summarize, keeping them clearly "
    "separate: what you found out, what you have done, and what is not done yet. "
    "Never describe unfinished work as completed. Do not change any numbers, dates, names, URLs, "
    "file paths, or other facts from the tool results. End by mentioning that you can continue "
    "the remaining work if the user asks you to."
)


def build_tool_message_content(result: dict) -> str:
    """tool 実行結果から、LLM へ返す tool メッセージの content 文字列を構築する。

    `data` を優先し、未設定なら `memory_entry` にフォールバックする。文字列以外は
    JSON シリアライズする。datetime は naive を UTC とみなしローカルタイムゾーンの
    ISO8601 文字列に変換する。

    失敗時（`success` が False）で `error` があるときは、LLM が自分で入力を直せるよう
    `error` も含める。`memory_entry` と `error` が両方あって異なれば
    `"{memory_entry}: {error}"`、同じか `memory_entry` が無ければ `error` だけにする。
    失敗時にも `data` があれば捨てず、このエラー文の次の行に続ける。成功時は従来どおり。
    """
    data = result.get("data")
    if result.get("success") is False and result.get("error"):
        error_text = _build_error_text(result.get("memory_entry"), result.get("error"))
        if not data:
            return error_text
        return f"{error_text}\n{_serialize_tool_content(data)}"
    return _serialize_tool_content(data or result.get("memory_entry") or "")


def _build_error_text(memory_entry: Any, error: Any) -> str:
    """失敗時に tool メッセージへ載せるエラー文を組み立てる。

    `memory_entry` が空、または `error` と同じ文字列なら `error` だけを返し、
    同じ文を重ねない。
    """
    error_text = str(error)
    if not memory_entry or str(memory_entry) == error_text:
        return error_text
    return f"{memory_entry}: {error_text}"


def _serialize_tool_content(tool_content: Any) -> str:
    """tool メッセージの content を文字列にする。

    文字列はそのまま返し、それ以外は JSON シリアライズする（datetime は naive を UTC と
    みなしローカルタイムゾーンの ISO8601 文字列にする）。
    """
    if isinstance(tool_content, str):
        return tool_content

    def _default(obj):
        if isinstance(obj, datetime):
            return ensure_utc(obj).astimezone().isoformat()
        raise TypeError(
            f"Object of type {type(obj).__name__} is not JSON serializable"
        )
    return json.dumps(tool_content, ensure_ascii=False, default=_default)


@dataclass
class ToolLoopResult:
    """`run_tool_loop` の結果。

    Attributes
    ----------
    content : str | None
        最終応答（tool_calls なし）の本文。上限に達したときは打ち切り時のまとめの本文で、
        まとめを書かせなかった・得られなかった場合と `needs_auth` での打ち切り時は None。
    provider : str | None
        最後に LLM を呼んだプロバイダー名（最終応答、または打ち切り時のまとめを返した
        プロバイダー）。
    limit_reached : bool
        最終応答の前に往復回数の上限に達したか（まとめを返したときも True）。
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


def _pre_tool_call_error(tool_name: str, message: str) -> dict:
    """事前実行で実行しなかった（できなかった）呼び出しのエラー結果を作る。"""
    return {
        "success": False,
        "tool_name": tool_name,
        "memory_entry": message,
        "data": None,
        "error": message,
    }


def _check_pre_tool_call(item: Any, schemas: dict[str, dict]) -> tuple[str, dict, dict | None]:
    """事前実行の 1 要素を検査する。

    Returns
    -------
    tuple
        `(ツール名, 引数, None)`。実行できなければ `(ツール名, 引数, エラー結果の dict)`。
        ツール名・引数が読み取れない要素では、それぞれ `INVALID_PRE_TOOL_NAME`・空の dict を返す。
    """
    if not isinstance(item, dict):
        return INVALID_PRE_TOOL_NAME, {}, _pre_tool_call_error(
            INVALID_PRE_TOOL_NAME, "事前実行の要素が dict ではありません"
        )
    tool_name = item.get("tool")
    if not isinstance(tool_name, str) or not tool_name:
        return INVALID_PRE_TOOL_NAME, {}, _pre_tool_call_error(
            INVALID_PRE_TOOL_NAME, "事前実行の要素の 'tool' が文字列ではありません"
        )
    args = item.get("args", {})
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return tool_name, {}, _pre_tool_call_error(
            tool_name, f"ツール '{tool_name}' の引数が dict ではありません"
        )
    schema = schemas.get(tool_name)
    if schema is None:
        return tool_name, args, _pre_tool_call_error(
            tool_name, f"ツール '{tool_name}' はこの会話で使えません"
        )
    required = schema.get("function", {}).get("parameters", {}).get("required") or []
    missing = [name for name in required if name not in args]
    if missing:
        return tool_name, args, _pre_tool_call_error(
            tool_name,
            f"ツール '{tool_name}' の必須の引数がありません: {', '.join(missing)}",
        )
    return tool_name, args, None


async def run_pre_tool_calls(
    messages: list[dict],
    pre_tool_calls: list,
    *,
    tools: list,
    llm_tools: dict,
    context: dict,
) -> list[dict]:
    """呼び出し元が決めたツールを順に実行し、呼び出しと結果を `messages` の末尾へ積む。

    LLM が自分でツールを呼んだときと同じ形（assistant の `tool_calls` と `tool` の結果）で、
    1 件ごとに 1 組ずつ積む。呼び出し ID は `PRE_TOOL_CALL_ID_PREFIX` + 連番で採番する。
    実行してよいのは `tools`（LLM に渡す tools パラメータ）に関数名があるものだけ。
    要素の形が不正・使えないツール・引数が dict でない・必須の引数が無い・実行中の例外は
    例外にせず、そのツールの結果としてエラー内容を積んで後続も続ける。実行は
    `execute_tool_call` を通すため、通知（depth 0）・キャッシュも通常の呼び出しと同じ。
    往復回数の上限には数えない（呼び出し元はこのあと `run_tool_loop` を回す）。

    Parameters
    ----------
    messages : list[dict]
        LLM へ送るメッセージ列。その場で追記される。
    pre_tool_calls : list
        `{"tool": str, "args": dict}` の並び（`args` は省略可）。
    tools : list
        LLM に渡す tools パラメータ（`build_tools_param` の戻り値）。
    llm_tools : dict
        ツールレジストリ（`load_llm_tools()` の戻り値）。
    context : dict
        `execute_tool_call` に渡すツール実行 context。

    Returns
    -------
    list[dict]
        ツール結果の `needs_auth_list` を出現順に連結したもの。
    """
    schemas = {
        schema["function"]["name"]: schema
        for schema in tools
        if isinstance(schema, dict) and schema.get("function", {}).get("name")
    }
    needs_auth_list: list[dict] = []
    for index, item in enumerate(pre_tool_calls):
        tool_name, args, tool_result = _check_pre_tool_call(item, schemas)
        if tool_result is None:
            tool_result = await execute_tool_call(tool_name, args, llm_tools, context)
            if tool_result.get("needs_auth"):
                needs_auth_list.extend(tool_result.get("needs_auth_list", []))
        if tool_result.get("success") is False:
            logger.warning(
                "Pre tool call returned a failure result (%s): %s",
                tool_name,
                tool_result.get("error"),
            )
        call_id = f"{PRE_TOOL_CALL_ID_PREFIX}{index}"
        messages.append({
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": call_id,
                "type": "function",
                "function": {
                    "name": tool_name,
                    "arguments": json.dumps(args, ensure_ascii=False, default=str),
                },
            }],
        })
        messages.append({
            "role": "tool",
            "tool_call_id": call_id,
            "content": build_tool_message_content(tool_result),
        })
    return needs_auth_list


def _summary_provider_for(provider: str | None, use_final_reply_provider: bool) -> str | None:
    """打ち切り時のまとめを書かせるプロバイダー名を返す。

    最後のループで使った具体プロバイダー（`None` なら `llm.default`）に
    `final_reply_provider` があれば、まとめは最終応答にあたるため指定先を返す。
    `use_final_reply_provider` が False（すでに書き直し先で回しているとき）なら見ない。
    """
    if not use_final_reply_provider:
        return provider
    llm_config = get_config().llm
    entry = llm_config.providers.get(provider or llm_config.default)
    rewrite_provider = entry.final_reply_provider if entry is not None else None
    return rewrite_provider or provider


async def summarize_on_limit(
    messages: list[dict], *, system_prompt: str, llm_name: str | None
) -> str | None:
    """ツール呼び出しの上限に達したとき、ツールなしで 1 回だけ LLM にまとめを書かせる。

    `messages` の末尾に `LIMIT_SUMMARY_INSTRUCTION` を user メッセージとして足して送る
    （呼び出し元の `messages` は変えない）。tool_calls を含む履歴をそのまま送れるよう、
    `chat_to_llm_with_tools` を tools なしで呼ぶ。ここで書いた本文の書き直しはしない。

    Returns
    -------
    str | None
        まとめの本文。呼び出しが失敗した・応答が空なら WARNING ログを出して None
        （呼び出し元は固定文言へ戻す）。送信前検査で止めた例外だけは、通常の LLM 呼び出しと
        同じく呼び出し元へ伝える。
    """
    summary_messages = [*messages, {"role": "user", "content": LIMIT_SUMMARY_INSTRUCTION}]
    try:
        response = await chat_to_llm_with_tools(
            summary_messages, system_prompt=system_prompt, tools=None, llm_name=llm_name
        )
    except (LlmSendBlockedError, LlmSendGuardUnavailableError):
        raise
    except Exception as e:
        logger.warning(
            "Failed to summarize after reaching the tool call limit (provider: %s): %s",
            llm_name,
            e,
        )
        return None
    content = response.get("content")
    if not isinstance(content, str) or not content.strip():
        logger.warning(
            "Summary after reaching the tool call limit was empty (provider: %s)", llm_name
        )
        return None
    return content


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
    summarize_with_final_reply_provider: bool = True,
) -> ToolLoopResult:
    """LLM のツール呼び出しループを最終応答か上限まで回す。

    ツール呼び出しかどうかは `finish_reason` ではなく `tool_calls` の有無で判定する。
    引数 JSON が壊れた呼び出しは実行せず、エラー内容を `tool` メッセージで返して続ける。
    ツールが失敗結果（`success: False`）を返したときは、ツール名と `error` を WARNING ログに
    出し、`error` を含めた `tool` メッセージで返して続ける（`build_tool_message_content`）。
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
    summarize_with_final_reply_provider : bool
        上限に達してまとめを書かせるとき、最後のプロバイダーの `final_reply_provider` を
        見るか。すでに書き直し先で回しているループでは False を渡し、書き直しを重ねない。

    上限に達したときは、`llm.on_tool_call_limit` が `summarize`（既定）なら
    `summarize_on_limit` でツールなしのまとめを 1 回だけ書かせて `content` に入れる。
    `message` なら、またはまとめを得られなければ `content` は None のまま返す。

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
                if tool_result.get("success") is False:
                    # 例外を出さず失敗結果を返すだけのツールはここでしかログに残らない
                    logger.warning(
                        "Tool returned a failure result (%s): %s",
                        tc["function"]["name"],
                        tool_result.get("error"),
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
    if get_config().llm.on_tool_call_limit != "message":
        summary_provider = _summary_provider_for(provider, summarize_with_final_reply_provider)
        logger.info("Tool call limit reached; summarizing with provider '%s'", summary_provider)
        result.content = await summarize_on_limit(
            messages, system_prompt=system_prompt, llm_name=summary_provider
        )
        if result.content is not None:
            result.provider = summary_provider
    return result
