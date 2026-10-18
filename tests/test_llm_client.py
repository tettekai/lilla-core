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


class TestSendPaths:
    """4 つの送信経路（Ollama ネイティブ・Chat Completions・tools 付き・Responses API）の
    リクエストの組み立てと応答の解釈、共通の送信処理（検査・WOL・タイムアウト・例外ラップ）を検証する。
    """

    CHAT_BODY = json.dumps({"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]})

    @pytest.fixture
    def env(self, monkeypatch):
        """設定・送信・WOL・送信前検査をモックし、呼ばれた順を記録する。"""
        order: list[str] = []
        send = AsyncMock(return_value=self.CHAT_BODY)
        send.side_effect = lambda *a, **kw: order.append("send") or send.return_value
        wake = AsyncMock(side_effect=lambda provider: order.append("wake"))
        guard = MagicMock(side_effect=lambda data: order.append("guard"))
        monkeypatch.setenv("DUMMY_LLM_KEY", "secret-key")
        with (
            patch.object(llm_client, "get_config") as mock_get_config,
            patch.object(llm_client, "send_http_request", new=send),
            patch.object(llm_client, "_wake_ollama_if_needed", new=wake),
            patch.object(llm_client, "ensure_llm_request_allowed", new=guard),
        ):
            cfg = mock_get_config.return_value
            cfg.system_prompt = "SYSTEM"
            yield cfg, send, wake, guard, order

    @staticmethod
    def _provider(provider_type="openai_compat", **kw):
        return MagicMock(
            type=provider_type,
            url="https://llm.example/v1",
            model="m1",
            api_key_env=kw.get("api_key_env", "DUMMY_LLM_KEY"),
            extra_params=kw.get("extra_params", {}),
            prompt=None,
            timeout_seconds=kw.get("timeout_seconds", 300.0),
        )

    async def test_ollama_native_request_and_response(self, env) -> None:
        cfg, send, wake, guard, order = env
        cfg.get_llm_provider.return_value = self._provider("ollama", extra_params={"num_ctx": 8})
        send.return_value = json.dumps({"message": {"content": "", "reasoning_content": "R"}})
        result = await llm_client.chat_to_llm("hi")
        assert result == "R"
        assert order == ["guard", "wake", "send"]
        assert send.await_args.args == ("https://llm.example/v1/api/chat",)
        kwargs = send.await_args.kwargs
        assert kwargs["method"] == "POST"
        assert kwargs["data"] == {
            "model": "m1",
            "messages": [
                {"role": "system", "content": "SYSTEM"},
                {"role": "user", "content": "hi"},
            ],
            "stream": False,
            "options": {"temperature": 0.7, "num_ctx": 8},
        }
        assert kwargs.get("headers") is None
        assert kwargs["timeout"] == 300

    async def test_chat_completions_request(self, env) -> None:
        cfg, send, wake, guard, order = env
        cfg.get_llm_provider.return_value = self._provider(extra_params={"reasoning_effort": "low"})
        assert await llm_client.chat_to_llm("hi", system_prompt="BASE") == "ok"
        assert order == ["guard", "send"]
        wake.assert_not_awaited()
        assert send.await_args.args == ("https://llm.example/v1/chat/completions",)
        kwargs = send.await_args.kwargs
        assert kwargs["data"] == {
            "model": "m1",
            "messages": [
                {"role": "system", "content": "BASE"},
                {"role": "user", "content": "hi"},
            ],
            "stream": False,
            "reasoning_effort": "low",
        }
        assert kwargs["headers"] == {"Authorization": "Bearer secret-key"}
        assert kwargs["timeout"] == 300

    async def test_chat_completions_without_api_key_env_sends_empty_bearer(self, env) -> None:
        cfg, send, *_ = env
        cfg.get_llm_provider.return_value = self._provider(api_key_env=None)
        await llm_client.chat_to_llm("hi")
        assert send.await_args.kwargs["headers"] == {"Authorization": "Bearer "}

    async def test_with_tools_request_and_response(self, env) -> None:
        cfg, send, wake, guard, order = env
        cfg.get_llm_provider.return_value = self._provider()
        tool_calls = [{"id": "c1", "function": {"name": "x", "arguments": "{}"}}]
        message = {"content": "thinking aloud", "tool_calls": tool_calls}
        send.return_value = json.dumps({"choices": [{"message": message, "finish_reason": "tool_calls"}]})
        tools = [{"type": "function", "function": {"name": "x"}}]
        result = await llm_client.chat_to_llm_with_tools(
            [{"role": "user", "content": "hi"}], system_prompt="BASE", tools=tools
        )
        assert result == {
            "content": "thinking aloud",
            "tool_calls": tool_calls,
            "finish_reason": "tool_calls",
            "raw_message": message,
        }
        assert order == ["guard", "send"]
        assert send.await_args.args == ("https://llm.example/v1/chat/completions",)
        data = send.await_args.kwargs["data"]
        assert data["tools"] == tools
        assert data["tool_choice"] == "auto"
        assert send.await_args.kwargs["headers"] == {"Authorization": "Bearer secret-key"}
        assert send.await_args.kwargs["timeout"] == 300

    async def test_with_tools_without_tools_omits_tool_keys(self, env) -> None:
        cfg, send, *_ = env
        cfg.get_llm_provider.return_value = self._provider()
        send.return_value = json.dumps({"choices": [{"message": {"content": "ok"}}]})
        result = await llm_client.chat_to_llm_with_tools([{"role": "user", "content": "hi"}], tools=[])
        assert result["finish_reason"] == "stop"
        assert result["tool_calls"] is None
        assert "tools" not in send.await_args.kwargs["data"]
        assert "tool_choice" not in send.await_args.kwargs["data"]

    async def test_with_tools_ollama_uses_v1_endpoint_and_wakes(self, env) -> None:
        cfg, send, wake, guard, order = env
        cfg.get_llm_provider.return_value = self._provider("ollama")
        await llm_client.chat_to_llm_with_tools([{"role": "user", "content": "hi"}])
        assert order == ["guard", "wake", "send"]
        assert send.await_args.args == ("https://llm.example/v1/v1/chat/completions",)
        # Ollama には API キーを渡さない（api_key_env が書かれていても使わない）
        assert send.await_args.kwargs["headers"] == {"Authorization": "Bearer "}
        assert "options" not in send.await_args.kwargs["data"]

    async def test_responses_request_and_response(self, env) -> None:
        cfg, send, wake, guard, order = env
        cfg.get_llm_provider.return_value = self._provider(extra_params={"store": False})
        output = [
            {"type": "web_search_call", "id": "ws1"},
            {"type": "message", "content": [
                {"type": "output_text", "text": "A"},
                {"type": "other"},
                {"type": "output_text", "text": "B"},
            ]},
        ]
        send.return_value = json.dumps({"output": output})
        tools = [{"type": "x_search"}]
        msgs = [{"role": "user", "content": "hi"}]
        result = await llm_client.chat_to_llm_responses(msgs, system_prompt="INS", tools=tools)
        assert result == {"content": "AB", "tool_calls": None, "finish_reason": "stop", "raw_output": output}
        assert order == ["guard", "send"]
        assert send.await_args.args == ("https://llm.example/v1/responses",)
        assert send.await_args.kwargs["data"] == {
            "model": "m1", "input": msgs, "stream": False, "store": False,
            "instructions": "INS", "tools": tools,
        }
        assert send.await_args.kwargs["headers"] == {"Authorization": "Bearer secret-key"}
        assert send.await_args.kwargs["timeout"] == 300

    async def test_responses_function_call_and_no_instructions(self, env) -> None:
        cfg, send, *_ = env
        cfg.get_llm_provider.return_value = self._provider()
        call = {"type": "function_call", "name": "x", "arguments": "{}"}
        send.return_value = json.dumps({"output": [call]})
        result = await llm_client.chat_to_llm_responses([{"role": "user", "content": "hi"}])
        assert result["content"] is None
        assert result["tool_calls"] == [call]
        assert result["finish_reason"] == "tool_calls"
        # system_prompt が無ければ既定のシステムプロンプトも入れない
        assert "instructions" not in send.await_args.kwargs["data"]
        assert "tools" not in send.await_args.kwargs["data"]

    async def test_responses_empty_output(self, env) -> None:
        cfg, send, *_ = env
        cfg.get_llm_provider.return_value = self._provider()
        send.return_value = json.dumps({"output": None})
        result = await llm_client.chat_to_llm_responses([{"role": "user", "content": "hi"}])
        assert result == {"content": None, "tool_calls": None, "finish_reason": "stop", "raw_output": []}

    async def test_responses_rejects_ollama(self, env) -> None:
        cfg, send, wake, guard, _ = env
        cfg.get_llm_provider.return_value = self._provider("ollama")
        with pytest.raises(ValueError, match="only supported for the openai_compat"):
            await llm_client.chat_to_llm_responses([{"role": "user", "content": "hi"}])
        guard.assert_not_called()
        send.assert_not_awaited()

    @pytest.mark.parametrize("call", ["chat", "tools"])
    async def test_unsupported_type_raises_value_error(self, env, call) -> None:
        cfg, send, *_ = env
        cfg.get_llm_provider.return_value = self._provider("anthropic")
        with pytest.raises(ValueError, match="Unsupported LLM provider type: anthropic"):
            if call == "chat":
                await llm_client.chat_to_llm("hi")
            else:
                await llm_client.chat_to_llm_with_tools([{"role": "user", "content": "hi"}])
        send.assert_not_awaited()

    @pytest.mark.parametrize(
        ("call", "provider_type"),
        [("chat", "ollama"), ("chat", "openai_compat"), ("tools", "ollama"),
         ("tools", "openai_compat"), ("responses", "openai_compat")],
    )
    @pytest.mark.parametrize("failure", ["http", "json", "shape"])
    async def test_failures_are_wrapped_in_llm_error(self, env, call, provider_type, failure) -> None:
        cfg, send, *_ = env
        cfg.get_llm_provider.return_value = self._provider(provider_type)
        if failure == "http":
            send.side_effect = RuntimeError("boom")
        elif failure == "json":
            send.return_value = "not json"
        else:
            send.return_value = json.dumps({"unexpected": True} if call != "responses" else [])
        msgs = [{"role": "user", "content": "hi"}]
        with pytest.raises(llm_client.LLMError, match="^LLM call error: ") as exc_info:
            if call == "chat":
                await llm_client.chat_to_llm(msgs)
            elif call == "tools":
                await llm_client.chat_to_llm_with_tools(msgs)
            else:
                await llm_client.chat_to_llm_responses(msgs)
        assert exc_info.value.__cause__ is not None

    @pytest.mark.parametrize("call", ["chat", "tools"])
    async def test_blocked_send_does_not_wake_ollama(self, env, call) -> None:
        """送信を止める回は WOL もしない。"""
        cfg, send, wake, guard, _ = env
        cfg.get_llm_provider.return_value = self._provider("ollama")
        guard.side_effect = llm_client.LLMError("blocked")
        with pytest.raises(llm_client.LLMError):
            if call == "chat":
                await llm_client.chat_to_llm("hi")
            else:
                await llm_client.chat_to_llm_with_tools([{"role": "user", "content": "hi"}])
        wake.assert_not_awaited()
        send.assert_not_awaited()

    @pytest.mark.parametrize(
        ("call", "provider_type"),
        [("chat", "ollama"), ("chat", "openai_compat"), ("tools", "ollama"),
         ("tools", "openai_compat"), ("responses", "openai_compat")],
    )
    async def test_timeout_comes_from_provider_setting(self, env, call, provider_type) -> None:
        """タイムアウトはプロバイダー設定の `timeout_seconds` を使う。"""
        cfg, send, *_ = env
        cfg.get_llm_provider.return_value = self._provider(provider_type, timeout_seconds=42.5)
        send.return_value = json.dumps({
            "message": {"content": "ok"},
            "choices": [{"message": {"content": "ok"}}],
            "output": [],
        })
        msgs = [{"role": "user", "content": "hi"}]
        if call == "chat":
            await llm_client.chat_to_llm(msgs)
        elif call == "tools":
            await llm_client.chat_to_llm_with_tools(msgs)
        else:
            await llm_client.chat_to_llm_responses(msgs)
        assert send.await_args.kwargs["timeout"] == 42.5


class TestAdapters:
    """アダプター単体（組み立て・解釈）と、`type` からの対応表を検証する。"""

    def test_adapter_table_covers_each_type_and_api(self) -> None:
        table = llm_client._ADAPTERS
        assert isinstance(table["ollama"]["chat"], llm_client.OllamaNativeAdapter)
        assert isinstance(table["ollama"]["tools"], llm_client.ChatCompletionsAdapter)
        assert table["ollama"]["tools"].wakes_ollama is True
        assert "responses" not in table["ollama"]
        assert table["openai_compat"]["chat"] is table["openai_compat"]["tools"]
        assert isinstance(table["openai_compat"]["responses"], llm_client.ResponsesAdapter)
        assert table["openai_compat"]["responses"].wakes_ollama is False

    def test_select_adapter_rejects_unknown_type(self) -> None:
        with pytest.raises(ValueError, match="Unsupported LLM provider type: x"):
            llm_client._select_adapter(MagicMock(type="x"), "chat")

    def test_chat_completions_parse_does_not_fall_back_when_tool_calls(self) -> None:
        """tool_calls がある回は reasoning_content へフォールバックしない。"""
        adapter = llm_client.ChatCompletionsAdapter("/chat/completions", use_api_key=True)
        message = {"content": "", "reasoning_content": "R", "tool_calls": [{"id": "c"}]}
        result = adapter.parse_response({"choices": [{"message": message}]})
        assert result.content == ""
        assert result.tool_calls == [{"id": "c"}]
        assert result.finish_reason == "stop"
        assert result.raw is message

    def test_chat_completions_parse_falls_back_without_tool_calls(self) -> None:
        adapter = llm_client.ChatCompletionsAdapter("/chat/completions", use_api_key=True)
        message = {"content": None, "reasoning_content": "R"}
        result = adapter.parse_response({"choices": [{"message": message, "finish_reason": "length"}]})
        assert result.content == "R"
        assert result.finish_reason == "length"

    def test_build_request_does_not_mutate_messages(self) -> None:
        provider = MagicMock(url="https://u", model="m", extra_params={}, api_key_env=None)
        messages = [{"role": "user", "content": "hi"}]
        for adapter in (
            llm_client.OllamaNativeAdapter(),
            llm_client.ChatCompletionsAdapter("/chat/completions", use_api_key=True),
            llm_client.ResponsesAdapter(),
        ):
            adapter.build_request(
                provider, messages, system_prompt="S", provider_prompt="P", tools=None
            )
        assert messages == [{"role": "user", "content": "hi"}]


class TestUsageRecording:
    """各送信経路で応答の使用量が記録されること（#239）。"""

    @pytest.fixture
    def env(self, monkeypatch):
        """設定・送信・WOL・送信前検査・記録関数をモックする。"""
        send = AsyncMock()
        record = MagicMock()
        with (
            patch.object(llm_client, "get_config") as mock_get_config,
            patch.object(llm_client, "send_http_request", new=send),
            patch.object(llm_client, "_wake_ollama_if_needed", new=AsyncMock()),
            patch.object(llm_client, "ensure_llm_request_allowed", new=MagicMock()),
            patch.object(llm_client, "record_llm_usage", new=record),
        ):
            cfg = mock_get_config.return_value
            cfg.system_prompt = "SYSTEM"
            cfg.llm.default = "main"
            yield cfg, send, record

    @staticmethod
    def _provider(provider_type="openai_compat", **kw):
        return MagicMock(
            type=provider_type,
            url="https://llm.example/v1",
            model="m1",
            api_key_env=None,
            extra_params={},
            prompt=None,
            timeout_seconds=300.0,
            **kw,
        )

    async def test_chat_completions_usage(self, env) -> None:
        cfg, send, record = env
        provider = self._provider()
        cfg.get_llm_provider.return_value = provider
        send.return_value = json.dumps({
            "choices": [{"message": {"content": "ok"}}],
            "usage": {
                "prompt_tokens": 12,
                "completion_tokens": 5,
                "total_tokens": 17,
                "completion_tokens_details": {"reasoning_tokens": 3},
            },
        })
        assert await llm_client.chat_to_llm("hi", llm_name="grok") == "ok"
        kwargs = record.call_args.kwargs
        assert kwargs["provider_name"] == "grok"
        assert kwargs["provider"] is provider
        assert kwargs["api"] == "chat"
        assert isinstance(kwargs["duration_ms"], int) and kwargs["duration_ms"] >= 0
        usage = kwargs["usage"]
        assert (usage.input_tokens, usage.output_tokens, usage.total_tokens) == (12, 5, 17)
        assert usage.raw["completion_tokens_details"] == {"reasoning_tokens": 3}

    async def test_tools_path_uses_default_provider_name(self, env) -> None:
        cfg, send, record = env
        cfg.get_llm_provider.return_value = self._provider()
        send.return_value = json.dumps({
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 2},
        })
        await llm_client.chat_to_llm_with_tools([{"role": "user", "content": "hi"}])
        kwargs = record.call_args.kwargs
        assert kwargs["provider_name"] == "main"
        assert kwargs["api"] == "tools"
        # 合計が無ければ入力と出力の和
        assert kwargs["usage"].total_tokens == 3

    async def test_responses_usage(self, env) -> None:
        cfg, send, record = env
        cfg.get_llm_provider.return_value = self._provider()
        send.return_value = json.dumps({
            "output": [],
            "usage": {"input_tokens": 30, "output_tokens": 10, "total_tokens": 40},
        })
        await llm_client.chat_to_llm_responses([{"role": "user", "content": "hi"}])
        kwargs = record.call_args.kwargs
        assert kwargs["api"] == "responses"
        usage = kwargs["usage"]
        assert (usage.input_tokens, usage.output_tokens, usage.total_tokens) == (30, 10, 40)

    async def test_ollama_native_usage(self, env) -> None:
        cfg, send, record = env
        cfg.get_llm_provider.return_value = self._provider("ollama")
        send.return_value = json.dumps({
            "message": {"content": "ok"},
            "prompt_eval_count": 20,
            "eval_count": 7,
            "eval_duration": 123,
        })
        await llm_client.chat_to_llm("hi")
        usage = record.call_args.kwargs["usage"]
        assert (usage.input_tokens, usage.output_tokens, usage.total_tokens) == (20, 7, 27)
        assert usage.raw == {"prompt_eval_count": 20, "eval_count": 7, "eval_duration": 123}

    async def test_missing_usage_is_recorded_as_unknown(self, env) -> None:
        cfg, send, record = env
        cfg.get_llm_provider.return_value = self._provider()
        send.return_value = json.dumps({"choices": [{"message": {"content": "ok"}}]})
        await llm_client.chat_to_llm("hi")
        record.assert_called_once()
        assert record.call_args.kwargs["usage"] is None

    async def test_resolver_name_is_recorded_as_fallback(self, env) -> None:
        cfg, send, record = env
        resolver = MagicMock(type="resolver", fallback="concrete")
        concrete = self._provider()
        cfg.get_llm_provider.side_effect = lambda name: resolver if name == "auto" else concrete
        send.return_value = json.dumps({"choices": [{"message": {"content": "ok"}}]})
        await llm_client.chat_to_llm("hi", llm_name="auto")
        assert record.call_args.kwargs["provider_name"] == "concrete"
        assert record.call_args.kwargs["provider"] is concrete

    async def test_failed_send_is_not_recorded(self, env) -> None:
        cfg, send, record = env
        cfg.get_llm_provider.return_value = self._provider()
        send.side_effect = RuntimeError("down")
        with pytest.raises(llm_client.LLMError):
            await llm_client.chat_to_llm("hi")
        record.assert_not_called()


class TestUsageRecordingFailure:
    """記録の失敗が LLM 呼び出しの結果に影響しないこと（本物の record_llm_usage を通す）。"""

    async def test_write_failure_does_not_affect_result(self, monkeypatch, caplog) -> None:
        import lilla_core.services.llm_usage as llm_usage

        def boom(record):
            raise RuntimeError("mongo down")

        monkeypatch.setattr(llm_usage, "_schedule_write", boom)
        provider = TestUsageRecording._provider()
        body = json.dumps({
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })
        with (
            patch.object(llm_client, "get_config") as mock_get_config,
            patch.object(llm_client, "send_http_request", new=AsyncMock(return_value=body)),
            patch.object(llm_client, "ensure_llm_request_allowed", new=MagicMock()),
            caplog.at_level(logging.WARNING),
        ):
            mock_get_config.return_value.get_llm_provider.return_value = provider
            mock_get_config.return_value.system_prompt = "S"
            mock_get_config.return_value.llm.default = "main"
            assert await llm_client.chat_to_llm("hi") == "ok"
        assert any("Failed to record LLM usage" in r.message for r in caplog.records)
