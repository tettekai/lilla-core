import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn, Protocol

from lilla_core.core.config import get_config, resolve_llm_provider_prompt_spec
from lilla_core.core.exceptions import LLMError
from lilla_core.core.http_util import send_http_request
from lilla_core.core.llm_send_guard import ensure_llm_request_allowed
from lilla_core.services.llm_usage import LlmUsage, build_usage, record_llm_usage
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


def _resolve_provider(llm_name: str | None):
    """HTTP を出せる具体プロバイダーのキーと設定を返す。

    `llm_name` が resolver 型なら、スクリプトは呼ばずにそのエントリの `fallback` を返す。
    回しごとの展開は `run_conversation` が済ませてから具体名を渡すため、ここへ resolver
    名が届くのは文脈を持たない直接呼び出し（`chat_to_llm` を直に呼ぶタスクや
    `!selftest` など）だけで、その場合は fallback を使う。

    Returns:
        `(プロバイダーのキー, LlmProviderConfig)`。キーは使用量の記録に使う。
    """
    config = get_config()
    name = llm_name or config.llm.default
    provider = config.get_llm_provider(llm_name)
    if provider.type == "resolver":
        logger.debug(
            "LLM provider '%s' is a resolver; using its fallback '%s' for a direct call",
            name, provider.fallback,
        )
        return provider.fallback, config.get_llm_provider(provider.fallback)
    return name, provider


def _get_provider(llm_name: str | None):
    """HTTP を出せる具体プロバイダーの設定を返す（`_resolve_provider` の設定だけ）。"""
    return _resolve_provider(llm_name)[1]


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


@dataclass(frozen=True)
class LLMRequest:
    """アダプターが組み立てた 1 回分の送信内容。

    `body` は送信前検査（`ensure_llm_request_allowed`）の対象で、`headers` は対象外。
    `headers` が `None` ならヘッダーを付けずに送る。
    """

    url: str
    body: dict[str, Any]
    headers: dict[str, str] | None = None


@dataclass(frozen=True)
class LLMResult:
    """アダプターが応答 JSON から取り出した、プロバイダーに依らない形の結果。

    `raw` は呼び出し元へそのまま返す生データ（Chat Completions / Ollama ネイティブなら
    assistant メッセージ、Responses API なら `output` 配列）。
    `usage` は応答の使用量で、応答に無ければ None（記録上はトークン数不明になる）。
    """

    content: str | None
    tool_calls: list[dict[str, Any]] | None
    finish_reason: str
    raw: Any
    usage: LlmUsage | None = None


class LLMProviderAdapter(Protocol):
    """1 つの API 形式（エンドポイントと入出力の形）を表すプロバイダーアダプター。

    送信前検査・WOL・HTTP 送信・例外ラップは共通の送信関数（`_send`）が行い、
    アダプターはリクエストの組み立てと応答の解釈だけを受け持つ。
    """

    #: 送信前に Ollama の起動確認（WOL）を行うか
    wakes_ollama: bool

    def build_request(
        self,
        provider,
        messages: list[dict[str, Any]],
        *,
        system_prompt: str | None,
        provider_prompt: str,
        tools: list | None,
    ) -> LLMRequest:
        """会話履歴・システムプロンプト・ツール定義から送信内容を組み立てる。"""
        ...

    def parse_response(self, response: Any) -> LLMResult:
        """デコード済みの応答 JSON を共通の結果型へ変換する（使用量も取り出す）。"""
        ...


#: Ollama ネイティブの応答のうち、使用量として生のまま残す数値項目
_OLLAMA_USAGE_KEYS = (
    "prompt_eval_count",
    "eval_count",
    "total_duration",
    "load_duration",
    "prompt_eval_duration",
    "eval_duration",
)


def _openai_usage(response: Any, input_key: str, output_key: str) -> LlmUsage | None:
    """OpenAI 互換の応答の `usage` から使用量を取り出す（無ければ None）。

    Chat Completions は `prompt_tokens` / `completion_tokens`、Responses API は
    `input_tokens` / `output_tokens` を使う。どちらも合計は `total_tokens`。
    """
    usage = response.get("usage") if isinstance(response, dict) else None
    if not isinstance(usage, dict):
        return None
    return build_usage(
        usage.get(input_key), usage.get(output_key), usage.get("total_tokens"), usage
    )


def _ollama_usage(response: Any) -> LlmUsage | None:
    """Ollama ネイティブの応答（`prompt_eval_count` / `eval_count`）から使用量を取り出す。"""
    if not isinstance(response, dict):
        return None
    raw = {key: response[key] for key in _OLLAMA_USAGE_KEYS if key in response}
    if not raw:
        return None
    return build_usage(raw.get("prompt_eval_count"), raw.get("eval_count"), None, raw)


def _bearer_headers(api_key: str) -> dict[str, str]:
    """`Authorization: Bearer` ヘッダーを返す（キーが空でもヘッダー自体は付ける）。"""
    return {"Authorization": f"Bearer {api_key}"}


class OllamaNativeAdapter:
    """Ollama ネイティブの `/api/chat`（tools なし）。

    `extra_params` はボディのトップレベルではなく `options`（既定 `temperature: 0.7`）へ
    展開する。認証ヘッダーは付けない。
    """

    wakes_ollama = True

    def build_request(
        self,
        provider,
        messages: list[dict[str, Any]],
        *,
        system_prompt: str | None,
        provider_prompt: str,
        tools: list | None,
    ) -> LLMRequest:
        """`/api/chat` 向けのボディを組み立てる（tools は使わない）。"""
        return LLMRequest(
            url=f"{provider.url}/api/chat",
            body={
                "model": provider.model,
                "messages": _ensure_system_prompt(messages, system_prompt, provider_prompt),
                "stream": False,
                "options": {"temperature": 0.7, **provider.extra_params},
            },
        )

    def parse_response(self, response: Any) -> LLMResult:
        """`message` から content を取り出す（空なら `reasoning_content` へフォールバック）。"""
        message = response["message"]
        return LLMResult(
            content=_resolve_content(message),
            tool_calls=None,
            finish_reason="stop",
            raw=message,
            usage=_ollama_usage(response),
        )


class ChatCompletionsAdapter:
    """OpenAI 互換の Chat Completions（`/chat/completions` 系）。

    `path` はプロバイダーの `url` に続けるパス。`use_api_key` が偽なら `api_key_env` を
    読まず、空のキーで `Authorization` ヘッダーを付ける（Ollama の OpenAI 互換エンドポイント）。
    """

    def __init__(self, path: str, *, use_api_key: bool, wakes_ollama: bool = False) -> None:
        """エンドポイントのパスと、API キー・WOL の扱いを受け取る。"""
        self.path = path
        self.use_api_key = use_api_key
        self.wakes_ollama = wakes_ollama

    def build_request(
        self,
        provider,
        messages: list[dict[str, Any]],
        *,
        system_prompt: str | None,
        provider_prompt: str,
        tools: list | None,
    ) -> LLMRequest:
        """`extra_params` をトップレベルへ展開し、tools があれば `tool_choice: auto` と共に足す。"""
        body: dict[str, Any] = {
            "model": provider.model,
            "messages": _ensure_system_prompt(messages, system_prompt, provider_prompt),
            "stream": False,
            **provider.extra_params,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        api_key = _resolve_api_key(provider) if self.use_api_key else ""
        return LLMRequest(
            url=f"{provider.url}{self.path}",
            body=body,
            headers=_bearer_headers(api_key),
        )

    def parse_response(self, response: Any) -> LLMResult:
        """`choices[0]` を解釈する。

        tool_calls がある回は content をそのまま返し、`reasoning_content` への
        フォールバックはしない（tool_calls が無い回だけフォールバックする）。
        """
        choice = response["choices"][0]
        message = choice["message"]
        tool_calls = message.get("tool_calls")
        return LLMResult(
            content=message.get("content") if tool_calls else _resolve_content(message),
            tool_calls=tool_calls,
            finish_reason=choice.get("finish_reason", "stop"),
            raw=message,
            usage=_openai_usage(response, "prompt_tokens", "completion_tokens"),
        )


class ResponsesAdapter:
    """OpenAI 互換の Responses API（`/responses`）。

    会話履歴は `input` に、システムプロンプトは `instructions` に渡す。Chat Completions と
    違い、`system_prompt` が無ければ既定のシステムプロンプトは補わない。
    """

    wakes_ollama = False

    def build_request(
        self,
        provider,
        messages: list[dict[str, Any]],
        *,
        system_prompt: str | None,
        provider_prompt: str,
        tools: list | None,
    ) -> LLMRequest:
        """`input` / `instructions` / `tools` を持つボディを組み立てる。"""
        body: dict[str, Any] = {
            "model": provider.model,
            "input": messages,
            "stream": False,
            **provider.extra_params,
        }
        instructions = _append_prompt(system_prompt, provider_prompt)
        if instructions:
            body["instructions"] = instructions
        if tools:
            body["tools"] = tools
        return LLMRequest(
            url=f"{provider.url}/responses",
            body=body,
            headers=_bearer_headers(_resolve_api_key(provider)),
        )

    def parse_response(self, response: Any) -> LLMResult:
        """`output` の `output_text` を連結し、`function_call` アイテムを tool_calls にする。"""
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
        return LLMResult(
            content="".join(content_parts) if content_parts else None,
            tool_calls=tool_calls or None,
            finish_reason="tool_calls" if tool_calls else "stop",
            raw=output,
            usage=_openai_usage(response, "input_tokens", "output_tokens"),
        )


#: 公開関数ごとの API の種類（`_ADAPTERS` の 2 段目のキー）と、エラーメッセージ用の名前
_API_LABELS = {
    "chat": "Chat API",
    "tools": "Chat API with tools",
    "responses": "Responses API",
}

_CHAT_COMPLETIONS = ChatCompletionsAdapter("/chat/completions", use_api_key=True)

#: プロバイダーの `type` → API の種類 → アダプター の対応表（ここが唯一の分岐点）
_ADAPTERS: dict[str, dict[str, LLMProviderAdapter]] = {
    "ollama": {
        "chat": OllamaNativeAdapter(),
        # tools 付きは Ollama の OpenAI 互換エンドポイントを使う
        "tools": ChatCompletionsAdapter(
            "/v1/chat/completions", use_api_key=False, wakes_ollama=True
        ),
    },
    "openai_compat": {
        "chat": _CHAT_COMPLETIONS,
        "tools": _CHAT_COMPLETIONS,
        "responses": ResponsesAdapter(),
    },
}


def _select_adapter(provider, api: str) -> LLMProviderAdapter:
    """プロバイダーの `type` と API の種類からアダプターを選ぶ。

    Raises:
        ValueError: 未対応の `type`、またはその `type` が API の種類に対応していない場合。
    """
    adapters = _ADAPTERS.get(provider.type)
    if adapters is None:
        raise ValueError(f"Unsupported LLM provider type: {provider.type}")
    adapter = adapters.get(api)
    if adapter is None:
        supported = " / ".join(t for t, by_api in _ADAPTERS.items() if api in by_api)
        raise ValueError(
            f"{_API_LABELS[api]} is only supported for the {supported} provider. "
            f"Specified provider type: {provider.type}"
        )
    return adapter


async def _send(
    adapter: LLMProviderAdapter,
    provider,
    request: LLMRequest,
    *,
    provider_name: str,
    api: str,
) -> LLMResult:
    """送信前検査 → （必要なら）WOL → HTTP 送信 → 応答解釈 → 例外ラップ を順に行う。

    送信を止める回は WOL もしないよう、検査を最初に済ませる。タイムアウトはプロバイダー
    設定の `timeout_seconds`（具体プロバイダーの既定 300 秒）を使う。検査の例外
    （`LLMError` のサブクラス）はそのまま、送信と解釈の失敗は `LLMError` にラップして送出する。

    応答を解釈できたら、HTTP 送信から解釈までの所要時間と使用量を
    `record_llm_usage()` で記録する（記録の失敗は結果に影響しない）。
    """
    ensure_llm_request_allowed(request.body)
    if adapter.wakes_ollama:
        await _wake_ollama_if_needed(provider)
    started = time.monotonic()
    try:
        reply = await send_http_request(
            request.url,
            method="POST",
            data=request.body,
            headers=request.headers,
            timeout=provider.timeout_seconds,
        )
        result = adapter.parse_response(json.loads(reply))
    except Exception as e:
        _raise_llm_error(e)
    record_llm_usage(
        provider_name=provider_name,
        provider=provider,
        api=api,
        duration_ms=int((time.monotonic() - started) * 1000),
        usage=result.usage,
    )
    return result


async def _call(
    api: str,
    messages: list[dict[str, Any]],
    system_prompt: str | None,
    tools: list | None,
    llm_name: str | None,
) -> LLMResult:
    """プロバイダーを解決してアダプターを選び、組み立てて送る（公開関数の共通部分）。"""
    provider_name, provider = _resolve_provider(llm_name)
    adapter = _select_adapter(provider, api)
    request = adapter.build_request(
        provider,
        messages,
        system_prompt=system_prompt,
        provider_prompt=_load_provider_prompt(provider),
        tools=tools,
    )
    return await _send(adapter, provider, request, provider_name=provider_name, api=api)


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
    Ollama はネイティブの `/api/chat`、OpenAI 互換は `/chat/completions` を使います。
    """
    if isinstance(messages, str):
        messages = [{"role": "user", "content": messages}]
    result = await _call("chat", messages, system_prompt, None, llm_name)
    return result.content or ""


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
    result = await _call("tools", messages, system_prompt, tools, llm_name)
    return {
        "content": result.content,
        "tool_calls": result.tool_calls,
        "finish_reason": result.finish_reason,
        "raw_message": result.raw,
    }


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
    result = await _call("responses", messages, system_prompt, tools, llm_name)
    return {
        "content": result.content,
        "tool_calls": result.tool_calls,
        "finish_reason": result.finish_reason,
        "raw_output": result.raw,
    }
