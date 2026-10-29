"""services/tool_loop.py のテスト。"""
from __future__ import annotations

import importlib
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

_MODULE_NAME = "lilla_core.services.tool_loop"


# ---------------------------------------------------------------------------
# フィクスチャ
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_llm_client() -> MagicMock:
    """lilla_core.api.llm_client モック。"""
    mock = MagicMock()
    mock.chat_to_llm_with_tools = AsyncMock()
    return mock


@pytest.fixture
def mock_loader() -> MagicMock:
    """lilla_core.loaders.llm_tool_loader モック。"""
    mock = MagicMock()
    mock.execute_tool_call = AsyncMock(return_value=_ok_result())
    return mock


@pytest.fixture
def tool_loop(mock_llm_client: MagicMock, mock_loader: MagicMock):
    """patch.dict 有効後に tool_loop をロードする。"""
    with patch.dict(
        sys.modules,
        {
            "lilla_core.api.llm_client": mock_llm_client,
            "lilla_core.loaders.llm_tool_loader": mock_loader,
        },
    ):
        sys.modules.pop(_MODULE_NAME, None)
        module = importlib.import_module(_MODULE_NAME)
        yield module
        sys.modules.pop(_MODULE_NAME, None)


@pytest.fixture
def llm_config(tool_loop, monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """tool_loop が `get_config()` で読む設定のモック（`llm` 節だけを使う）。"""
    cfg = MagicMock()
    cfg.llm.on_tool_call_limit = "summarize"
    cfg.llm.default = "default-llm"
    cfg.llm.providers = {}
    monkeypatch.setattr(tool_loop, "get_config", lambda: cfg)
    return cfg


@pytest.fixture
def set_limit_mode(llm_config: MagicMock):
    """`llm.on_tool_call_limit` を差し替える関数を返す。"""

    def _set(mode: str) -> None:
        llm_config.llm.on_tool_call_limit = mode

    return _set


def _provider(final_reply_provider: str | None = None) -> MagicMock:
    provider = MagicMock()
    provider.final_reply_provider = final_reply_provider
    return provider


def _ok_result(data: str = "RESULT") -> dict:
    return {
        "success": True, "tool_name": "t", "memory_entry": None,
        "needs_auth": False, "needs_auth_list": [], "data": data, "error": None,
    }


def _auth_result(service: str = "svc") -> dict:
    return {
        "success": False, "tool_name": "t", "memory_entry": None, "needs_auth": True,
        "needs_auth_list": [{"auth_service": service, "auth_url": f"https://auth/{service}"}],
        "data": None, "error": "auth",
    }


def _tool_response(*calls: tuple[str, object], finish_reason: str = "tool_calls") -> dict:
    tool_calls = [
        {"id": f"c{i}", "function": {"name": name, "arguments": args}}
        for i, (name, args) in enumerate(calls)
    ]
    return {
        "content": None, "tool_calls": tool_calls, "finish_reason": finish_reason,
        "raw_message": {"role": "assistant", "content": None, "tool_calls": tool_calls},
    }


def _final_response(text: str = "done", finish_reason: str = "stop") -> dict:
    return {
        "content": text, "tool_calls": None, "finish_reason": finish_reason,
        "raw_message": {"role": "assistant", "content": text},
    }


async def _run(tool_loop, messages=None, **kwargs):
    params = {
        "system_prompt": "SP",
        "tools": [{"name": "t"}],
        "llm_tools": {"t": {}},
        "context": {"client_type": "discord"},
        "max_iterations": 3,
    }
    params.update(kwargs)
    return await tool_loop.run_tool_loop(messages if messages is not None else [], **params)


# ---------------------------------------------------------------------------
# TestRunToolLoop
# ---------------------------------------------------------------------------


class TestRunToolLoop:
    async def test_returns_final_reply_without_tool_calls(
        self, tool_loop, mock_llm_client: MagicMock, mock_loader: MagicMock
    ) -> None:
        """tool_calls の無い応答はそのまま最終応答になり、messages には足さない。"""
        mock_llm_client.chat_to_llm_with_tools.return_value = _final_response("hello")
        messages = [{"role": "user", "content": "hi"}]

        result = await _run(tool_loop, messages, llm_name="p1")

        assert result.content == "hello"
        assert result.provider == "p1"
        assert result.limit_reached is False
        assert result.iterations == 0
        assert messages == [{"role": "user", "content": "hi"}]
        mock_loader.execute_tool_call.assert_not_called()
        kwargs = mock_llm_client.chat_to_llm_with_tools.await_args.kwargs
        assert kwargs == {"system_prompt": "SP", "tools": [{"name": "t"}], "llm_name": "p1"}

    async def test_empty_content_becomes_empty_string(
        self, tool_loop, mock_llm_client: MagicMock
    ) -> None:
        """最終応答の content が None なら空文字列にする。"""
        mock_llm_client.chat_to_llm_with_tools.return_value = _final_response(None)

        result = await _run(tool_loop)

        assert result.content == ""

    async def test_tool_calls_are_judged_by_presence_not_finish_reason(
        self, tool_loop, mock_llm_client: MagicMock, mock_loader: MagicMock
    ) -> None:
        """finish_reason が stop でも tool_calls があれば実行し、tool_calls 判定の応答でも無ければ終える。"""
        mock_llm_client.chat_to_llm_with_tools.side_effect = [
            _tool_response(("t", "{}"), finish_reason="stop"),
            _final_response("end", finish_reason="tool_calls"),
        ]

        result = await _run(tool_loop)

        assert result.content == "end"
        assert mock_loader.execute_tool_call.await_count == 1

    async def test_tool_results_are_appended_as_tool_messages(
        self, tool_loop, mock_llm_client: MagicMock, mock_loader: MagicMock
    ) -> None:
        """assistant の tool_calls と各 tool の結果を messages に足し、引数は解釈して渡す。"""
        first = _tool_response(("a", '{"x": 1}'), ("b", {"y": 2}))
        mock_llm_client.chat_to_llm_with_tools.side_effect = [first, _final_response()]
        mock_loader.execute_tool_call.side_effect = [_ok_result("A"), _ok_result("B")]
        context = {"client_type": "discord"}
        messages: list[dict] = []

        result = await _run(tool_loop, messages, context=context)

        assert messages == [
            first["raw_message"],
            {"role": "tool", "tool_call_id": "c0", "content": "A"},
            {"role": "tool", "tool_call_id": "c1", "content": "B"},
        ]
        calls = mock_loader.execute_tool_call.await_args_list
        assert calls[0].args == ("a", {"x": 1}, {"t": {}}, context)
        assert calls[1].args == ("b", {"y": 2}, {"t": {}}, context)
        assert result.called_tool_names == ["a", "b"]
        assert result.iterations == 1

    async def test_broken_arguments_json_returns_error_and_continues(
        self, tool_loop, mock_llm_client: MagicMock, mock_loader: MagicMock
    ) -> None:
        """壊れた引数 JSON は実行せず、エラー内容を tool メッセージで返してループを続ける。"""
        mock_llm_client.chat_to_llm_with_tools.side_effect = [
            _tool_response(("broken", "{oops"), ("ok", "{}")),
            _final_response("recovered"),
        ]
        messages: list[dict] = []

        result = await _run(tool_loop, messages)

        assert result.content == "recovered"
        assert messages[1]["tool_call_id"] == "c0"
        assert "JSON" in messages[1]["content"]
        assert messages[2] == {"role": "tool", "tool_call_id": "c1", "content": "RESULT"}
        mock_loader.execute_tool_call.assert_awaited_once()
        assert result.called_tool_names == ["ok"]

    async def test_limit_reached(
        self, tool_loop, mock_llm_client: MagicMock, mock_loader: MagicMock, set_limit_mode
    ) -> None:
        """上限回数だけ往復して最終応答が無ければ limit_reached を立てる（`message` ならまとめない）。"""
        set_limit_mode("message")
        mock_llm_client.chat_to_llm_with_tools.return_value = _tool_response(("t", "{}"))

        result = await _run(tool_loop, max_iterations=2)

        assert result.limit_reached is True
        assert result.content is None
        assert result.iterations == 2
        assert mock_llm_client.chat_to_llm_with_tools.await_count == 2

    async def test_zero_iterations_reaches_limit_without_calling_llm(
        self, tool_loop, mock_llm_client: MagicMock, set_limit_mode
    ) -> None:
        """上限 0 なら LLM を呼ばずに上限到達として返す。"""
        set_limit_mode("message")
        result = await _run(tool_loop, max_iterations=0)

        assert result.limit_reached is True
        mock_llm_client.chat_to_llm_with_tools.assert_not_called()

    async def test_needs_auth_is_collected_and_loop_continues_by_default(
        self, tool_loop, mock_llm_client: MagicMock, mock_loader: MagicMock
    ) -> None:
        """既定では needs_auth の一覧を集めるだけで、残りの呼び出しもループも続ける。"""
        mock_llm_client.chat_to_llm_with_tools.side_effect = [
            _tool_response(("t", "{}"), ("t", "{}")),
            _final_response("final"),
        ]
        mock_loader.execute_tool_call.side_effect = [_auth_result("a"), _auth_result("b")]

        result = await _run(tool_loop)

        assert result.content == "final"
        assert result.stopped_on_needs_auth is False
        assert [i["auth_service"] for i in result.needs_auth_list] == ["a", "b"]
        assert mock_loader.execute_tool_call.await_count == 2

    async def test_stop_on_needs_auth_aborts_immediately(
        self, tool_loop, mock_llm_client: MagicMock, mock_loader: MagicMock
    ) -> None:
        """stop_on_needs_auth なら needs_auth の時点で残りを実行せずに打ち切る。"""
        mock_llm_client.chat_to_llm_with_tools.return_value = _tool_response(("t", "{}"), ("t", "{}"))
        mock_loader.execute_tool_call.side_effect = [_auth_result("a"), _ok_result()]

        result = await _run(tool_loop, stop_on_needs_auth=True)

        assert result.stopped_on_needs_auth is True
        assert result.content is None
        assert result.limit_reached is False
        assert [i["auth_service"] for i in result.needs_auth_list] == ["a"]
        assert mock_loader.execute_tool_call.await_count == 1
        assert mock_llm_client.chat_to_llm_with_tools.await_count == 1

    async def test_select_provider_is_called_before_each_llm_call(
        self, tool_loop, mock_llm_client: MagicMock
    ) -> None:
        """select_provider は各 LLM 呼び出しの前に、実行済みツール名と前回の選択を受け取る。"""
        mock_llm_client.chat_to_llm_with_tools.side_effect = [
            _tool_response(("a", "{}")),
            _tool_response(("b", "{}")),
            _final_response(),
        ]
        seen: list[tuple] = []

        async def select(called, previous):
            seen.append((called, previous))
            return f"p{len(seen)}"

        result = await _run(tool_loop, select_provider=select, llm_name="ignored")

        assert seen == [((), None), (("a",), "p1"), (("a", "b"), "p2")]
        assert [
            c.kwargs["llm_name"] for c in mock_llm_client.chat_to_llm_with_tools.await_args_list
        ] == ["p1", "p2", "p3"]
        assert result.provider == "p3"


# ---------------------------------------------------------------------------
# TestLimitSummary
# ---------------------------------------------------------------------------


class TestLimitSummary:
    """上限に達したときの、ツールなしのまとめのテスト。"""

    async def test_summarizes_once_without_tools(
        self, tool_loop, mock_llm_client: MagicMock, llm_config: MagicMock
    ) -> None:
        """上限に達したら最後のプロバイダーでツールなしに 1 回だけ呼び、本文を content に入れる。"""
        mock_llm_client.chat_to_llm_with_tools.side_effect = [
            _tool_response(("t", "{}")),
            _tool_response(("t", "{}")),
            _final_response("summary"),
        ]
        messages = [{"role": "user", "content": "hi"}]

        result = await _run(tool_loop, messages, max_iterations=2, llm_name="p1")

        assert result.limit_reached is True
        assert result.content == "summary"
        assert result.provider == "p1"
        calls = mock_llm_client.chat_to_llm_with_tools.await_args_list
        assert len(calls) == 3
        summary_call = calls[-1]
        assert summary_call.kwargs == {"system_prompt": "SP", "tools": None, "llm_name": "p1"}
        sent = summary_call.args[0]
        assert sent[:-1] == messages
        assert sent[-1] == {"role": "user", "content": tool_loop.LIMIT_SUMMARY_INSTRUCTION}
        # 呼び出し元の messages には指示を足さない
        assert messages[-1]["role"] == "tool"

    def test_instruction_covers_required_points(self, tool_loop) -> None:
        """指示文に、冒頭での打ち切りの伝達・3 区分のまとめ・未完了を完了と書かないこと・続きの案内が入る。"""
        text = tool_loop.LIMIT_SUMMARY_INSTRUCTION
        assert "cannot use any more tools" in text
        assert "very beginning" in text and "tool call limit" in text
        assert "what you found out" in text
        assert "what you have done" in text
        assert "what is not done yet" in text
        assert "Never describe unfinished work as completed" in text
        assert "continue" in text

    async def test_uses_final_reply_provider_of_last_provider(
        self, tool_loop, mock_llm_client: MagicMock, llm_config: MagicMock
    ) -> None:
        """最後のプロバイダーに `final_reply_provider` があれば指定先がまとめを書く（連鎖しない）。"""
        llm_config.llm.providers = {"p1": _provider("p2"), "p2": _provider("p3")}
        mock_llm_client.chat_to_llm_with_tools.side_effect = [
            _tool_response(("t", "{}")),
            _final_response("summary"),
        ]

        result = await _run(tool_loop, max_iterations=1, llm_name="p1")

        assert result.content == "summary"
        assert result.provider == "p2"
        assert mock_llm_client.chat_to_llm_with_tools.await_args.kwargs["llm_name"] == "p2"

    async def test_default_provider_is_used_for_lookup_when_name_is_none(
        self, tool_loop, mock_llm_client: MagicMock, llm_config: MagicMock
    ) -> None:
        """プロバイダー名が None なら `llm.default` の `final_reply_provider` を見る。"""
        llm_config.llm.providers = {"default-llm": _provider("p2")}
        mock_llm_client.chat_to_llm_with_tools.side_effect = [
            _tool_response(("t", "{}")),
            _final_response("summary"),
        ]

        await _run(tool_loop, max_iterations=1)

        assert mock_llm_client.chat_to_llm_with_tools.await_args.kwargs["llm_name"] == "p2"

    async def test_final_reply_provider_can_be_ignored(
        self, tool_loop, mock_llm_client: MagicMock, llm_config: MagicMock
    ) -> None:
        """`summarize_with_final_reply_provider=False` なら最後のプロバイダーのまま書かせる。"""
        llm_config.llm.providers = {"p1": _provider("p2")}
        mock_llm_client.chat_to_llm_with_tools.side_effect = [
            _tool_response(("t", "{}")),
            _final_response("summary"),
        ]

        result = await _run(
            tool_loop, max_iterations=1, llm_name="p1", summarize_with_final_reply_provider=False
        )

        assert result.provider == "p1"
        assert mock_llm_client.chat_to_llm_with_tools.await_args.kwargs["llm_name"] == "p1"

    @pytest.mark.parametrize("last", [
        _final_response(""),
        _final_response("  \n"),
        _final_response(None),
        RuntimeError("boom"),
    ])
    async def test_failure_or_empty_leaves_content_none(
        self, tool_loop, mock_llm_client: MagicMock, llm_config: MagicMock, last, caplog
    ) -> None:
        """まとめの呼び出しが失敗した・応答が空なら WARNING を出して content は None のまま。"""
        mock_llm_client.chat_to_llm_with_tools.side_effect = [_tool_response(("t", "{}")), last]

        with caplog.at_level("WARNING", logger=_MODULE_NAME):
            result = await _run(tool_loop, max_iterations=1, llm_name="p1")

        assert result.limit_reached is True
        assert result.content is None
        assert result.provider == "p1"
        assert any(r.levelname == "WARNING" for r in caplog.records)

    async def test_send_guard_errors_are_propagated(
        self, tool_loop, mock_llm_client: MagicMock, llm_config: MagicMock
    ) -> None:
        """送信前検査で止めた例外は握りつぶさず、通常の LLM 呼び出しと同じく呼び出し元へ伝える。"""
        from lilla_core.core.exceptions import LlmSendBlockedError

        mock_llm_client.chat_to_llm_with_tools.side_effect = [
            _tool_response(("t", "{}")),
            LlmSendBlockedError("blocked"),
        ]

        with pytest.raises(LlmSendBlockedError):
            await _run(tool_loop, max_iterations=1)

    async def test_needs_auth_stop_does_not_summarize(
        self, tool_loop, mock_llm_client: MagicMock, mock_loader: MagicMock, llm_config: MagicMock
    ) -> None:
        """`needs_auth` での打ち切りは上限到達ではないため、まとめを書かせない。"""
        mock_llm_client.chat_to_llm_with_tools.return_value = _tool_response(("t", "{}"))
        mock_loader.execute_tool_call.side_effect = [_auth_result("a")]

        result = await _run(tool_loop, stop_on_needs_auth=True)

        assert result.content is None
        assert mock_llm_client.chat_to_llm_with_tools.await_count == 1


# ---------------------------------------------------------------------------
# TestBuildToolMessageContent
# ---------------------------------------------------------------------------


class TestBuildToolMessageContent:
    def test_prefers_data_then_memory_entry(self, tool_loop) -> None:
        """data を優先し、無ければ memory_entry を使う。"""
        assert tool_loop.build_tool_message_content({"data": "D", "memory_entry": "M"}) == "D"
        assert tool_loop.build_tool_message_content({"data": None, "memory_entry": "M"}) == "M"
        assert tool_loop.build_tool_message_content({}) == ""

    def test_serializes_non_string_with_datetime(self, tool_loop) -> None:
        """文字列以外は JSON にし、datetime は ISO8601 にする。"""
        dt = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)
        content = tool_loop.build_tool_message_content({"data": {"at": dt, "名前": "x"}})

        assert '"名前": "x"' in content
        assert dt.astimezone().isoformat() in content

    def test_failure_includes_error_detail(self, tool_loop) -> None:
        """失敗時は memory_entry と error を両方含める。"""
        from lilla_core.tool_support.tool_result import tool_error

        content = tool_loop.build_tool_message_content(tool_error("t", "X 失敗", "詳細"))

        assert content == "X 失敗: 詳細"

    def test_failure_without_memory_entry_uses_error_only(self, tool_loop) -> None:
        """memory_entry が無い失敗結果は error だけを返す。"""
        from lilla_core.tool_support.tool_result import tool_error

        assert tool_loop.build_tool_message_content(tool_error("t", None, "詳細")) == "詳細"

    def test_failure_does_not_repeat_same_text(self, tool_loop) -> None:
        """memory_entry と error が同じなら 1 回だけ載せる（needs_auth など）。"""
        from lilla_core.tool_support.tool_result import tool_needs_auth

        result = tool_needs_auth("t", "https://auth", "再認証してください")

        assert tool_loop.build_tool_message_content(result) == "再認証してください"

    def test_failure_keeps_data_after_error(self, tool_loop) -> None:
        """失敗時に data があれば捨てず、エラー文の次の行に続ける。"""
        content = tool_loop.build_tool_message_content(
            {"success": False, "memory_entry": "M", "error": "E", "data": {"k": 1}}
        )

        assert content == 'M: E\n{"k": 1}'

    def test_failure_without_error_falls_back_to_previous_behavior(self, tool_loop) -> None:
        """失敗でも error が無ければ従来どおり data → memory_entry の順で使う。"""
        result = {"success": False, "memory_entry": "M", "error": None, "data": None}

        assert tool_loop.build_tool_message_content(result) == "M"

    def test_success_ignores_error(self, tool_loop) -> None:
        """成功時は従来どおりで、error があっても載せない。"""
        result = {"success": True, "memory_entry": "M", "error": "E", "data": "D"}

        assert tool_loop.build_tool_message_content(result) == "D"


# ---------------------------------------------------------------------------
# TestToolFailureInLoop
# ---------------------------------------------------------------------------


class TestToolFailureInLoop:
    async def test_failure_result_is_sent_with_error_and_logged(
        self, tool_loop, mock_llm_client: MagicMock, mock_loader: MagicMock, caplog
    ) -> None:
        """失敗結果の error を tool メッセージに含め、ツール名と error を WARNING ログに出す。"""
        from lilla_core.tool_support.tool_result import tool_error

        mock_llm_client.chat_to_llm_with_tools.side_effect = [
            _tool_response(("diary_draft_set", "{}")),
            _final_response("ok"),
        ]
        mock_loader.execute_tool_call.return_value = tool_error(
            "diary_draft_set", "diary_draft_set 失敗", "欠けている箇所: 見出し"
        )
        messages: list[dict] = []

        with caplog.at_level("WARNING", logger=_MODULE_NAME):
            await _run(tool_loop, messages)

        assert messages[1]["content"] == "diary_draft_set 失敗: 欠けている箇所: 見出し"
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert any(
            "diary_draft_set" in r.getMessage() and "欠けている箇所: 見出し" in r.getMessage()
            for r in warnings
        )

    async def test_success_result_is_not_logged_as_warning(
        self, tool_loop, mock_llm_client: MagicMock, caplog
    ) -> None:
        """成功結果では WARNING ログを出さない。"""
        mock_llm_client.chat_to_llm_with_tools.side_effect = [
            _tool_response(("t", "{}")),
            _final_response("ok"),
        ]

        with caplog.at_level("WARNING", logger=_MODULE_NAME):
            await _run(tool_loop)

        assert not [r for r in caplog.records if r.levelname == "WARNING"]


# ---------------------------------------------------------------------------
# TestLlmExpertLimitCatalog
# ---------------------------------------------------------------------------


class TestLlmExpertLimitCatalog:
    @pytest.mark.parametrize("locale", ["ja", "en"])
    def test_catalog_has_llm_expert_tool_call_limit(self, locale: str) -> None:
        """llm_expert の上限到達時の文言が ja / en の両方のカタログにある。"""
        path = Path(__file__).resolve().parents[1] / "src" / "lilla_core" / "locales" / f"{locale}.yaml"
        catalog = yaml.safe_load(path.read_text(encoding="utf-8"))

        assert catalog["tool"]["llm_expert"]["tool_call_limit"]


# ---------------------------------------------------------------------------
# TestRunPreToolCalls
# ---------------------------------------------------------------------------


def _schema(name: str, required: list[str] | None = None) -> dict:
    parameters: dict = {"type": "object", "properties": {}}
    if required is not None:
        parameters["required"] = required
    return {"type": "function", "function": {"name": name, "parameters": parameters}}


async def _run_pre(tool_loop, pre_tool_calls, messages=None, tools=None, context=None):
    messages = messages if messages is not None else []
    needs_auth = await tool_loop.run_pre_tool_calls(
        messages,
        pre_tool_calls,
        tools=tools if tools is not None else [_schema("climate"), _schema("light", ["on"])],
        llm_tools={"climate": {}, "light": {}, "hidden": {}},
        context=context if context is not None else {"client_type": "discord"},
    )
    return messages, needs_auth


class TestRunPreToolCalls:
    """`run_pre_tool_calls` が呼び出し元の決めたツールを実行して `messages` へ積むこと。"""

    async def test_executes_in_order_and_appends_call_and_result(
        self, tool_loop, mock_loader: MagicMock
    ) -> None:
        """書かれた順に実行し、assistant の tool_calls と tool の結果を 1 組ずつ積む。"""
        mock_loader.execute_tool_call.side_effect = [_ok_result("C"), _ok_result("L")]
        context = {"client_type": "discord"}
        messages, needs_auth = await _run_pre(
            tool_loop,
            [{"tool": "climate"}, {"tool": "light", "args": {"on": True}}],
            messages=[{"role": "user", "content": "hi"}],
            context=context,
        )

        calls = mock_loader.execute_tool_call.await_args_list
        assert [c.args[0] for c in calls] == ["climate", "light"]
        assert calls[0].args[1] == {}
        assert calls[1].args[1] == {"on": True}
        assert calls[0].args[3] is context
        assert needs_auth == []
        assert messages == [
            {"role": "user", "content": "hi"},
            {
                "role": "assistant", "content": None,
                "tool_calls": [{
                    "id": "pre_tool_call_0", "type": "function",
                    "function": {"name": "climate", "arguments": "{}"},
                }],
            },
            {"role": "tool", "tool_call_id": "pre_tool_call_0", "content": "C"},
            {
                "role": "assistant", "content": None,
                "tool_calls": [{
                    "id": "pre_tool_call_1", "type": "function",
                    "function": {"name": "light", "arguments": '{"on": true}'},
                }],
            },
            {"role": "tool", "tool_call_id": "pre_tool_call_1", "content": "L"},
        ]

    @pytest.mark.parametrize(
        ("item", "expected_name", "expected_error"),
        [
            ({"tool": "missing"}, "missing", "この会話で使えません"),
            ({"tool": "hidden"}, "hidden", "この会話で使えません"),
            ({"tool": "climate", "args": "x"}, "climate", "dict ではありません"),
            ({"tool": "light", "args": {}}, "light", "必須の引数がありません: on"),
            ("climate", "invalid_pre_tool_call", "要素が dict ではありません"),
            ({"tool": 1}, "invalid_pre_tool_call", "'tool' が文字列ではありません"),
            ({}, "invalid_pre_tool_call", "'tool' が文字列ではありません"),
        ],
    )
    async def test_invalid_item_becomes_error_result_and_continues(
        self, tool_loop, mock_loader: MagicMock, item, expected_name: str, expected_error: str
    ) -> None:
        """使えないツール・不正な引数・不正な要素は実行せずエラーの結果を積み、後続も実行する。"""
        messages, _ = await _run_pre(tool_loop, [item, {"tool": "climate"}])

        mock_loader.execute_tool_call.assert_awaited_once()
        assert mock_loader.execute_tool_call.await_args.args[0] == "climate"
        assert len(messages) == 4
        assert messages[0]["tool_calls"][0]["function"]["name"] == expected_name
        assert messages[1]["role"] == "tool"
        assert expected_error in messages[1]["content"]
        assert messages[3]["content"] == "RESULT"

    async def test_tool_failure_is_passed_as_result(
        self, tool_loop, mock_loader: MagicMock
    ) -> None:
        """実行中の例外（execute_tool_call の失敗結果）はそのまま tool の結果として積む。"""
        mock_loader.execute_tool_call.return_value = {
            "success": False, "tool_name": "climate", "data": None,
            "error": "ツール実行中にエラーが発生しました: boom",
        }
        messages, _ = await _run_pre(tool_loop, [{"tool": "climate"}])

        assert messages[1]["content"] == "ツール実行中にエラーが発生しました: boom"

    async def test_collects_needs_auth(self, tool_loop, mock_loader: MagicMock) -> None:
        """再認証が必要な結果の `needs_auth_list` を返す。"""
        mock_loader.execute_tool_call.return_value = _auth_result("svc")
        _, needs_auth = await _run_pre(tool_loop, [{"tool": "climate"}])

        assert needs_auth == [{"auth_service": "svc", "auth_url": "https://auth/svc"}]
