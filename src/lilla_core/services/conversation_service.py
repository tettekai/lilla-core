"""会話サービス。

tool_call ループと会話履歴の読み書きを担う共通ロジック。
Discord・WebSocket など複数のエントリポイントから再利用する。
"""
from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from lilla_core.core.config import get_config
from lilla_core.core.extension import (
    ConversationContext,
    build_tool_context,
    get_conversation_start_hooks,
)
from lilla_core.core.runtime_state import is_tools_disabled
from lilla_core.api.llm_client import chat_to_llm, chat_to_llm_with_tools
from lilla_core.loaders.llm_tool_loader import build_tools_param, execute_tool_call
from lilla_core.services.memory_manager import get_memory_manager
from lilla_core.services.message_util import extract_meta_block, prepend_timestamp_prefix
from lilla_core.services.session_memory_manager import get_session_memory_manager
from lilla_core.ui.messages import t
from lilla_core.utils.datetime_utils import ensure_utc, local_now

logger = logging.getLogger(__name__)

_config = get_config()

def _stamp_user_content(content):
    """user メッセージの content 先頭に "[Apr 28 11:22] " 形式のタイムスタンプを付与する。

    str / list（content_parts）のいずれにも対応。元の dict は変更せず新しい値を返す。
    """
    now = local_now()
    prefix = f"[{now.strftime('%b %d %H:%M')}] "
    return prepend_timestamp_prefix(content, prefix)


def _handle_set_session_memory(action: dict) -> None:
    """META アクション `set_session_memory` をセッションメモリに反映する。

    - `value` に文字列があればセッションメモリを上書きする
    - `value` が未指定・空文字・空白のみ・文字列以外の場合はセッションメモリをクリアする
    """
    manager = get_session_memory_manager()
    value = action.get("value")
    content = value.strip() if isinstance(value, str) else ""
    if content:
        manager.set(content)
        logger.info("Updated session memory (%d characters)", len(content))
    else:
        manager.clear()
        logger.info("Cleared session memory.")


# META ブロックの actions で扱えるアクション種別 → ハンドラーの対応表。
# 新しいアクション種別はここに追加する（既存アクションと共存できる）。
_META_ACTION_HANDLERS: dict[str, Callable[[dict], None]] = {
    "set_session_memory": _handle_set_session_memory,
}


def _apply_meta_actions(reply: str) -> str:
    """LLM 最終応答から META ブロックを取り出してアクションを適用し、除去済みテキストを返す。

    - META ブロックがない、またはマーカーが壊れている場合は何もせず元のテキストを返す
    - `actions` 配列の各要素を `type` でディスパッチし、未知の種別は警告して読み飛ばす
    - 個々のアクションの適用に失敗しても、残りのアクションと本文の返却は継続する
    """
    meta, cleaned = extract_meta_block(reply)
    if meta is None:
        return cleaned
    actions = meta.get("actions")
    if not isinstance(actions, list):
        logger.warning("META block has no actions array.")
        return cleaned
    for action in actions:
        if not isinstance(action, dict):
            logger.warning("Ignored META action because it is not a dict: %r", action)
            continue
        action_type = action.get("type")
        handler = _META_ACTION_HANDLERS.get(action_type)
        if handler is None:
            logger.warning("Ignored unknown META action type: %r", action_type)
            continue
        try:
            handler(action)
        except Exception as e:
            logger.warning("Failed to apply META action %r: %s", action_type, e)
    return cleaned

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


# `build_tool_context` は `core/extension.py` に実体があり、LLM ツールと task ツールの
# 両方で共有する。ここから import できる名前は互換のため残している。
__all__ = ["build_tool_context", "build_tool_message_content", "run_conversation"]


async def run_conversation(
    llm_tools: dict,
    client_type: str = "discord",
    client_state: Any = None,
    discord_channel_id: int | None = None,
    llm_name: str | None = None,
    inject_user_content=None,
    override_last_user_content=None,
    tool_call_notifier: Callable[[str], Awaitable[None]] | None = None,
) -> str:
    """会話履歴を読み込み、tool_call ループを回して返答を返す。

    通常会話では呼び出し元が事前にユーザー発言を `MemoryManager.add_conversation` で
    保存している前提で、本関数は MongoDB から履歴を取得して LLM に送る。
    llm_tools が空の場合は chat_to_llm（非ストリーム）にフォールバックする。

    Parameters
    ----------
    llm_tools : dict
        load_llm_tools() の戻り値
    client_type : str
        メッセージ送信元のクライアント種別。
        通常会話（対話クライアント）: "discord" や拡張が増やす種別
        タスク実行: "task"
        コアは "task" だけを非対話の種別として扱い、それ以外はすべて
        対話クライアントとみなす（拡張が対話クライアント種別を
        追加してもここの判定は変更不要）。
    client_state : Any, optional
        呼び出し元のクライアント拡張が、自分の会話開始フックとツールへ届けたい
        任意の状態（接続中クライアントの集合など）。コアは中身を解釈せず、
        `ConversationContext.client_state` とツール実行 context の
        `client_state` キーにそのまま載せる。
    discord_channel_id : int, optional
        会話が行われている Discord チャンネルの ID。Discord からの呼び出し時に渡す。
        非同期依頼ツールなど、後から同じチャンネルへ結果を返すツールが参照する。
    llm_name : str, optional
        使用する LLM プロバイダー名。None の場合はデフォルトプロバイダーを使用する。
    inject_user_content : str | list, optional
        履歴の末尾に user メッセージとして追加する一時プロンプト。
        ハートビートなど MongoDB に保存しないが LLM には渡したい場合に使う。
    override_last_user_content : str | list, optional
        履歴末尾の user メッセージ content を LLM 送信時のみ差し替える。
        画像添付など、MongoDB にはテキスト placeholder を保存しつつ、
        LLM には rich content（base64 画像など）を送りたい場合に使う。
    tool_call_notifier : Callable[[str], Awaitable[None]], optional
        tool_call 発生時（llm_expert 経由のネスト呼び出しを含む）にログ行文字列を
        渡して呼び出されるコールバック。client_type == "discord" のときのみ実際に
        使われる（`execute_tool_call` 側でガードされる）。Discord 以外の呼び出し元
        （拡張が増やす対話クライアント種別, task）は渡さない想定。

    Returns
    -------
    str
        LLM からの返答全文。META ブロック（---META---...---END_META---）が
        含まれていた場合は、その `actions` を適用（`set_session_memory` なら
        SessionMemoryManager へ反映）したうえで、本文から除去した文字列を返す。
    """
    # クライアント種別ごとの会話開始フック（拡張側のクライアント固有の前処理用）。
    # 複数の拡張が同じ client_type に登録していればロード順に await する。
    # 未登録の client_type では何もしない。フックの失敗は会話本体も後続の
    # フックも止めないよう、1 件ずつ握りつぶす。
    hooks = get_conversation_start_hooks(client_type)
    if hooks:
        ctx = ConversationContext(
            client_type=client_type,
            client_state=client_state,
            discord_channel_id=discord_channel_id,
            llm_name=llm_name,
        )
        for hook in hooks:
            try:
                await hook(ctx)
            except Exception as e:
                logger.debug("Skipped running conversation start hook: %s", e)

    # 「対話クライアントかどうか」の判定は "task" 以外かで行う。コアが知っておくべき
    # 非対話の種別は "task" だけで、対話クライアント側の値（"discord" や拡張が
    # 追加する種別）はコアに列挙しない。
    if is_tools_disabled() and client_type != "task":
        llm_tools = {}

    memory_manager = get_memory_manager()
    extra = _config.conversation_prompt if client_type != "task" else ""
    system_prompt = await memory_manager.build_system_prompt(
        extra_prompt=extra,
        client_type=client_type,
        discord_channel_id=discord_channel_id,
    )
    history = await memory_manager.load_conversation_history_with_timestamps()

    if override_last_user_content is not None and history and history[-1]["role"] == "user":
        history[-1] = {"role": "user", "content": _stamp_user_content(override_last_user_content)}
    if inject_user_content is not None:
        history.append({"role": "user", "content": _stamp_user_content(inject_user_content)})

    if not llm_tools:
        reply = await chat_to_llm(history, system_prompt=system_prompt, llm_name=llm_name)
        return _apply_meta_actions(reply)

    tools_param = build_tools_param(
        llm_tools,
        client_type=client_type,
        allowed_names=_config.tools.main_available_tools,
    )
    messages = list(history)
    max_iterations = _config.llm.max_tool_call_iterations
    context = build_tool_context()
    context["client_type"] = client_type
    context["llm_tools"] = llm_tools
    if client_state is not None:
        context["client_state"] = client_state
    if discord_channel_id is not None:
        context["discord_channel_id"] = discord_channel_id
    if tool_call_notifier is not None:
        context["_tool_call_notifier"] = tool_call_notifier
    iteration = 0
    pending_reauth_notices: list[str] = []

    while iteration < max_iterations:
        response = await chat_to_llm_with_tools(
            messages, system_prompt=system_prompt, tools=tools_param, llm_name=llm_name
        )

        if response["tool_calls"]:
            messages.append(response["raw_message"])
            for tc in response["tool_calls"]:
                arguments = tc["function"]["arguments"]
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except json.JSONDecodeError as e:
                        logger.warning(
                            "Failed to parse tool call arguments as JSON (%s): %s",
                            tc["function"]["name"],
                            e,
                        )
                        error_message = f"引数の JSON が壊れています: {e}"
                        result = {
                            "success": False,
                            "tool_name": tc["function"]["name"],
                            "memory_entry": error_message,
                            "data": None,
                            "error": error_message,
                        }
                        tool_content = build_tool_message_content(result)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": tool_content,
                        })
                        continue
                result = await execute_tool_call(
                    tc["function"]["name"],
                    arguments,
                    llm_tools,
                    context,
                )
                if result.get("needs_auth"):
                    for item in result.get("needs_auth_list", []):
                        auth_service = item.get("auth_service", "")
                        auth_url = item.get("auth_url", "")
                        pending_reauth_notices.append(
                            t("conversation.reauth_required", service=auth_service, url=auth_url)
                        )
                tool_content = build_tool_message_content(result)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": tool_content,
                })
            iteration += 1
        else:
            reply = _apply_meta_actions(response["content"] or "")
            if pending_reauth_notices:
                return reply + "\n\n" + "\n\n".join(pending_reauth_notices)
            return reply

    return t("conversation.tool_call_limit")
