"""src/lilla_core/api/llm_client.py のテスト。

他テストが `sys.modules["lilla_core.api.llm_client"]` を一時的にモックへ差し替える
ことがあるため、`importlib.util.spec_from_file_location` で
`sys.modules` の "lilla_core.api.llm_client" キーと衝突しない専用の名前で本物の
モジュールを読み込み、以後はそのモジュールオブジェクトを直接参照する
（`patch.object` を使い、文字列パス経由の `patch("lilla_core.api.llm_client....")` は
使わない）。

このロードの副作用として `lilla_core.core.config`（本物）が `sys.modules` に
キャッシュされる。後続テストへの影響を防ぐため、読み込み後に
`lilla_core.core.config` のキャッシュを明示的に取り除く。
"""
from __future__ import annotations

import importlib.util
import json
import logging
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

_llm_client_path = Path(__file__).parent.parent / "src" / "lilla_core" / "api" / "llm_client.py"
_spec = importlib.util.spec_from_file_location(
    "llm_client_under_test", str(_llm_client_path)
)
llm_client = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(llm_client)
sys.modules.pop("lilla_core.core.config", None)

_PROVIDER = MagicMock(
    type="openai_compat",
    url="https://example.com/v1",
    model="test-model",
    api_key_env=None,
    extra_params={},
)


async def test_chat_to_llm_uses_content_when_present() -> None:
    response_body = json.dumps(
        {"choices": [{"message": {"content": "こんにちは", "reasoning_content": "考え中"}}]}
    )
    with (
        patch.object(llm_client, "get_config") as mock_get_config,
        patch.object(llm_client, "send_http_request", new=AsyncMock(return_value=response_body)),
    ):
        mock_get_config.return_value.get_llm_provider.return_value = _PROVIDER
        mock_get_config.return_value.system_prompt = "system"
        result = await llm_client.chat_to_llm("こんにちは")

    assert result == "こんにちは"


async def test_chat_to_llm_falls_back_to_reasoning_content_when_content_empty(
    caplog,
) -> None:
    response_body = json.dumps(
        {"choices": [{"message": {"content": "", "reasoning_content": "結論はこれです"}}]}
    )
    with (
        patch.object(llm_client, "get_config") as mock_get_config,
        patch.object(llm_client, "send_http_request", new=AsyncMock(return_value=response_body)),
        caplog.at_level(logging.WARNING),
    ):
        mock_get_config.return_value.get_llm_provider.return_value = _PROVIDER
        mock_get_config.return_value.system_prompt = "system"
        result = await llm_client.chat_to_llm("質問")

    assert result == "結論はこれです"
    assert any(
        "falling back to reasoning_content" in record.message for record in caplog.records
    )


async def test_chat_to_llm_returns_empty_string_when_both_missing() -> None:
    response_body = json.dumps({"choices": [{"message": {"content": None}}]})
    with (
        patch.object(llm_client, "get_config") as mock_get_config,
        patch.object(llm_client, "send_http_request", new=AsyncMock(return_value=response_body)),
    ):
        mock_get_config.return_value.get_llm_provider.return_value = _PROVIDER
        mock_get_config.return_value.system_prompt = "system"
        result = await llm_client.chat_to_llm("質問")

    assert result == ""


async def test_chat_to_llm_with_tools_falls_back_to_reasoning_content_when_no_tool_calls(
    caplog,
) -> None:
    response_body = json.dumps(
        {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": "", "reasoning_content": "最終回答はこちら"},
                }
            ]
        }
    )
    with (
        patch.object(llm_client, "get_config") as mock_get_config,
        patch.object(llm_client, "send_http_request", new=AsyncMock(return_value=response_body)),
        caplog.at_level(logging.WARNING),
    ):
        mock_get_config.return_value.get_llm_provider.return_value = _PROVIDER
        mock_get_config.return_value.system_prompt = "system"
        result = await llm_client.chat_to_llm_with_tools([{"role": "user", "content": "質問"}])

    assert result["content"] == "最終回答はこちら"
    assert result["finish_reason"] == "stop"
    assert any(
        "falling back to reasoning_content" in record.message for record in caplog.records
    )


async def test_chat_to_llm_with_tools_keeps_content_none_when_tool_calls_present() -> None:
    response_body = json.dumps(
        {
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "content": None,
                        "reasoning_content": "ツールを呼びます",
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "function": {"name": "dummy", "arguments": "{}"},
                            }
                        ],
                    },
                }
            ]
        }
    )
    with (
        patch.object(llm_client, "get_config") as mock_get_config,
        patch.object(llm_client, "send_http_request", new=AsyncMock(return_value=response_body)),
    ):
        mock_get_config.return_value.get_llm_provider.return_value = _PROVIDER
        mock_get_config.return_value.system_prompt = "system"
        result = await llm_client.chat_to_llm_with_tools([{"role": "user", "content": "質問"}])

    assert result["content"] is None
    assert result["finish_reason"] == "tool_calls"


async def test_chat_to_llm_with_resolver_name_uses_fallback_without_script() -> None:
    """resolver 型の名前を直接渡すと、スクリプトを呼ばずにその fallback で呼ぶ。"""
    resolver = MagicMock(type="resolver", fallback="concrete")
    providers = {"router": resolver, "concrete": _PROVIDER}
    response_body = json.dumps({"choices": [{"message": {"content": "ok"}}]})
    with (
        patch.object(llm_client, "get_config") as mock_get_config,
        patch.object(
            llm_client, "send_http_request", new=AsyncMock(return_value=response_body)
        ) as mock_send,
    ):
        mock_get_config.return_value.get_llm_provider.side_effect = lambda name=None: providers[name]
        mock_get_config.return_value.system_prompt = "system"
        result = await llm_client.chat_to_llm("hi", llm_name="router")

    assert result == "ok"
    assert mock_send.call_args.kwargs["data"]["model"] == "test-model"
