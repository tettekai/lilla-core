import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, NoReturn

from lilla_core.core.config import get_config, resolve_llm_provider_prompt_spec
from lilla_core.core.exceptions import LLMError
from lilla_core.core.http_util import send_http_request
from lilla_core.core.llm_send_guard import ensure_llm_request_allowed
from lilla_core.utils.resource_loader import load_text_resources

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


def _append_prompt(base: str | None, addition: str) -> str | None:
    """base の末尾へ addition を空行区切りで足す（addition が空なら base のまま）。"""
    if not addition:
        return base
    if not base:
        return addition
    return f"{base.rstrip()}\n\n{addition}"


def _ensure_system_prompt(
    messages: list[dict[str, Any]],
    system_prompt: str | None,
    provider_prompt: str = "",
) -> list[dict[str, Any]]:
    """messages の先頭にシステムプロンプトを補い、プロバイダーの追記を足す。

    既に内容のある system メッセージが含まれていれば、最初のそれ（content が文字列の
    もの）の末尾へ provider_prompt を足した新しいリストを返す。含まれない場合は
    system_prompt（未指定ならデフォルト）に provider_prompt を足して先頭に追加した
    新しいリストを返す。渡された messages 自体は書き換えない。
    """
    if _has_system_message(messages):
        if not provider_prompt:
            return messages
        result = list(messages)
        for i, m in enumerate(result):
            content = m.get("content")
            if m.get("role") == "system" and isinstance(content, str) and content.strip():
                result[i] = {**m, "content": _append_prompt(content, provider_prompt)}
                break
        return result
    effective_prompt = _append_prompt(system_prompt or get_config().system_prompt, provider_prompt)
    return [{"role": "system", "content": effective_prompt}, *messages]


def _get_provider(llm_name: str | None):
    """HTTP を出せる具体プロバイダーの設定を返す。

    `llm_name` が resolver 型なら、スクリプトは呼ばずにそのエントリの `fallback` を返す。
    回しごとの展開は `run_conversation` が済ませてから具体名を渡すため、ここへ resolver
    名が届くのは文脈を持たない直接呼び出し（`chat_to_llm` を直に呼ぶタスクや
    `!selftest` など）だけで、その場合は fallback を使う。
    """
    config = get_config()
    provider = config.get_llm_provider(llm_name)
    if provider.type == "resolver":
        logger.debug(
            "LLM provider '%s' is a resolver; using its fallback '%s' for a direct call",
            llm_name or config.llm.default, provider.fallback,
        )
        return config.get_llm_provider(provider.fallback)
    return provider


def _load_provider_prompt(provider) -> str:
    """実際に送る具体プロバイダーの `prompt`（追記）を読み込んで返す。

    `provider` は `_get_provider` が返した具体プロバイダー（resolver 名の直呼びなら
    fallback 先）で、resolver エントリ自身の `prompt` はここへ届かない。
    呼び出しのたびにディスクから解決する（既存のシステムプロンプトと同じ）。
    未設定なら空文字列。相対パスは `CONFIG_ROOT` 基準で読む。
    """
    spec = getattr(provider, "prompt", None)
    if not spec or not isinstance(spec, (str, list)):
        return ""
    config_root = get_config().env.config_root
    return load_text_resources(
        resolve_llm_provider_prompt_spec(spec, config_root), config_root
    ).strip()


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
    resolver 型の名前を渡した場合はスクリプトを呼ばず、そのエントリの fallback を使います。
    messagesには list[dict[str, Any]] 形式か、文字列（ユーザーメッセージ）を渡せます。
    文字列の場合は {"role": "user", "content": messages} に変換します。
    system_prompt を指定した場合はそれを使用し、未指定の場合はデフォルトのシステムプロンプトを使用します。
    実際に送る具体プロバイダーに `prompt` があれば、システムプロンプトの末尾へ空行区切りで足します。
    """
    if isinstance(messages, str):
        messages = [{"role": "user", "content": messages}]
    provider = _get_provider(llm_name)
    messages = _ensure_system_prompt(messages, system_prompt, _load_provider_prompt(provider))

    if provider.type == "ollama":
        return await _chat_ollama(messages, provider)
    elif provider.type == "openai_compat":
        return await _chat_openai_compat(messages, provider)
    else:
        raise ValueError(f"Unsupported LLM provider type: {provider.type}")


async def _chat_ollama(messages: list[dict[str, Any]], provider) -> str:
    """Ollama へチャットリクエストを送信し、応答を返す。WOL 処理も行う。

    送信前（WOL より前）に `ensure_llm_request_allowed` でボディを検査する。
    """
    request_data = {
        "model": provider.model,
        "messages": messages,
        "stream": False,
        "options": {"temperature": 0.7, **provider.extra_params},
    }
    ensure_llm_request_allowed(request_data)
    await _wake_ollama_if_needed(provider)

    try:
        reply = await send_http_request(
            f"{provider.url}/api/chat",
            method="POST",
            data=request_data,
            timeout=300,
        )
        return _resolve_content(json.loads(reply)["message"])
    except Exception as e:
        _raise_llm_error(e)


async def _chat_openai_compat(messages: list[dict[str, Any]], provider) -> str:
    """OpenAI 互換 API へチャットリクエストを送信し、応答を返す。

    送信前に `ensure_llm_request_allowed` でボディを検査する（ヘッダーは対象外）。
    """
    request_data = {
        "model": provider.model,
        "messages": messages,
        "stream": False,
        **provider.extra_params,
    }
    ensure_llm_request_allowed(request_data)
    api_key = _resolve_api_key(provider)
    try:
        reply = await send_http_request(
            f"{provider.url}/chat/completions",
            method="POST",
            data=request_data,
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
        システムプロンプト。未指定の場合はデフォルトを使用。実際に送る具体
        プロバイダーに `prompt` があれば、その末尾へ空行区切りで足す。
    tools : list | None
        LLM に渡すツール定義リスト
    llm_name : str | None
        使用するプロバイダー名。None の場合はデフォルト。resolver 型なら
        スクリプトを呼ばずにその fallback を使う。

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
    provider = _get_provider(llm_name)
    messages = _ensure_system_prompt(messages, system_prompt, _load_provider_prompt(provider))

    if provider.type == "ollama":
        # Ollama は OpenAI 互換エンドポイントを使用
        url = f"{provider.url}/v1/chat/completions"
        api_key = ""
    elif provider.type == "openai_compat":
        url = f"{provider.url}/chat/completions"
        api_key = _resolve_api_key(provider)
    else:
        raise ValueError(f"Unsupported LLM provider type: {provider.type}")

    request_data = _build_tools_request_data(messages, provider, tools)
    # 送信を止める回は WOL も行わないよう、検査を先に済ませる
    ensure_llm_request_allowed(request_data)
    if provider.type == "ollama":
        await _wake_ollama_if_needed(provider)

    return await _chat_with_tools_openai_compat(request_data, url, api_key)


def _build_tools_request_data(
    messages: list[dict[str, Any]],
    provider,
    tools: list | None,
) -> dict[str, Any]:
    """tools パラメータ付きの Chat Completions リクエストボディを組み立てる。"""
    request_data: dict[str, Any] = {
        "model": provider.model,
        "messages": messages,
        "stream": False,
        **provider.extra_params,
    }
    if tools:
        request_data["tools"] = tools
        request_data["tool_choice"] = "auto"
    return request_data


async def _chat_with_tools_openai_compat(
    request_data: dict[str, Any],
    url: str,
    api_key: str,
) -> dict:
    """OpenAI 互換 API へ tools パラメータ付きでリクエストを送信し、結果を返す。

    Parameters
    ----------
    request_data : dict[str, Any]
        `_build_tools_request_data` が組み立て、検査を済ませたリクエストボディ
    url : str
        エンドポイント URL
    api_key : str
        認証用 API キー

    Returns
    -------
    dict
        {"content", "tool_calls", "finish_reason", "raw_message"} を含む辞書
    """
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
        システムプロンプト。Responses API の `instructions` に渡す。実際に送る
        具体プロバイダーに `prompt` があれば、その末尾へ空行区切りで足す。
    tools : list | None
        組み込みツール（または将来の function calling 用）のツール定義リスト。
    llm_name : str | None
        使用するプロバイダー名。None の場合はデフォルト。resolver 型なら
        スクリプトを呼ばずにその fallback を使う。

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
    provider = _get_provider(llm_name)

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
    instructions = _append_prompt(system_prompt, _load_provider_prompt(provider))
    if instructions:
        request_data["instructions"] = instructions
    if tools:
        request_data["tools"] = tools
    ensure_llm_request_allowed(request_data)

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


