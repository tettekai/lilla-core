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

import pytest

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


class TestSendGuard:
    """送信直前の拒否リスト検査（`core/llm_send_guard.py`）との結線を検証する。

    拒否リストにはダミーの語だけを使う（実在の個人情報は使わない）。
    """

    DUMMY = "Qvxdummyterm"

    @pytest.fixture
    def guarded(self, tmp_path):
        """ダミー語 1 つの拒否リストを有効にし、送信関数のモックを返す。"""
        guard_globals = llm_client.ensure_llm_request_allowed.__globals__
        guard_globals["clear_cache"]()
        list_path = tmp_path / "blocklist.json"
        list_path.write_text(json.dumps([self.DUMMY]), encoding="utf-8")
        guard_cfg = MagicMock()
        guard_cfg.llm.send_blocklist_path = str(list_path)
        response_body = json.dumps({
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "output": [],
        })
        send = AsyncMock(return_value=response_body)
        with (
            patch.dict(guard_globals, {"get_config": lambda: guard_cfg}),
            patch.object(llm_client, "get_config") as mock_get_config,
            patch.object(llm_client, "send_http_request", new=send),
        ):
            mock_get_config.return_value.system_prompt = "system"
            yield mock_get_config, send
        guard_globals["clear_cache"]()

    def _provider(self, **extra):
        return MagicMock(
            type="openai_compat",
            url="https://example.com/v1",
            model="test-model",
            api_key_env=extra.pop("api_key_env", None),
            extra_params=extra.get("extra_params", {}),
        )

    @pytest.mark.parametrize(
        "call",
        [
            "messages", "with_tools_messages", "tool_definition",
            "responses_input", "responses_instructions", "extra_params",
        ],
    )
    async def test_dummy_term_stops_send(self, guarded, call, caplog) -> None:
        mock_get_config, send = guarded
        caplog.set_level(logging.DEBUG)
        extra = {"extra_params": {"metadata": self.DUMMY}} if call == "extra_params" else {}
        mock_get_config.return_value.get_llm_provider.return_value = self._provider(**extra)
        msgs = [{"role": "user", "content": self.DUMMY if "messages" in call or call == "responses_input" else "hi"}]
        with pytest.raises(llm_client.LLMError) as exc_info:
            if call == "messages":
                await llm_client.chat_to_llm(msgs)
            elif call == "with_tools_messages":
                await llm_client.chat_to_llm_with_tools(msgs)
            elif call == "tool_definition":
                tools = [{"type": "function", "function": {"name": "x", "description": self.DUMMY}}]
                await llm_client.chat_to_llm_with_tools(msgs, tools=tools)
            elif call == "responses_input":
                await llm_client.chat_to_llm_responses(msgs)
            elif call == "responses_instructions":
                await llm_client.chat_to_llm_responses(msgs, system_prompt=self.DUMMY)
            else:
                await llm_client.chat_to_llm(msgs)
        send.assert_not_awaited()
        assert self.DUMMY.casefold() not in str(exc_info.value).casefold()
        assert self.DUMMY.casefold() not in caplog.text.casefold()

    async def test_ollama_blocked_before_wake(self, guarded) -> None:
        mock_get_config, send = guarded
        provider = self._provider()
        provider.type = "ollama"
        mock_get_config.return_value.get_llm_provider.return_value = provider
        with pytest.raises(llm_client.LLMError):
            await llm_client.chat_to_llm(self.DUMMY)
        send.assert_not_awaited()

    async def test_header_only_term_does_not_stop_send(self, guarded, monkeypatch) -> None:
        """API キー（ヘッダー）にだけ語があっても止めない。"""
        mock_get_config, send = guarded
        monkeypatch.setenv("DUMMY_GUARD_KEY", self.DUMMY)
        mock_get_config.return_value.get_llm_provider.return_value = self._provider(
            api_key_env="DUMMY_GUARD_KEY"
        )
        assert await llm_client.chat_to_llm("hi") == "ok"
        assert send.await_args.kwargs["headers"]["Authorization"] == f"Bearer {self.DUMMY}"

    async def test_unreadable_list_stops_send(self, guarded, tmp_path) -> None:
        mock_get_config, send = guarded
        guard_globals = llm_client.ensure_llm_request_allowed.__globals__
        guard_globals["clear_cache"]()
        guard_globals["get_config"]().llm.send_blocklist_path = str(tmp_path / "missing.json")
        mock_get_config.return_value.get_llm_provider.return_value = self._provider()
        with pytest.raises(llm_client.LLMError):
            await llm_client.chat_to_llm("hi")
        send.assert_not_awaited()


class TestProviderPrompt:
    """具体プロバイダーの `prompt`（追記）がシステムプロンプトの末尾へ足されることを検証する。"""

    @pytest.fixture
    def env(self, tmp_path):
        """`CONFIG_ROOT` を tmp_path に向け、追記ファイルと送信モックを用意する。"""
        (tmp_path / "prompts").mkdir()
        (tmp_path / "prompts" / "mimo.md").write_text("MIMO ADDITION\n", encoding="utf-8")
        (tmp_path / "prompts" / "fb.md").write_text("FALLBACK ADDITION", encoding="utf-8")
        response_body = json.dumps({
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "output": [],
        })
        send = AsyncMock(return_value=response_body)
        with (
            patch.object(llm_client, "get_config") as mock_get_config,
            patch.object(llm_client, "send_http_request", new=send),
        ):
            cfg = mock_get_config.return_value
            cfg.system_prompt = "DEFAULT SYSTEM"
            cfg.env.config_root = tmp_path
            cfg.llm.default = "plain"
            yield cfg, send, tmp_path

    @staticmethod
    def _provider(prompt=None, **kw):
        return MagicMock(
            type=kw.get("type", "openai_compat"),
            url="https://example.com/v1",
            model="test-model",
            api_key_env=None,
            extra_params={},
            prompt=prompt,
            fallback=kw.get("fallback"),
        )

    @staticmethod
    def _use(cfg, providers):
        cfg.get_llm_provider.side_effect = lambda name=None: providers[name or "plain"]

    @staticmethod
    def _system(send):
        return send.await_args.kwargs["data"]["messages"][0]["content"]

    async def test_relative_file_prompt_is_appended(self, env) -> None:
        cfg, send, _ = env
        self._use(cfg, {"mimo": self._provider("file:prompts/mimo.md")})
        await llm_client.chat_to_llm("hi", system_prompt="BASE", llm_name="mimo")
        assert self._system(send) == "BASE\n\nMIMO ADDITION"

    async def test_default_system_prompt_gets_addition(self, env) -> None:
        cfg, send, _ = env
        self._use(cfg, {"mimo": self._provider("file:${config_root}/prompts/mimo.md")})
        await llm_client.chat_to_llm("hi", llm_name="mimo")
        assert self._system(send) == "DEFAULT SYSTEM\n\nMIMO ADDITION"

    async def test_provider_without_prompt_keeps_system_prompt(self, env) -> None:
        cfg, send, _ = env
        self._use(cfg, {"plain": self._provider()})
        await llm_client.chat_to_llm_with_tools(
            [{"role": "user", "content": "hi"}], system_prompt="BASE"
        )
        assert self._system(send) == "BASE"

    async def test_with_tools_uses_addition(self, env) -> None:
        cfg, send, _ = env
        self._use(cfg, {"mimo": self._provider("dir:prompts")})
        await llm_client.chat_to_llm_with_tools(
            [{"role": "user", "content": "hi"}], system_prompt="BASE", llm_name="mimo"
        )
        # dir: はファイル名昇順（fb.md → mimo.md）で読む
        assert self._system(send) == "BASE\n\nFALLBACK ADDITION\nMIMO ADDITION"

    async def test_resolver_direct_call_uses_fallback_prompt(self, env) -> None:
        """resolver 名の直呼びは fallback 先の追記を使い、resolver 自身の prompt は使わない。"""
        cfg, send, _ = env
        self._use(cfg, {
            "router": self._provider("file:prompts/mimo.md", type="resolver", fallback="fb"),
            "fb": self._provider("file:prompts/fb.md"),
        })
        await llm_client.chat_to_llm("hi", system_prompt="BASE", llm_name="router")
        assert self._system(send) == "BASE\n\nFALLBACK ADDITION"

    async def test_existing_system_message_gets_addition(self, env) -> None:
        cfg, send, _ = env
        self._use(cfg, {"mimo": self._provider("file:prompts/mimo.md")})
        original = [{"role": "system", "content": "GIVEN"}, {"role": "user", "content": "hi"}]
        await llm_client.chat_to_llm(original, llm_name="mimo")
        assert self._system(send) == "GIVEN\n\nMIMO ADDITION"
        assert original[0]["content"] == "GIVEN"

    async def test_responses_instructions_get_addition(self, env) -> None:
        cfg, send, _ = env
        self._use(cfg, {"mimo": self._provider("file:prompts/mimo.md")})
        await llm_client.chat_to_llm_responses(
            [{"role": "user", "content": "hi"}], system_prompt="BASE", llm_name="mimo"
        )
        assert send.await_args.kwargs["data"]["instructions"] == "BASE\n\nMIMO ADDITION"

    async def test_addition_follows_provider_of_each_call(self, env) -> None:
        """ツールループで具体プロバイダーが変わると、その呼び出しの追記も変わる。"""
        cfg, send, _ = env
        self._use(cfg, {
            "mimo": self._provider("file:prompts/mimo.md"),
            "fb": self._provider("file:prompts/fb.md"),
            "plain": self._provider(),
        })
        messages = [{"role": "user", "content": "hi"}]
        seen = []
        for name in ("mimo", "fb", "plain"):
            await llm_client.chat_to_llm_with_tools(messages, system_prompt="BASE", llm_name=name)
            seen.append(self._system(send))
        assert seen == ["BASE\n\nMIMO ADDITION", "BASE\n\nFALLBACK ADDITION", "BASE"]
        assert messages == [{"role": "user", "content": "hi"}]

    async def test_prompt_is_read_on_every_call(self, env) -> None:
        cfg, send, tmp_path = env
        self._use(cfg, {"mimo": self._provider("file:prompts/mimo.md")})
        await llm_client.chat_to_llm("hi", system_prompt="BASE", llm_name="mimo")
        (tmp_path / "prompts" / "mimo.md").write_text("CHANGED", encoding="utf-8")
        await llm_client.chat_to_llm("hi", system_prompt="BASE", llm_name="mimo")
        assert self._system(send) == "BASE\n\nCHANGED"
