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
from lilla_core.core.llm_call_origin import llm_call_origin
from lilla_core.core.runtime_state import is_tools_disabled
from lilla_core.api.llm_client import chat_to_llm, chat_to_llm_with_tools
from lilla_core.loaders.llm_tool_loader import (
    MAIN_TOOLSET_TOKEN,
    build_tools_param,
    execute_tool_call,
    resolve_available_tools,
)
from lilla_core.services.llm_resolver import content_has_image, resolve_llm_name
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


async def _build_skill_list_section(tools_param: list, llm_tools: dict) -> str:
    """LLM に渡すツールに `llm_load_skill` があれば、有効なスキル一覧の節を返す。

    YAML の stem（LLM に見せる名前）は利用者が決めるため、`tools_param` の名前から
    レジストリのエントリを引き、YAML の `type` で `llm_load_skill` かどうかを判定する。
    複数あれば最初の 1 つの名前と上限設定を使う。

    Returns:
        節の文字列。該当するツールが無い・有効なスキルが無い場合は空文字列。
    """
    from lilla_core.builtin_tools import llm_load_skill

    for schema in tools_param:
        name = (schema.get("function") or {}).get("name") or schema.get("name")
        entry = llm_tools.get(name)
        if entry is not None and llm_load_skill.is_load_skill_tool(entry):
            return await llm_load_skill.build_skill_list_section(
                name, entry.get("tool_config")
            )
    return ""


# 最終応答の書き直し（`final_reply_provider`）で、捨てた最終応答の代わりに末尾へ足す指示。
# 書き直し先は会話の言語・システムプロンプトの口調に従い、ツール結果の事実は変えない。
_FINAL_REPLY_REWRITE_INSTRUCTION = (
    "Based on the conversation and tool results above, write the final reply to the user now. "
    "Write it in your usual voice and tone as defined in the system prompt, in the same language "
    "as the conversation. Do not change any numbers, dates, names, URLs, file paths, or other facts "
    "from the tool results. Do not mention that this reply is being rewritten."
)


def _final_reply_provider_for(provider_name: str | None) -> str | None:
    """最終応答を返した具体プロバイダーの `final_reply_provider` を返す。

    `provider_name` が `None` なら `llm.default` を見る。台帳に無い名前や
    未設定なら `None`（書き直さない）。
    """
    provider = _config.llm.providers.get(provider_name or _config.llm.default)
    if provider is None:
        return None
    return provider.final_reply_provider


async def _rewrite_final_reply(
    messages: list[dict], system_prompt: str, rewrite_provider: str
) -> str:
    """ツールループの最終応答を `rewrite_provider` でツールなしに 1 回だけ書き直す。

    `messages` は捨てた最終応答を含まないメッセージ列で、末尾に書き直しの指示を
    user メッセージとして足して送る（呼び出し元の `messages` は変えない）。
    書き直し先の `final_reply_provider` は見ないため、経路はこの 1 回で閉じる。
    tool_calls を含む履歴をそのまま送れるよう、`chat_to_llm` ではなく
    `chat_to_llm_with_tools` を tools なしで呼ぶ。
    """
    rewrite_messages = [
        *messages,
        {"role": "user", "content": _FINAL_REPLY_REWRITE_INSTRUCTION},
    ]
    response = await chat_to_llm_with_tools(
        rewrite_messages, system_prompt=system_prompt, tools=None, llm_name=rewrite_provider
    )
    return response["content"] or ""


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
    tool_call_notifier: Callable[[str, int], Awaitable[None]] | None = None,
    allowed_tool_names: list[str] | None = None,
    max_tool_call_iterations: int | None = None,
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
        決まったキーが `type: resolver` なら、ツールループ内の各 LLM 呼び出しの前
        （最初の呼び出しを含む）にスクリプトの `resolve(ctx)` で具体プロバイダーへ
        展開する（`services/llm_resolver.py`）。その回しで既に呼んだツール名と前回
        選んだ具体プロバイダー名を文脈に載せる。
        会話開始フックの `ConversationContext.llm_name` は展開前のキーのまま。
        ツールループで最終応答（tool_calls なし）を返した具体プロバイダーに
        `final_reply_provider` があれば、その本文は捨て、指定先へツールなしで 1 回だけ
        問い合わせた結果を最終応答にする（ツールなしの会話では書き直さない）。
    inject_user_content : str | list, optional
        履歴の末尾に user メッセージとして追加する一時プロンプト。
        ハートビートなど MongoDB に保存しないが LLM には渡したい場合に使う。
    override_last_user_content : str | list, optional
        履歴末尾の user メッセージ content を LLM 送信時のみ差し替える。
        画像添付など、MongoDB にはテキスト placeholder を保存しつつ、
        LLM には rich content（base64 画像など）を送りたい場合に使う。
    tool_call_notifier : Callable[[str, int], Awaitable[None]], optional
        ツール実行前（llm_expert 経由のネスト呼び出しを含む）に
        ``(tool_name, depth)`` を渡して呼び出されるコールバック。depth は直接
        呼び出しが 0、ネストするたびに 1 増える。client_type に関係なく、渡せば
        通知される。表示の書式（Discord の ``-#`` 行など）は呼び出し元が組み立てる。
        引数や実行結果は渡さない。通知の失敗は警告ログのみでツール実行は止めない。
    allowed_tool_names : list[str] | None, optional
        この回だけ LLM に見せるツールの許可リスト（展開済みの YAML stem。
        `llm_tool_loader.resolve_available_tools` の戻り値を想定）。None なら
        `tools.sets` の `main` セットを展開して使う（未設定ならツールなし）。
        空リストならツールを渡さない。
        いずれの場合も、続けて `supported_client_type` で絞る。
    max_tool_call_iterations : int | None, optional
        この回だけ使うツール呼び出しの往復上限。None なら
        `llm.max_tool_call_iterations` を使う。上限に達したときの扱い
        （例外にせず中断文言を返す）はどちらでも同じ。

    Returns
    -------
    str
        LLM からの返答全文。META ブロック（---META---...---END_META---）が
        含まれていた場合は、その `actions` を適用（`set_session_memory` なら
        SessionMemoryManager へ反映）したうえで、本文から除去した文字列を返す。
    """
    # 使用量の記録（`services/llm_usage.py`）が入口を知れるよう、会話のクライアント種別と
    # チャンネルを `contextvars` に積む。タスクやツールの中から呼ばれた場合は、外側が
    # 積んだタスク名・ツール名をそのまま残す。
    with llm_call_origin(client_type=client_type, discord_channel_id=discord_channel_id):
        return await _run_conversation(
            llm_tools,
            client_type=client_type,
            client_state=client_state,
            discord_channel_id=discord_channel_id,
            llm_name=llm_name,
            inject_user_content=inject_user_content,
            override_last_user_content=override_last_user_content,
            tool_call_notifier=tool_call_notifier,
            allowed_tool_names=allowed_tool_names,
            max_tool_call_iterations=max_tool_call_iterations,
        )


async def _run_conversation(
    llm_tools: dict,
    client_type: str = "discord",
    client_state: Any = None,
    discord_channel_id: int | None = None,
    llm_name: str | None = None,
    inject_user_content=None,
    override_last_user_content=None,
    tool_call_notifier: Callable[[str, int], Awaitable[None]] | None = None,
    allowed_tool_names: list[str] | None = None,
    max_tool_call_iterations: int | None = None,
) -> str:
    """`run_conversation` の本体（引数の意味は `run_conversation` を参照）。"""
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

    # 省略（None）は「`main` セットを使う」。展開結果が空（`main` 未設定を含む）なら
    # ツールを渡さない。システムプロンプトのスキル一覧は実際に渡すツールで決まるため、
    # ツールの解決はシステムプロンプトの組み立てより先に行う。
    if allowed_tool_names is None and llm_tools:
        allowed_tool_names = resolve_available_tools([MAIN_TOOLSET_TOKEN], llm_tools)

    use_tools = bool(llm_tools) and allowed_tool_names != []
    tools_param = (
        build_tools_param(
            llm_tools,
            client_type=client_type,
            allowed_names=allowed_tool_names,
        )
        if use_tools
        else []
    )

    memory_manager = get_memory_manager()
    extra = _config.conversation_prompt if client_type != "task" else ""
    system_prompt = await memory_manager.build_system_prompt(
        extra_prompt=extra,
        client_type=client_type,
        discord_channel_id=discord_channel_id,
        extra_sections=[await _build_skill_list_section(tools_param, llm_tools)],
    )
    history = await memory_manager.load_conversation_history_with_timestamps()

    override_applied = (
        override_last_user_content is not None and bool(history) and history[-1]["role"] == "user"
    )
    if override_applied:
        history[-1] = {"role": "user", "content": _stamp_user_content(override_last_user_content)}
    if inject_user_content is not None:
        history.append({"role": "user", "content": _stamp_user_content(inject_user_content)})

    # 決まったキーが resolver 型なら、LLM を呼ぶたびに具体プロバイダーへ展開する。
    # 入口（Discord・拡張のクライアント・task）ごとに複製しないよう、ここに一本化する。
    # 画像の有無は今回 LLM へ実際に渡す入力（適用された差し替え・追加分）だけから
    # 判断し、過去の履歴は見ない。具体プロバイダーのキーならスクリプトは呼ばれない。
    llm_key = llm_name
    resolve_kwargs = {
        "client_type": client_type,
        "discord_channel_id": discord_channel_id,
        "user_content": history[-1]["content"] if history and history[-1]["role"] == "user" else None,
        "has_image": (override_applied and content_has_image(override_last_user_content))
        or content_has_image(inject_user_content),
    }

    if not use_tools:
        resolved_name = await resolve_llm_name(llm_key, **resolve_kwargs)
        reply = await chat_to_llm(history, system_prompt=system_prompt, llm_name=resolved_name)
        return _apply_meta_actions(reply)

    messages = list(history)
    max_iterations = (
        max_tool_call_iterations
        if max_tool_call_iterations is not None
        else _config.llm.max_tool_call_iterations
    )
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
    # resolver へ渡す、この回しで既に呼んだツール名（出現順・重複あり）と前回の選択
    called_tool_names: list[str] = []
    resolved_name: str | None = None

    while iteration < max_iterations:
        resolved_name = await resolve_llm_name(
            llm_key,
            called_tool_names=tuple(called_tool_names),
            previous_provider=resolved_name,
            **resolve_kwargs,
        )
        response = await chat_to_llm_with_tools(
            messages, system_prompt=system_prompt, tools=tools_param, llm_name=resolved_name
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
                called_tool_names.append(tc["function"]["name"])
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
            content = response["content"] or ""
            # 最終応答を返したプロバイダーに `final_reply_provider` があれば、その本文は捨てて
            # 指定先に書き直させる（ツールループのこの箇所だけ。ツールなしの会話には効かない）
            rewrite_provider = _final_reply_provider_for(resolved_name)
            if rewrite_provider is not None:
                logger.info(
                    "Rewriting final reply from provider '%s' with provider '%s'",
                    resolved_name,
                    rewrite_provider,
                )
                content = await _rewrite_final_reply(messages, system_prompt, rewrite_provider)
            reply = _apply_meta_actions(content)
            if pending_reauth_notices:
                return reply + "\n\n" + "\n\n".join(pending_reauth_notices)
            return reply

    return t("conversation.tool_call_limit")
