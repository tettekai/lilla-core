import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, NoReturn

from lilla_core.core.config import get_config
from lilla_core.core.exceptions import LLMError
from lilla_core.core.http_util import send_http_request

logger = logging.getLogger(__name__)

# LLM 呼び出しに失敗したときに送出する例外メッセージの接頭辞
_LLM_ERROR_PREFIX = "LLM call error"


def _raise_llm_error(error: Exception) -> NoReturn:
    """元例外を接頭辞付きの LLMError にラップして送出する。"""
    raise LLMError(f"{_LLM_ERROR_PREFIX}: {error}") from error


def _resolve_content(message: dict[str, Any]) -> str:
    """assistant メッセージから最終回答の content を取り出す。

    推論モデルが最終回答を `content` に出力せず `reasoning_content`
    （思考過程）側に書き切ってしまうことがあるため、`content` が空の場合は
    `reasoning_content` にフォールバックする。フォールバック発動時は
    頻度をモニタリングできるよう WARNING ログを出す。
    """
    content = message.get("content")
    if content:
        return content
    reasoning_content = message.get("reasoning_content")
    if reasoning_content:
        logger.warning("content is empty; falling back to reasoning_content")
        return reasoning_content
    return content or ""


def _has_system_message(messages: list[dict[str, Any]]) -> bool:
    """messages に内容のある system ロールのメッセージが含まれるか判定する。"""
    return any(
        m.get("role") == "system" and (m.get("content") or "").strip()
        for m in messages
    )


def _ensure_system_prompt(
    messages: list[dict[str, Any]],
    system_prompt: str | None,
) -> list[dict[str, Any]]:
    """messages の先頭にシステムプロンプトを補う。

    既に内容のある system メッセージが含まれていればそのまま返す。
    含まれない場合は system_prompt（未指定ならデフォルト）を先頭に追加した
    新しいリストを返す。
    """
    if _has_system_message(messages):
        return messages
    effective_prompt = system_prompt or get_config().system_prompt
    return [{"role": "system", "content": effective_prompt}, *messages]


def _resolve_api_key(provider) -> str:
    """プロバイダー設定から API キーを解決する。

    api_key_env が設定されていれば対応する環境変数の値を返し、
    未設定または環境変数が無い場合は空文字を返す。
    """
    if not provider.api_key_env:
        return ""
    return os.environ.get(provider.api_key_env, "")


async def _wake_ollama_if_needed(provider) -> None:
    """Ollama のヘルスチェックを行い、応答しなければ WOL を試みる。

    `/api/version` への疎通確認に失敗した場合、wakeup_file が設定されていれば
    それを touch して `wol_sleep_seconds` 秒だけ起動を待つ。未設定の場合は
    警告ログを出して WOL をスキップする。
    """
    try:
        await send_http_request(f"{provider.url}/api/version", method="GET", timeout=3)
    except Exception:
        if provider.wakeup_file:
            Path(provider.wakeup_file).touch(exist_ok=True)
            await asyncio.sleep(provider.wol_sleep_seconds)
        else:
            logger.warning("wakeup_file is not configured. Skipping WOL")


async def chat_to_llm(
    messages: list[dict[str, Any]] | str,
    system_prompt: str | None = None,
    llm_name: str | None = None,
) -> str:
    """
    LLMにチャットメッセージを送信し、応答を取得します。
    llm_name でプロバイダーを指定します。None の場合はデフォルトプロバイダーを使用します。
    messagesには list[dict[str, Any]] 形式か、文字列（ユーザーメッセージ）を渡せます。
    文字列の場合は {"role": "user", "content": messages} に変換します。
    system_prompt を指定した場合はそれを使用し、未指定の場合はデフォルトのシステムプロンプトを使用します。
    """
    if isinstance(messages, str):
        messages = [{"role": "user", "content": messages}]
    messages = _ensure_system_prompt(messages, system_prompt)

    provider = get_config().get_llm_provider(llm_name)

    if provider.type == "ollama":
        return await _chat_ollama(messages, provider)
    elif provider.type == "openai_compat":
        return await _chat_openai_compat(messages, provider)
    else:
        raise ValueError(f"Unsupported LLM provider type: {provider.type}")


async def _chat_ollama(messages: list[dict[str, Any]], provider) -> str:
    """Ollama へチャットリクエストを送信し、応答を返す。WOL 処理も行う。"""
    await _wake_ollama_if_needed(provider)

    try:
        reply = await send_http_request(
            f"{provider.url}/api/chat",
            method="POST",
            data={
                "model": provider.model,
                "messages": messages,
                "stream": False,
                "options": {"temperature": 0.7, **provider.extra_params},
            },
            timeout=300,
        )
        return _resolve_content(json.loads(reply)["message"])
    except Exception as e:
        _raise_llm_error(e)


async def _chat_openai_compat(messages: list[dict[str, Any]], provider) -> str:
    """OpenAI 互換 API へチャットリクエストを送信し、応答を返す。"""
    api_key = _resolve_api_key(provider)
    try:
        reply = await send_http_request(
            f"{provider.url}/chat/completions",
            method="POST",
            data={
                "model": provider.model,
                "messages": messages,
                "stream": False,
                **provider.extra_params,
            },
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=300,
        )
        return _resolve_content(json.loads(reply)["choices"][0]["message"])
    except Exception as e:
        _raise_llm_error(e)


async def chat_to_llm_with_tools(
    messages: list[dict[str, Any]],
    system_prompt: str | None = None,
    tools: list | None = None,
    llm_name: str | None = None,
) -> dict:
    """tools を渡して LLM を呼び出す（非ストリーミング）。

    Ollama の場合は /v1/chat/completions（OpenAI 互換エンドポイント）を使用する。

    Parameters
    ----------
    messages : list[dict[str, Any]]
        会話履歴
    system_prompt : str | None
        システムプロンプト。未指定の場合はデフォルトを使用。
    tools : list | None
        LLM に渡すツール定義リスト
    llm_name : str | None
        使用するプロバイダー名。None の場合はデフォルト。

    Returns
    -------
    dict
        {
            "content": str | None,       # テキスト応答（finish_reason="stop" の場合）
            "tool_calls": list | None,   # tool_calls（finish_reason="tool_calls" の場合）
            "finish_reason": str,
            "raw_message": dict          # messages に追加するための assistant メッセージ
        }
    """
    messages = _ensure_system_prompt(messages, system_prompt)

    provider = get_config().get_llm_provider(llm_name)

    if provider.type == "ollama":
        await _wake_ollama_if_needed(provider)
        # Ollama は OpenAI 互換エンドポイントを使用
        url = f"{provider.url}/v1/chat/completions"
        api_key = ""
    elif provider.type == "openai_compat":
        url = f"{provider.url}/chat/completions"
        api_key = _resolve_api_key(provider)
    else:
        raise ValueError(f"Unsupported LLM provider type: {provider.type}")

    return await _chat_with_tools_openai_compat(messages, provider, tools, url, api_key)


async def _chat_with_tools_openai_compat(
    messages: list[dict[str, Any]],
    provider,
    tools: list | None,
    url: str,
    api_key: str,
) -> dict:
    """OpenAI 互換 API へ tools パラメータ付きでリクエストを送信し、結果を返す。

    Parameters
    ----------
    messages : list[dict[str, Any]]
        会話履歴（システムプロンプト含む）
    provider : LlmProviderConfig
        使用するプロバイダーの設定
    tools : list | None
        ツール定義リスト
    url : str
        エンドポイント URL
    api_key : str
        認証用 API キー

    Returns
    -------
    dict
        {"content", "tool_calls", "finish_reason", "raw_message"} を含む辞書
    """
    request_data: dict[str, Any] = {
        "model": provider.model,
        "messages": messages,
        "stream": False,
        **provider.extra_params,
    }
    if tools:
        request_data["tools"] = tools
        request_data["tool_choice"] = "auto"

    try:
        reply = await send_http_request(
            url,
            method="POST",
            data=request_data,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=300,
        )
        response = json.loads(reply)
        choice = response["choices"][0]
        message = choice["message"]
        finish_reason = choice.get("finish_reason", "stop")
        tool_calls = message.get("tool_calls")
        content = message.get("content") if tool_calls else _resolve_content(message)

        return {
            "content": content,
            "tool_calls": tool_calls,
            "finish_reason": finish_reason,
            "raw_message": message,
        }
    except Exception as e:
        _raise_llm_error(e)


async def chat_to_llm_responses(
    messages: list[dict[str, Any]],
    system_prompt: str | None = None,
    tools: list | None = None,
    llm_name: str | None = None,
) -> dict:
    """Responses API（`/responses`）経由でLLMを呼び出す。

    Grok（xAI）の組み込みツール（Built-in Tools）は Chat Completions API では
    利用できず、Responses API 経由でのみ利用可能なため、その専用経路を提供する。

    tools には組み込みツール（`{"type": "x_search"}` 等）に加え、将来的には
    function calling 用のツール定義も渡せる形にしておく。現時点では組み込み
    ツール専用の用途で使うのみ（呼び出し側で function calling 用ツールを
    渡さない運用とする）。

    組み込みツールはサーバー側で自律的に複数回呼ばれる場合がある（xAI 側の
    agentic tool calling 仕様）。こちら側でループを実装する必要はない。

    Parameters
    ----------
    messages : list[dict[str, Any]]
        会話履歴。Responses API の `input` にそのまま渡す。
    system_prompt : str | None
        システムプロンプト。Responses API の `instructions` に渡す。
    tools : list | None
        組み込みツール（または将来の function calling 用）のツール定義リスト。
    llm_name : str | None
        使用するプロバイダー名。None の場合はデフォルト。

    Returns
    -------
    dict
        {
            "content": str | None,        # テキスト応答
            "tool_calls": list | None,    # function_call アイテム（組み込みツールでは通常 None）
            "finish_reason": "stop" | "tool_calls",
            "raw_output": list            # Responses API の output 配列そのまま
        }
    """
    provider = get_config().get_llm_provider(llm_name)

    if provider.type != "openai_compat":
        raise ValueError(
            "Responses API is only supported for the openai_compat provider. "
            f"Specified provider type: {provider.type}"
        )

    api_key = _resolve_api_key(provider)

    request_data: dict[str, Any] = {
        "model": provider.model,
        "input": messages,
        "stream": False,
        **provider.extra_params,
    }
    if system_prompt:
        request_data["instructions"] = system_prompt
    if tools:
        request_data["tools"] = tools

    try:
        reply = await send_http_request(
            f"{provider.url}/responses",
            method="POST",
            data=request_data,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=300,
        )
        response = json.loads(reply)
        output = response.get("output", []) or []

        content_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for item in output:
            item_type = item.get("type")
            if item_type == "message":
                for part in item.get("content", []) or []:
                    if part.get("type") == "output_text":
                        content_parts.append(part.get("text", ""))
            elif item_type == "function_call":
                tool_calls.append(item)

        content = "".join(content_parts) if content_parts else None
        tool_calls_result = tool_calls or None
        finish_reason = "tool_calls" if tool_calls_result else "stop"

        return {
            "content": content,
            "tool_calls": tool_calls_result,
            "finish_reason": finish_reason,
            "raw_output": output,
        }
    except Exception as e:
        _raise_llm_error(e)


