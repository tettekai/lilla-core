"""builtin_tools/llm_expert.py のテスト。"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_MODULE_NAME = "lilla_core.builtin_tools.llm_expert"


# ---------------------------------------------------------------------------
# フィクスチャ
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_cfg() -> MagicMock:
    """llm_expert が参照する AppConfig モック。"""
    cfg = MagicMock()
    cfg.llm.max_tool_call_iterations = 3
    cfg.env.config_root = Path("/tmp/test_config_root")
    return cfg


@pytest.fixture
def mock_llm_client() -> MagicMock:
    """lilla_core.api.llm_client モック。"""
    mock = MagicMock()
    mock.chat_to_llm_with_tools = AsyncMock()
    mock.chat_to_llm_responses = AsyncMock()
    return mock


@pytest.fixture
def mock_loader() -> MagicMock:
    """lilla_core.loaders.llm_tool_loader モック。"""
    mock = MagicMock()
    mock.build_tools_param = MagicMock(return_value=[])
    mock.execute_tool_call = AsyncMock()
    # 既定では許可リストをそのまま返す（展開の中身はコア側のテストが担保する）
    mock.resolve_available_tools = MagicMock(
        side_effect=lambda entries, llm_tools: list(entries)
    )
    return mock


@pytest.fixture
def mock_conv_service() -> MagicMock:
    """services.conversation_service モック。"""
    mock = MagicMock()
    mock.build_tool_message_content = MagicMock(
        side_effect=lambda r: str(r.get("data") or r.get("memory_entry") or "")
    )
    return mock


@pytest.fixture
def mock_load_prompt() -> MagicMock:
    """load_text_resources の差し替え用モック。"""
    return MagicMock(return_value="EXPERT SYSTEM PROMPT")


@pytest.fixture
def with_mocked_modules(
    mock_cfg: MagicMock,
    mock_llm_client: MagicMock,
    mock_loader: MagicMock,
    mock_conv_service: MagicMock,
):
    """依存モジュールを patch.dict で差し替える。"""
    with patch.dict(
        sys.modules,
        {
            "lilla_core.core.config": MagicMock(get_config=lambda: mock_cfg),
            "lilla_core.api.llm_client": mock_llm_client,
            "lilla_core.loaders.llm_tool_loader": mock_loader,
            "lilla_core.services.conversation_service": mock_conv_service,
        },
    ):
        yield


@pytest.fixture
def llm_expert(with_mocked_modules, mock_load_prompt: MagicMock):
    """patch.dict 有効後に llm_expert をロードする。"""
    # utils.resource_loader は実モジュールを使い、ロード後に load_text_resources を差し替える
    # （sys.modules を MagicMock で汚すと他テストが影響を受けるため）
    sys.modules.pop(_MODULE_NAME, None)
    module = importlib.import_module(_MODULE_NAME)
    module.load_text_resources = mock_load_prompt
    yield module
    sys.modules.pop(_MODULE_NAME, None)


def _stop_response(text: str = "expert reply") -> dict:
    return {
        "content": text,
        "tool_calls": None,
        "finish_reason": "stop",
        "raw_message": {"role": "assistant", "content": text},
    }


def _tool_call_response(name: str = "llm_health_get", args: str = "{}", call_id: str = "call_1") -> dict:
    return {
        "content": None,
        "finish_reason": "tool_calls",
        "tool_calls": [
            {"id": call_id, "type": "function", "function": {"name": name, "arguments": args}},
        ],
        "raw_message": {"role": "assistant", "content": None, "tool_calls": []},
    }


def _base_context(**overrides) -> dict:
    ctx = {
        "prompt": "${config_root}/prompt/experts/health",
        "llm_provider": "grok",
        "available_tools": ["llm_health_get"],
        "llm_tools": {"llm_health_get": {"schema": {}, "execute": AsyncMock()}},
        "client_type": "discord",
    }
    ctx.update(overrides)
    return ctx


# ---------------------------------------------------------------------------
# TestLLMExpertExecute
# ---------------------------------------------------------------------------


class TestLLMExpertExecute:
    async def test_returns_immediate_text_response(
        self, llm_expert, mock_llm_client: MagicMock
    ) -> None:
        """LLM が即 stop を返した場合、その content が data に入る。"""
        mock_llm_client.chat_to_llm_with_tools.return_value = _stop_response("睡眠は良好です")

        result = await llm_expert.execute({"prompt": "睡眠どう？"}, _base_context())

        assert result["success"] is True
        assert result["data"] == "睡眠は良好です"
        assert result["needs_auth"] is False
        assert result["memory_entry"] is None
        assert result["tool_name"] == "llm_expert"

    async def test_tool_call_then_stop(
        self, llm_expert, mock_llm_client: MagicMock, mock_loader: MagicMock
    ) -> None:
        """tool_calls → stop の通常ループ。"""
        mock_llm_client.chat_to_llm_with_tools.side_effect = [
            _tool_call_response(),
            _stop_response("分析完了"),
        ]
        mock_loader.execute_tool_call.return_value = {
            "success": True,
            "tool_name": "llm_health_get",
            "memory_entry": "ok",
            "needs_auth": False,
            "needs_auth_list": [],
            "data": {"weight": 60},
            "error": None,
        }

        result = await llm_expert.execute({"prompt": "体重教えて"}, _base_context())

        assert result["success"] is True
        assert result["data"] == "分析完了"
        assert mock_loader.execute_tool_call.await_count == 1

    async def test_needs_auth_propagates_and_aborts(
        self, llm_expert, mock_llm_client: MagicMock, mock_loader: MagicMock
    ) -> None:
        """ツールが needs_auth を返したらループ中断して伝播する。"""
        mock_llm_client.chat_to_llm_with_tools.return_value = _tool_call_response()
        mock_loader.execute_tool_call.return_value = {
            "success": False,
            "tool_name": "llm_health_get",
            "memory_entry": None,
            "needs_auth": True,
            "needs_auth_list": [
                {"auth_service": "withings", "auth_url": "https://auth.example/withings"},
            ],
            "data": None,
            "error": "認証が必要です",
        }

        result = await llm_expert.execute({"prompt": "体重"}, _base_context())

        assert result["success"] is False
        assert result["needs_auth"] is True
        assert result["needs_auth_list"] == [
            {"auth_service": "withings", "auth_url": "https://auth.example/withings"}
        ]
        assert result["data"] is None
        # ループは即中断しているので 1 回しか LLM を呼ばない
        assert mock_llm_client.chat_to_llm_with_tools.await_count == 1

    async def test_max_iterations_returns_success_with_abort_text(
        self,
        llm_expert,
        mock_cfg: MagicMock,
        mock_llm_client: MagicMock,
        mock_loader: MagicMock,
    ) -> None:
        """ループ上限到達時はエラーではなく success: True + 中断テキストを返す。"""
        mock_cfg.llm.max_tool_call_iterations = 2
        mock_llm_client.chat_to_llm_with_tools.return_value = _tool_call_response()
        mock_loader.execute_tool_call.return_value = {
            "success": True,
            "tool_name": "llm_health_get",
            "memory_entry": "ok",
            "needs_auth": False,
            "needs_auth_list": [],
            "data": "ok",
            "error": None,
        }

        result = await llm_expert.execute({"prompt": "x"}, _base_context())

        assert result["success"] is True
        assert result["needs_auth"] is False
        assert "中断" in result["data"]
        assert mock_llm_client.chat_to_llm_with_tools.await_count == 2

    async def test_passes_resolved_tools_to_build_tools_param(
        self, llm_expert, mock_llm_client: MagicMock, mock_loader: MagicMock
    ) -> None:
        """available_tools はコアの resolve_available_tools に渡され、その戻り値が allowed_names になる。"""
        mock_llm_client.chat_to_llm_with_tools.return_value = _stop_response()
        mock_loader.resolve_available_tools.side_effect = None
        mock_loader.resolve_available_tools.return_value = ["resolved_a", "resolved_b"]
        ctx = _base_context(available_tools=["llm_health_get", "llm_calendar_get"])

        await llm_expert.execute({"prompt": "x"}, ctx)

        mock_loader.resolve_available_tools.assert_called_once_with(
            ["llm_health_get", "llm_calendar_get"], ctx["llm_tools"]
        )
        mock_loader.build_tools_param.assert_called_once()
        kwargs = mock_loader.build_tools_param.call_args.kwargs
        assert kwargs["allowed_names"] == ["resolved_a", "resolved_b"]
        assert kwargs["client_type"] == "discord"

    async def test_main_token_passed_to_core_as_is(
        self, llm_expert, mock_llm_client: MagicMock, mock_loader: MagicMock
    ) -> None:
        """`$main` は Expert 側で解釈せず、そのままコアへ渡す。"""
        mock_llm_client.chat_to_llm_with_tools.return_value = _stop_response()

        await llm_expert.execute(
            {"prompt": "x"}, _base_context(available_tools=["$main", "llm_health_get"])
        )

        entries = mock_loader.resolve_available_tools.call_args.args[0]
        assert entries == ["$main", "llm_health_get"]

    async def test_omitted_available_tools_resolves_empty_list(
        self, llm_expert, mock_llm_client: MagicMock, mock_loader: MagicMock
    ) -> None:
        """available_tools 省略時は空リストとしてコアへ渡し、ツールなしになる。"""
        mock_llm_client.chat_to_llm_with_tools.return_value = _stop_response()
        ctx = _base_context()
        ctx.pop("available_tools")

        await llm_expert.execute({"prompt": "x"}, ctx)

        assert mock_loader.resolve_available_tools.call_args.args[0] == []
        kwargs = mock_loader.build_tools_param.call_args.kwargs
        assert kwargs["allowed_names"] == []

    async def test_resolve_error_is_not_swallowed(
        self, llm_expert, mock_llm_client: MagicMock, mock_loader: MagicMock
    ) -> None:
        """存在しない stem などでコアが投げた ValueError はそのまま伝播し、LLM は呼ばない。"""
        mock_loader.resolve_available_tools.side_effect = ValueError(
            "Tool allow-list contains unknown tools: ['llm_missing']"
        )

        with pytest.raises(ValueError, match="llm_missing"):
            await llm_expert.execute(
                {"prompt": "x"}, _base_context(available_tools=["llm_missing"])
            )

        mock_loader.build_tools_param.assert_not_called()
        mock_llm_client.chat_to_llm_with_tools.assert_not_called()

    async def test_llm_provider_passed_as_llm_name(
        self, llm_expert, mock_llm_client: MagicMock
    ) -> None:
        """llm_provider が chat_to_llm_with_tools の llm_name に渡る。"""
        mock_llm_client.chat_to_llm_with_tools.return_value = _stop_response()

        await llm_expert.execute({"prompt": "x"}, _base_context(llm_provider="ollama-gemma3"))

        kwargs = mock_llm_client.chat_to_llm_with_tools.call_args.kwargs
        assert kwargs["llm_name"] == "ollama-gemma3"

    async def test_llm_provider_omitted_passes_none(
        self, llm_expert, mock_llm_client: MagicMock
    ) -> None:
        """llm_provider 未指定時は llm_name=None で呼ばれる（デフォルトプロバイダー使用）。"""
        mock_llm_client.chat_to_llm_with_tools.return_value = _stop_response()
        ctx = _base_context()
        ctx.pop("llm_provider")

        await llm_expert.execute({"prompt": "x"}, ctx)

        kwargs = mock_llm_client.chat_to_llm_with_tools.call_args.kwargs
        assert kwargs["llm_name"] is None

    async def test_prompt_source_passed_to_loader(
        self, llm_expert, mock_llm_client: MagicMock, mock_load_prompt: MagicMock
    ) -> None:
        """YAML の prompt source spec がそのまま load_text_resources に渡される。

        `${config_root}` の展開は load_text_resources 内部で行われるため、
        Expert 側は raw な source spec をそのまま渡す。
        """
        mock_llm_client.chat_to_llm_with_tools.return_value = _stop_response()

        await llm_expert.execute(
            {"prompt": "x"},
            _base_context(prompt="dir:${config_root}/prompt/experts/health"),
        )

        called_arg = mock_load_prompt.call_args.args[0]
        assert called_arg == "dir:${config_root}/prompt/experts/health"

    async def test_missing_prompt_returns_error(
        self, llm_expert, mock_llm_client: MagicMock
    ) -> None:
        """prompt 未設定の場合はエラーを返す。"""
        mock_llm_client.chat_to_llm_with_tools.return_value = _stop_response()
        ctx = _base_context()
        ctx.pop("prompt")

        result = await llm_expert.execute({"prompt": "x"}, ctx)

        assert result["success"] is False
        assert result["error"]
        mock_llm_client.chat_to_llm_with_tools.assert_not_called()


@pytest.fixture
def llm_expert_with_real_loader(
    monkeypatch: pytest.MonkeyPatch,
    mock_cfg: MagicMock,
    mock_llm_client: MagicMock,
    mock_conv_service: MagicMock,
    mock_load_prompt: MagicMock,
):
    """本物のコアの llm_tool_loader（resolve_available_tools / build_tools_param）で llm_expert をロードする。

    `$main` の展開が Expert からコアまで通しで効くことを確かめるため、ローダーだけは
    差し替えず、ローダーが読む `get_config()` を monkeypatch で差し替える。
    """
    import lilla_core.loaders.llm_tool_loader as real_loader

    monkeypatch.setattr(real_loader, "get_config", lambda: mock_cfg)
    with patch.dict(
        sys.modules,
        {
            "lilla_core.core.config": MagicMock(get_config=lambda: mock_cfg),
            "lilla_core.api.llm_client": mock_llm_client,
            "lilla_core.loaders.llm_tool_loader": real_loader,
            "lilla_core.services.conversation_service": mock_conv_service,
        },
    ):
        sys.modules.pop(_MODULE_NAME, None)
        module = importlib.import_module(_MODULE_NAME)
        module.load_text_resources = mock_load_prompt
        yield module
        sys.modules.pop(_MODULE_NAME, None)


def _llm_tools(*names: str) -> dict:
    return {
        name: {"schema": {"type": "function", "function": {"name": name}}, "execute": AsyncMock()}
        for name in names
    }


class TestLLMExpertToolsetResolution:
    async def test_main_token_uses_main_conversation_tools(
        self, llm_expert_with_real_loader, mock_cfg: MagicMock, mock_llm_client: MagicMock
    ) -> None:
        """`$main` を書いた Expert は tools.main_available_tools のツールを使える。"""
        mock_cfg.tools.main_available_tools = ["llm_calendar_get", "llm_health_get"]
        mock_llm_client.chat_to_llm_with_tools.return_value = _stop_response()
        ctx = _base_context(
            available_tools=["$main"],
            llm_tools=_llm_tools("llm_calendar_get", "llm_health_get", "llm_obsidian_write"),
        )

        await llm_expert_with_real_loader.execute({"prompt": "x"}, ctx)

        tools = mock_llm_client.chat_to_llm_with_tools.call_args.kwargs["tools"]
        assert [t["function"]["name"] for t in tools] == ["llm_calendar_get", "llm_health_get"]

    async def test_unknown_stem_raises_from_core(
        self, llm_expert_with_real_loader, mock_llm_client: MagicMock
    ) -> None:
        """ロードされていない stem はコアが ValueError で失敗させ、Expert は握りつぶさない。"""
        ctx = _base_context(
            available_tools=["llm_missing"], llm_tools=_llm_tools("llm_health_get")
        )

        with pytest.raises(ValueError, match="llm_missing"):
            await llm_expert_with_real_loader.execute({"prompt": "x"}, ctx)

        mock_llm_client.chat_to_llm_with_tools.assert_not_called()

    async def test_empty_list_means_no_tools(
        self, llm_expert_with_real_loader, mock_llm_client: MagicMock
    ) -> None:
        """空リストはツールなしのまま。"""
        mock_llm_client.chat_to_llm_with_tools.return_value = _stop_response()
        ctx = _base_context(available_tools=[], llm_tools=_llm_tools("llm_health_get"))

        await llm_expert_with_real_loader.execute({"prompt": "x"}, ctx)

        assert mock_llm_client.chat_to_llm_with_tools.call_args.kwargs["tools"] == []


def _responses_result(text: str = "X検索の結果です") -> dict:
    return {
        "content": text,
        "tool_calls": None,
        "finish_reason": "stop",
        "raw_output": [],
    }


class TestLLMExpertApiBranching:
    async def test_pattern1_default_api_uses_chat_completions(
        self, llm_expert, mock_llm_client: MagicMock
    ) -> None:
        """(1) api 未指定（デフォルト）+ available_tools は既存動作のまま。"""
        mock_llm_client.chat_to_llm_with_tools.return_value = _stop_response("既存動作")

        result = await llm_expert.execute({"prompt": "x"}, _base_context())

        assert result["success"] is True
        assert result["data"] == "既存動作"
        mock_llm_client.chat_to_llm_with_tools.assert_awaited()
        mock_llm_client.chat_to_llm_responses.assert_not_called()

    async def test_pattern2_chat_completions_with_grok_tools_raises(
        self, llm_expert, mock_llm_client: MagicMock
    ) -> None:
        """(2) api=chat_completions（省略時含む）+ grok_tools は組み合わせ不可エラー。"""
        ctx = _base_context(grok_tools=[{"type": "x_search"}])

        with pytest.raises(ValueError) as exc:
            await llm_expert.execute({"prompt": "x"}, ctx)

        assert "grok_tools" in str(exc.value)
        assert "responses" in str(exc.value)
        mock_llm_client.chat_to_llm_responses.assert_not_called()

    async def test_pattern3_responses_with_available_tools_raises(
        self, llm_expert, mock_llm_client: MagicMock
    ) -> None:
        """(3) api=responses + available_tools は未対応エラー。"""
        ctx = _base_context(api="responses", grok_tools=[{"type": "x_search"}])

        with pytest.raises(ValueError) as exc:
            await llm_expert.execute({"prompt": "x"}, ctx)

        assert "available_tools" in str(exc.value)
        mock_llm_client.chat_to_llm_responses.assert_not_called()

    async def test_pattern4_responses_with_grok_tools(
        self, llm_expert, mock_llm_client: MagicMock
    ) -> None:
        """(4) api=responses + grok_tools は chat_to_llm_responses を1回呼ぶ。"""
        mock_llm_client.chat_to_llm_responses.return_value = _responses_result("X検索結果")
        ctx = _base_context(api="responses", grok_tools=[{"type": "x_search"}])
        ctx.pop("available_tools")

        result = await llm_expert.execute({"prompt": "最新の反応は？"}, ctx)

        assert result["success"] is True
        assert result["data"] == "X検索結果"
        assert result["memory_entry"] is None
        assert result["needs_auth"] is False
        assert result["needs_auth_list"] == []
        assert result["error"] is None
        assert result["tool_name"] == "llm_expert"
        mock_llm_client.chat_to_llm_responses.assert_awaited_once()
        kwargs = mock_llm_client.chat_to_llm_responses.call_args.kwargs
        assert kwargs["tools"] == [{"type": "x_search"}]

    async def test_pattern4_responses_without_grok_tools(
        self, llm_expert, mock_llm_client: MagicMock
    ) -> None:
        """(4) api=responses + grok_tools なしでも単発呼び出しが許容される。"""
        mock_llm_client.chat_to_llm_responses.return_value = _responses_result()
        ctx = _base_context(api="responses")
        ctx.pop("available_tools")

        result = await llm_expert.execute({"prompt": "x"}, ctx)

        assert result["success"] is True
        mock_llm_client.chat_to_llm_responses.assert_awaited_once()
        kwargs = mock_llm_client.chat_to_llm_responses.call_args.kwargs
        assert kwargs["tools"] is None

    async def test_pattern4_empty_content_returns_empty_string(
        self, llm_expert, mock_llm_client: MagicMock
    ) -> None:
        """(4) content が None でも data は空文字列になる。"""
        mock_llm_client.chat_to_llm_responses.return_value = {
            "content": None,
            "tool_calls": None,
            "finish_reason": "stop",
            "raw_output": [],
        }
        ctx = _base_context(api="responses")
        ctx.pop("available_tools")

        result = await llm_expert.execute({"prompt": "x"}, ctx)

        assert result["success"] is True
        assert result["data"] == ""

    async def test_unknown_api_raises(self, llm_expert) -> None:
        """不明な api 指定はエラー。"""
        ctx = _base_context(api="grpc")
        ctx.pop("available_tools")

        with pytest.raises(ValueError) as exc:
            await llm_expert.execute({"prompt": "x"}, ctx)

        assert "Unknown api" in str(exc.value)


class TestLLMExpertLoading:
    def test_loads_via_file_name(
        self, mock_cfg: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """YAML の `type: llm_expert`（ファイル名）で builtin の実装がロードされ、YAML の名前で登録される。"""
        with patch.dict(
            sys.modules,
            {"lilla_core.core.config": MagicMock(get_config=lambda: mock_cfg)},
        ):
            sys.modules.pop("lilla_core.loaders.llm_tool_loader", None)
            import lilla_core.loaders.llm_tool_loader as loader
            from lilla_core.loaders import tool_paths

            monkeypatch.setattr(tool_paths, "get_config", lambda: mock_cfg)
            tools_dir = tmp_path / "config_root" / "tools"
            tools_dir.mkdir(parents=True)
            (tools_dir / "llm_health_expert.yaml").write_text(
                "type: llm_expert\ndescription: 健康の専門家\n", encoding="utf-8"
            )
            mock_cfg.paths.tool_root = tmp_path / "no_such_root"

            result = loader.load_llm_tools(config_root=tmp_path / "config_root")
            sys.modules.pop("lilla_core.loaders.llm_tool_loader", None)

        assert "llm_health_expert" in result
        assert callable(result["llm_health_expert"]["execute"])
        function = result["llm_health_expert"]["schema"]["function"]
        assert function["name"] == "llm_health_expert"
        assert function["description"] == "健康の専門家"
