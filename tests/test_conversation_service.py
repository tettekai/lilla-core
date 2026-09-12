"""conversation_service.py のテスト。"""
from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# フィクスチャ
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_cfg() -> MagicMock:
    """conversation_service が参照する AppConfig モック。"""
    cfg = MagicMock()
    cfg.llm.max_tool_call_iterations = 3
    cfg.media_reset_minutes = 5
    cfg.media_default = ""
    cfg.media_base_url = ""
    cfg.env.mongodb_uri = "mongodb://localhost:27017"
    cfg.mongodb.db_name = "test_db"
    cfg.memory.session_memory_ttl_hours = 3
    # datetime_utils.local_timezone() が OS のローカルタイムゾーンへ解決するようにする
    cfg.ui.timezone = None
    return cfg


@pytest.fixture
def mock_llm_module() -> MagicMock:
    """lilla_core.api.llm_client モック。"""
    mock = MagicMock()
    mock.chat_to_llm = AsyncMock(return_value="default reply")
    mock.chat_to_llm_with_tools = AsyncMock(return_value={
        "content": "tool loop reply",
        "tool_calls": None,
        "finish_reason": "stop",
        "raw_message": {"role": "assistant", "content": "tool loop reply"},
    })
    return mock


@pytest.fixture
def mock_memory_manager_instance() -> MagicMock:
    """MemoryManager インスタンスのモック。"""
    instance = MagicMock()
    instance.build_system_prompt = AsyncMock(return_value="test system prompt")
    instance.load_conversation_history_with_timestamps = AsyncMock(return_value=[])
    instance.add_conversation = AsyncMock()
    return instance


@pytest.fixture
def mock_memory_manager_module(mock_memory_manager_instance: MagicMock) -> MagicMock:
    """services.memory_manager モック。"""
    module = MagicMock()
    module.get_memory_manager = MagicMock(return_value=mock_memory_manager_instance)
    return module


@pytest.fixture
def mock_llm_tool_loader() -> MagicMock:
    """lilla_core.loaders.llm_tool_loader モック。"""
    mock = MagicMock()
    mock.build_tools_param = lambda x, client_type="discord", allowed_names=None: list(x.values())
    mock.execute_tool_call = AsyncMock(return_value={
        "success": True, "tool_name": "test_tool", "memory_entry": "tool result", "data": None, "error": None
    })
    return mock


@pytest.fixture
def with_mocked_modules(
    mock_cfg: MagicMock,
    mock_llm_module: MagicMock,
    mock_memory_manager_module: MagicMock,
    mock_llm_tool_loader: MagicMock,
):
    """依存モジュールを patch.dict で差し替える。"""
    with patch.dict(
        sys.modules,
        {
            "lilla_core.core.config": MagicMock(get_config=lambda: mock_cfg),
            "lilla_core.api.llm_client": mock_llm_module,
            "lilla_core.services.memory_manager": mock_memory_manager_module,
            "lilla_core.loaders.llm_tool_loader": mock_llm_tool_loader,
        },
    ):
        yield


@pytest.fixture
def conversation_service(with_mocked_modules, mock_cfg: MagicMock):
    """patch.dict 有効後に conversation_service をロードする。"""
    import importlib
    sys.modules.pop("lilla_core.services.conversation_service", None)
    cs = importlib.import_module("lilla_core.services.conversation_service")
    cs._config = mock_cfg
    yield cs
    sys.modules.pop("lilla_core.services.conversation_service", None)


# ---------------------------------------------------------------------------
# TestRunConversation
# ---------------------------------------------------------------------------


class TestRunConversation:
    async def test_fallback_to_chat_to_llm_when_no_tools(
        self, conversation_service, mock_memory_manager_instance, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """llm_tools が空のとき chat_to_llm にフォールバックする。"""
        mock_chat = AsyncMock(return_value="fallback reply")
        monkeypatch.setattr(conversation_service, "chat_to_llm", mock_chat)

        result = await conversation_service.run_conversation({})

        assert result == "fallback reply"
        mock_chat.assert_called_once()

    async def test_does_not_save_or_duplicate_user_message(
        self, conversation_service, mock_memory_manager_instance, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """run_conversation は add_conversation を呼ばず、履歴を二重に追加しない。"""
        mock_chat = AsyncMock(return_value="ok")
        monkeypatch.setattr(conversation_service, "chat_to_llm", mock_chat)
        mock_memory_manager_instance.load_conversation_history_with_timestamps.return_value = [
            {"role": "user", "content": "[May 04 09:10] こんにちは"},
        ]

        await conversation_service.run_conversation({})

        mock_memory_manager_instance.add_conversation.assert_not_called()
        sent_history = mock_chat.call_args[0][0]
        assert sent_history == [{"role": "user", "content": "[May 04 09:10] こんにちは"}]

    async def test_inject_user_content_appends_with_timestamp(
        self, conversation_service, mock_memory_manager_instance, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """inject_user_content は履歴末尾にタイムスタンプ付きで追加される（task 用）。"""
        mock_chat = AsyncMock(return_value="ok")
        monkeypatch.setattr(conversation_service, "chat_to_llm", mock_chat)
        mock_memory_manager_instance.load_conversation_history_with_timestamps.return_value = []

        await conversation_service.run_conversation({}, inject_user_content="heartbeat prompt")

        sent_history = mock_chat.call_args[0][0]
        assert len(sent_history) == 1
        assert sent_history[0]["role"] == "user"
        assert sent_history[0]["content"].endswith("heartbeat prompt")
        assert sent_history[0]["content"].startswith("[")

    async def test_override_last_user_content_replaces_last_user_message(
        self, conversation_service, mock_memory_manager_instance, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """override_last_user_content は履歴末尾の user メッセージを差し替える（画像添付用）。"""
        mock_chat = AsyncMock(return_value="ok")
        monkeypatch.setattr(conversation_service, "chat_to_llm", mock_chat)
        mock_memory_manager_instance.load_conversation_history_with_timestamps.return_value = [
            {"role": "user", "content": "[May 04 09:10] 見て [画像 1 枚添付]"},
        ]
        rich = [
            {"type": "text", "text": "見て"},
            {"type": "image_url", "image_url": {"url": "x"}},
        ]

        await conversation_service.run_conversation({}, override_last_user_content=rich)

        sent_history = mock_chat.call_args[0][0]
        assert len(sent_history) == 1
        assert isinstance(sent_history[0]["content"], list)
        assert sent_history[0]["content"][0]["type"] == "text"
        assert sent_history[0]["content"][0]["text"].endswith("見て")
        assert sent_history[0]["content"][0]["text"].startswith("[")
        assert sent_history[0]["content"][1]["type"] == "image_url"

    async def test_override_last_user_content_noop_when_no_user_message(
        self, conversation_service, mock_memory_manager_instance, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """履歴末尾が user でないとき override_last_user_content は何もしない。"""
        mock_chat = AsyncMock(return_value="ok")
        monkeypatch.setattr(conversation_service, "chat_to_llm", mock_chat)
        mock_memory_manager_instance.load_conversation_history_with_timestamps.return_value = [
            {"role": "assistant", "content": "[May 04 09:10] hi"},
        ]

        await conversation_service.run_conversation({}, override_last_user_content="rich")

        sent_history = mock_chat.call_args[0][0]
        assert sent_history == [{"role": "assistant", "content": "[May 04 09:10] hi"}]

    async def test_tool_call_then_text_reply(
        self, conversation_service, mock_memory_manager_instance, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """tool_calls → stop の 2 ステップで最終テキストが返る。"""
        tool_response = {
            "content": None,
            "finish_reason": "tool_calls",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "test_tool", "arguments": '{"key": "val"}'},
                }
            ],
            "raw_message": {"role": "assistant", "content": None, "tool_calls": []},
        }
        text_response = {
            "content": "完了しました",
            "finish_reason": "stop",
            "tool_calls": None,
            "raw_message": {"role": "assistant", "content": "完了しました"},
        }
        mock_chat = AsyncMock(side_effect=[tool_response, text_response])
        monkeypatch.setattr(conversation_service, "chat_to_llm_with_tools", mock_chat)
        monkeypatch.setattr(conversation_service, "execute_tool_call", AsyncMock(return_value={
            "success": True, "tool_name": "test_tool", "memory_entry": "ok", "data": None, "error": None
        }))

        fake_tools = {"test_tool": {"schema": {}, "execute": AsyncMock()}}
        result = await conversation_service.run_conversation(fake_tools)

        assert result == "完了しました"

    async def test_tool_calls_dispatched_even_when_finish_reason_is_stop(
        self, conversation_service, mock_memory_manager_instance, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """finish_reason が "stop" でも tool_calls があればツールを実行する（互換プロバイダ対策）。"""
        tool_response = {
            "content": None,
            "finish_reason": "stop",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "test_tool", "arguments": "{}"},
                }
            ],
            "raw_message": {"role": "assistant", "content": None, "tool_calls": []},
        }
        text_response = {
            "content": "完了しました",
            "finish_reason": "stop",
            "tool_calls": None,
            "raw_message": {"role": "assistant", "content": "完了しました"},
        }
        mock_chat = AsyncMock(side_effect=[tool_response, text_response])
        monkeypatch.setattr(conversation_service, "chat_to_llm_with_tools", mock_chat)
        mock_execute_tool_call = AsyncMock(return_value={
            "success": True, "tool_name": "test_tool", "memory_entry": "ok", "data": None, "error": None
        })
        monkeypatch.setattr(conversation_service, "execute_tool_call", mock_execute_tool_call)

        fake_tools = {"test_tool": {"schema": {}, "execute": AsyncMock()}}
        result = await conversation_service.run_conversation(fake_tools)

        mock_execute_tool_call.assert_awaited_once()
        assert result == "完了しました"

    async def test_malformed_tool_arguments_json_returns_tool_error_without_crashing(
        self, conversation_service, mock_memory_manager_instance, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """引数の JSON が壊れていてもツールエラーとして LLM に返し、会話を落とさない。"""
        tool_response = {
            "content": None,
            "finish_reason": "tool_calls",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "test_tool", "arguments": "{invalid json"},
                }
            ],
            "raw_message": {"role": "assistant", "content": None, "tool_calls": []},
        }
        text_response = {
            "content": "エラーを確認しました",
            "finish_reason": "stop",
            "tool_calls": None,
            "raw_message": {"role": "assistant", "content": "エラーを確認しました"},
        }
        mock_chat = AsyncMock(side_effect=[tool_response, text_response])
        monkeypatch.setattr(conversation_service, "chat_to_llm_with_tools", mock_chat)
        mock_execute_tool_call = AsyncMock()
        monkeypatch.setattr(conversation_service, "execute_tool_call", mock_execute_tool_call)

        fake_tools = {"test_tool": {"schema": {}, "execute": AsyncMock()}}
        result = await conversation_service.run_conversation(fake_tools)

        assert result == "エラーを確認しました"
        mock_execute_tool_call.assert_not_awaited()
        second_call_messages = mock_chat.call_args_list[1][0][0]
        tool_msg = next(m for m in second_call_messages if m.get("role") == "tool")
        assert "JSON" in tool_msg["content"]

    async def test_tool_call_notifier_injected_into_context(
        self, conversation_service, mock_memory_manager_instance, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """tool_call_notifier を渡すと execute_tool_call の context に注入される。"""
        tool_response = {
            "content": None,
            "finish_reason": "tool_calls",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "test_tool", "arguments": "{}"},
                }
            ],
            "raw_message": {"role": "assistant", "content": None, "tool_calls": []},
        }
        text_response = {
            "content": "完了しました",
            "finish_reason": "stop",
            "tool_calls": None,
            "raw_message": {"role": "assistant", "content": "完了しました"},
        }
        mock_chat = AsyncMock(side_effect=[tool_response, text_response])
        monkeypatch.setattr(conversation_service, "chat_to_llm_with_tools", mock_chat)
        mock_execute_tool_call = AsyncMock(return_value={
            "success": True, "tool_name": "test_tool", "memory_entry": "ok", "data": None, "error": None
        })
        monkeypatch.setattr(conversation_service, "execute_tool_call", mock_execute_tool_call)

        async def notifier(line: str) -> None:
            pass

        fake_tools = {"test_tool": {"schema": {}, "execute": AsyncMock()}}
        await conversation_service.run_conversation(
            fake_tools, client_type="discord", tool_call_notifier=notifier
        )

        called_context = mock_execute_tool_call.call_args[0][3]
        assert called_context["_tool_call_notifier"] is notifier

    async def test_no_notifier_key_when_not_provided(
        self, conversation_service, mock_memory_manager_instance, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """tool_call_notifier を渡さなければ context にキーが存在しない。"""
        tool_response = {
            "content": None,
            "finish_reason": "tool_calls",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "test_tool", "arguments": "{}"},
                }
            ],
            "raw_message": {"role": "assistant", "content": None, "tool_calls": []},
        }
        text_response = {
            "content": "完了しました",
            "finish_reason": "stop",
            "tool_calls": None,
            "raw_message": {"role": "assistant", "content": "完了しました"},
        }
        mock_chat = AsyncMock(side_effect=[tool_response, text_response])
        monkeypatch.setattr(conversation_service, "chat_to_llm_with_tools", mock_chat)
        mock_execute_tool_call = AsyncMock(return_value={
            "success": True, "tool_name": "test_tool", "memory_entry": "ok", "data": None, "error": None
        })
        monkeypatch.setattr(conversation_service, "execute_tool_call", mock_execute_tool_call)

        fake_tools = {"test_tool": {"schema": {}, "execute": AsyncMock()}}
        await conversation_service.run_conversation(fake_tools, client_type="lilla-client")

        called_context = mock_execute_tool_call.call_args[0][3]
        assert "_tool_call_notifier" not in called_context

    async def test_max_iterations_returns_abort_message(
        self, conversation_service, mock_memory_manager_instance, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """tool_calls が max_iterations 回続いたとき上限メッセージを返す。"""
        tool_response = {
            "content": None,
            "finish_reason": "tool_calls",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "test_tool", "arguments": "{}"},
                }
            ],
            "raw_message": {"role": "assistant", "content": None, "tool_calls": []},
        }
        mock_chat = AsyncMock(return_value=tool_response)
        monkeypatch.setattr(conversation_service, "chat_to_llm_with_tools", mock_chat)
        monkeypatch.setattr(conversation_service, "execute_tool_call", AsyncMock(return_value={
            "success": True, "tool_name": "test_tool", "memory_entry": "ok", "data": None, "error": None
        }))

        fake_tools = {"test_tool": {"schema": {}, "execute": AsyncMock()}}
        result = await conversation_service.run_conversation(fake_tools)

        assert result == "ツール呼び出しの上限に達しました。処理を中断しました。"
        assert mock_chat.call_count == 3

    async def test_tool_data_with_datetime_is_serialized(
        self, conversation_service, mock_memory_manager_instance, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """tool の data に datetime が含まれていても JSON シリアライズに成功する。"""
        from datetime import datetime, timezone, timedelta
        import json

        dt = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)

        tool_response = {
            "content": None,
            "finish_reason": "tool_calls",
            "tool_calls": [
                {
                    "id": "call_dt",
                    "type": "function",
                    "function": {"name": "test_tool", "arguments": "{}"},
                }
            ],
            "raw_message": {"role": "assistant", "content": None, "tool_calls": []},
        }
        text_response = {
            "content": "完了",
            "finish_reason": "stop",
            "tool_calls": None,
            "raw_message": {"role": "assistant", "content": "完了"},
        }
        mock_chat = AsyncMock(side_effect=[tool_response, text_response])
        monkeypatch.setattr(conversation_service, "chat_to_llm_with_tools", mock_chat)
        monkeypatch.setattr(conversation_service, "execute_tool_call", AsyncMock(return_value={
            "success": True, "tool_name": "test_tool", "memory_entry": None,
            "data": {"timestamp": dt, "value": 42}, "error": None,
        }))

        fake_tools = {"test_tool": {"schema": {}, "execute": AsyncMock()}}
        result = await conversation_service.run_conversation(fake_tools)

        assert result == "完了"
        # tool メッセージの content が正常に JSON 文字列になっていること
        second_call_messages = mock_chat.call_args_list[1][0][0]
        tool_msg = next(m for m in second_call_messages if m.get("role") == "tool")
        parsed = json.loads(tool_msg["content"])
        # ISO 8601 文字列として復元でき、UTC で同じ時刻を指すことを確認
        parsed_dt = datetime.fromisoformat(parsed["timestamp"])
        assert parsed_dt.astimezone(timezone.utc) == dt
        assert parsed["value"] == 42

    async def test_tool_result_not_saved_to_conversation(
        self, conversation_service, mock_memory_manager_instance, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """tool_call 中間メッセージは会話履歴に保存されず、最終テキストのみ保存される。"""
        tool_response = {
            "content": None,
            "finish_reason": "tool_calls",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "test_tool", "arguments": "{}"},
                }
            ],
            "raw_message": {"role": "assistant", "content": None, "tool_calls": []},
        }
        text_response = {
            "content": "完了！",
            "finish_reason": "stop",
            "tool_calls": None,
            "raw_message": {"role": "assistant", "content": "完了！"},
        }
        mock_chat = AsyncMock(side_effect=[tool_response, text_response])
        monkeypatch.setattr(conversation_service, "chat_to_llm_with_tools", mock_chat)
        monkeypatch.setattr(conversation_service, "execute_tool_call", AsyncMock(return_value={
            "success": True, "tool_name": "test_tool", "memory_entry": "ok", "data": None, "error": None
        }))

        fake_tools = {"test_tool": {"schema": {}, "execute": AsyncMock()}}
        await conversation_service.run_conversation(fake_tools)

        mock_memory_manager_instance.add_conversation.assert_not_called()


# ---------------------------------------------------------------------------
# TestMetaActionsIntegration
# ---------------------------------------------------------------------------


class TestMetaActionsIntegration:
    def _patch_session_memory(self, monkeypatch: pytest.MonkeyPatch, conversation_service) -> MagicMock:
        """get_session_memory_manager をモックに差し替え、そのインスタンスを返す。"""
        fake = MagicMock()
        fake.set = MagicMock()
        fake.clear = MagicMock()
        monkeypatch.setattr(conversation_service, "get_session_memory_manager", lambda: fake)
        return fake

    @staticmethod
    def _meta_block(body: str) -> str:
        """META ブロック文字列を組み立てる。"""
        return f"---META---\n{body}\n---END_META---"

    async def test_tool_loop_path_strips_block_and_saves(
        self, conversation_service, mock_memory_manager_instance, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """tool_calls ループ終了時、set_session_memory が反映され本文から除去される。"""
        fake_session_memory = self._patch_session_memory(monkeypatch, conversation_service)
        tool_response = {
            "content": None,
            "finish_reason": "tool_calls",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "test_tool", "arguments": "{}"},
                }
            ],
            "raw_message": {"role": "assistant", "content": None, "tool_calls": []},
        }
        meta = self._meta_block(
            '{"actions": [{"type": "set_session_memory", "value": "作業中: XXX"}]}'
        )
        text_response = {
            "content": f"完了しました。\n\n{meta}",
            "finish_reason": "stop",
            "tool_calls": None,
            "raw_message": {"role": "assistant", "content": "完了しました。"},
        }
        mock_chat = AsyncMock(side_effect=[tool_response, text_response])
        monkeypatch.setattr(conversation_service, "chat_to_llm_with_tools", mock_chat)
        monkeypatch.setattr(conversation_service, "execute_tool_call", AsyncMock(return_value={
            "success": True, "tool_name": "test_tool", "memory_entry": "ok", "data": None, "error": None
        }))

        fake_tools = {"test_tool": {"schema": {}, "execute": AsyncMock()}}
        result = await conversation_service.run_conversation(fake_tools)

        assert "META" not in result
        assert result == "完了しました。"
        fake_session_memory.set.assert_called_once_with("作業中: XXX")
        fake_session_memory.clear.assert_not_called()

    async def test_no_tools_path_strips_block_and_saves(
        self, conversation_service, mock_memory_manager_instance, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ツール未使用フォールバック経路でも set_session_memory が反映される。"""
        fake_session_memory = self._patch_session_memory(monkeypatch, conversation_service)
        meta = self._meta_block(
            '{"actions": [{"type": "set_session_memory", "value": "作業中: YYY"}]}'
        )
        mock_chat = AsyncMock(return_value=f"了解です。\n\n{meta}")
        monkeypatch.setattr(conversation_service, "chat_to_llm", mock_chat)

        result = await conversation_service.run_conversation({})

        assert "META" not in result
        assert result == "了解です。"
        fake_session_memory.set.assert_called_once_with("作業中: YYY")

    async def test_empty_value_clears_session_memory(
        self, conversation_service, mock_memory_manager_instance, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """value が空文字の場合 clear が呼ばれ、set は呼ばれない。"""
        fake_session_memory = self._patch_session_memory(monkeypatch, conversation_service)
        meta = self._meta_block('{"actions": [{"type": "set_session_memory", "value": "  "}]}')
        mock_chat = AsyncMock(return_value=f"完了。\n\n{meta}")
        monkeypatch.setattr(conversation_service, "chat_to_llm", mock_chat)

        result = await conversation_service.run_conversation({})

        assert result == "完了。"
        fake_session_memory.clear.assert_called_once()
        fake_session_memory.set.assert_not_called()

    async def test_missing_value_clears_session_memory(
        self, conversation_service, mock_memory_manager_instance, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """value キーが無い場合も clear 扱いになる。"""
        fake_session_memory = self._patch_session_memory(monkeypatch, conversation_service)
        meta = self._meta_block('{"actions": [{"type": "set_session_memory"}]}')
        mock_chat = AsyncMock(return_value=f"完了。\n\n{meta}")
        monkeypatch.setattr(conversation_service, "chat_to_llm", mock_chat)

        await conversation_service.run_conversation({})

        fake_session_memory.clear.assert_called_once()
        fake_session_memory.set.assert_not_called()

    async def test_no_meta_block_leaves_session_memory_untouched(
        self, conversation_service, mock_memory_manager_instance, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """META ブロックがない場合 set / clear のどちらも呼ばれず、返信も変化しない。"""
        fake_session_memory = self._patch_session_memory(monkeypatch, conversation_service)
        mock_chat = AsyncMock(return_value="ただの返信です。")
        monkeypatch.setattr(conversation_service, "chat_to_llm", mock_chat)

        result = await conversation_service.run_conversation({})

        assert result == "ただの返信です。"
        fake_session_memory.set.assert_not_called()
        fake_session_memory.clear.assert_not_called()

    async def test_meta_without_set_session_memory_action_leaves_memory_untouched(
        self, conversation_service, mock_memory_manager_instance, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """set_session_memory 以外のアクションのみの場合、セッションメモリは維持される。"""
        fake_session_memory = self._patch_session_memory(monkeypatch, conversation_service)
        meta = self._meta_block('{"actions": [{"type": "some_future_action", "value": "X"}]}')
        mock_chat = AsyncMock(return_value=f"返信です。\n\n{meta}")
        monkeypatch.setattr(conversation_service, "chat_to_llm", mock_chat)

        result = await conversation_service.run_conversation({})

        assert result == "返信です。"
        fake_session_memory.set.assert_not_called()
        fake_session_memory.clear.assert_not_called()

    async def test_other_action_coexists_with_set_session_memory(
        self, conversation_service, mock_memory_manager_instance, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """未知のアクションが混在していても set_session_memory は適用される。"""
        fake_session_memory = self._patch_session_memory(monkeypatch, conversation_service)
        meta = self._meta_block(
            '{"actions": ['
            '{"type": "some_future_action", "foo": 1}, '
            '{"type": "set_session_memory", "value": "健康ノート整理中"}'
            "]}"
        )
        mock_chat = AsyncMock(return_value=f"返信です。\n\n{meta}")
        monkeypatch.setattr(conversation_service, "chat_to_llm", mock_chat)

        result = await conversation_service.run_conversation({})

        assert result == "返信です。"
        fake_session_memory.set.assert_called_once_with("健康ノート整理中")

    async def test_legacy_session_memory_block_is_not_parsed(
        self, conversation_service, mock_memory_manager_instance, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """旧 ---SESSION_MEMORY--- ブロックはもう解釈されず、本文もそのまま残る。"""
        fake_session_memory = self._patch_session_memory(monkeypatch, conversation_service)
        raw = "返信です。\n\n---SESSION_MEMORY---\n作業中: XXX\n---END_SESSION_MEMORY---"
        mock_chat = AsyncMock(return_value=raw)
        monkeypatch.setattr(conversation_service, "chat_to_llm", mock_chat)

        result = await conversation_service.run_conversation({})

        assert result == raw
        fake_session_memory.set.assert_not_called()
        fake_session_memory.clear.assert_not_called()

    async def test_malformed_meta_block_leaves_session_memory_untouched(
        self, conversation_service, mock_memory_manager_instance, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """マーカーが壊れている場合、パース失敗として現状維持し、マーカーも保持される。"""
        fake_session_memory = self._patch_session_memory(monkeypatch, conversation_service)
        raw = '返信です。\n\n---META---\n{"actions": []}'
        mock_chat = AsyncMock(return_value=raw)
        monkeypatch.setattr(conversation_service, "chat_to_llm", mock_chat)

        result = await conversation_service.run_conversation({})

        assert result == raw
        fake_session_memory.set.assert_not_called()
        fake_session_memory.clear.assert_not_called()

    async def test_invalid_json_block_is_stripped_and_memory_untouched(
        self, conversation_service, mock_memory_manager_instance, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """JSON が壊れている場合はブロックを除去しつつセッションメモリは維持する。"""
        fake_session_memory = self._patch_session_memory(monkeypatch, conversation_service)
        meta = self._meta_block("これはJSONではない")
        mock_chat = AsyncMock(return_value=f"返信です。\n\n{meta}")
        monkeypatch.setattr(conversation_service, "chat_to_llm", mock_chat)

        result = await conversation_service.run_conversation({})

        assert result == "返信です。"
        fake_session_memory.set.assert_not_called()
        fake_session_memory.clear.assert_not_called()

    async def test_block_stripped_before_reauth_notice_concat(
        self, conversation_service, mock_memory_manager_instance, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """再認証通知がある場合も、META ブロックは通知連結前に除去される。"""
        fake_session_memory = self._patch_session_memory(monkeypatch, conversation_service)
        tool_response = {
            "content": None,
            "finish_reason": "tool_calls",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "test_tool", "arguments": "{}"},
                }
            ],
            "raw_message": {"role": "assistant", "content": None, "tool_calls": []},
        }
        meta = self._meta_block(
            '{"actions": [{"type": "set_session_memory", "value": "作業中: ZZZ"}]}'
        )
        text_response = {
            "content": f"完了。\n\n{meta}",
            "finish_reason": "stop",
            "tool_calls": None,
            "raw_message": {"role": "assistant", "content": "完了。"},
        }
        mock_chat = AsyncMock(side_effect=[tool_response, text_response])
        monkeypatch.setattr(conversation_service, "chat_to_llm_with_tools", mock_chat)
        monkeypatch.setattr(conversation_service, "execute_tool_call", AsyncMock(return_value={
            "success": True,
            "tool_name": "test_tool",
            "memory_entry": None,
            "data": None,
            "error": None,
            "needs_auth": True,
            "needs_auth_list": [
                {"auth_service": "Google", "auth_url": "https://example.com/auth"},
            ],
        }))

        fake_tools = {"test_tool": {"schema": {}, "execute": AsyncMock()}}
        result = await conversation_service.run_conversation(fake_tools)

        assert "META" not in result
        assert result.startswith("完了。")
        assert "再認証が必要です" in result
        fake_session_memory.set.assert_called_once_with("作業中: ZZZ")

    async def test_max_iterations_path_does_not_touch_session_memory(
        self, conversation_service, mock_memory_manager_instance, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """最大イテレーション到達時の固定メッセージは META 処理の対象外。"""
        fake_session_memory = self._patch_session_memory(monkeypatch, conversation_service)
        tool_response = {
            "content": None,
            "finish_reason": "tool_calls",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "test_tool", "arguments": "{}"},
                }
            ],
            "raw_message": {"role": "assistant", "content": None, "tool_calls": []},
        }
        mock_chat = AsyncMock(return_value=tool_response)
        monkeypatch.setattr(conversation_service, "chat_to_llm_with_tools", mock_chat)
        monkeypatch.setattr(conversation_service, "execute_tool_call", AsyncMock(return_value={
            "success": True, "tool_name": "test_tool", "memory_entry": "ok", "data": None, "error": None
        }))

        fake_tools = {"test_tool": {"schema": {}, "execute": AsyncMock()}}
        result = await conversation_service.run_conversation(fake_tools)

        assert result == "ツール呼び出しの上限に達しました。処理を中断しました。"
        fake_session_memory.set.assert_not_called()
        fake_session_memory.clear.assert_not_called()


# ---------------------------------------------------------------------------
# TestBuildToolContext
# ---------------------------------------------------------------------------


class TestBuildToolContext:
    """`build_tool_context()` が登録済みプロバイダを展開することのテスト。"""

    @pytest.fixture(autouse=True)
    def providers(self, conversation_service, make_extension, use_extensions):
        """ツール実行 context プロバイダを拡張として差し込むヘルパーを返す。"""
        def _register(**providers):
            use_extensions(make_extension("context-pack", tool_context_providers=providers))

        use_extensions()
        return _register

    def test_registered_providers_are_expanded_into_context(
        self, conversation_service, providers, mock_cfg: MagicMock
    ) -> None:
        """登録されたプロバイダの戻り値がそのキー名で context に入る。"""
        obsidian = MagicMock()
        bitbucket = MagicMock()
        providers(obsidian_client=lambda: obsidian, bitbucket_client=lambda: bitbucket)

        context = conversation_service.build_tool_context()

        assert context == {
            "obsidian_client": obsidian,
            "bitbucket_client": bitbucket,
        }

    def test_failing_provider_only_drops_its_own_key(
        self, conversation_service, providers, mock_cfg: MagicMock
    ) -> None:
        """プロバイダが例外を送出しても、そのキーが欠けるだけで他は残る。"""

        def _raise():
            raise Exception("not configured")

        healthy = MagicMock()
        providers(obsidian_client=_raise)
        providers(bitbucket_client=lambda: healthy)

        context = conversation_service.build_tool_context()

        assert "obsidian_client" not in context
        assert context["bitbucket_client"] is healthy

    def test_no_providers_returns_empty_context(
        self, conversation_service, providers, mock_cfg: MagicMock
    ) -> None:
        """プロバイダ未登録（コア単体起動相当）でも例外を出さず context は空になる。"""
        mock_cfg.media_base_url = "http://lilla.example"

        context = conversation_service.build_tool_context()

        assert context == {}
        assert "media_base_url" not in context

    def test_media_base_url_is_injected_via_provider(
        self, conversation_service, providers, mock_cfg: MagicMock
    ) -> None:
        """media_base_url は拡張が登録したプロバイダ経由で context に入る。"""
        repo = MagicMock()
        providers(
            current_media_repo=lambda: repo,
            media_base_url=lambda: mock_cfg.media_base_url,
        )
        mock_cfg.media_base_url = "http://lilla.example"

        context = conversation_service.build_tool_context()

        assert context["current_media_repo"] is repo
        assert context["media_base_url"] == "http://lilla.example"

    def test_media_base_url_key_exists_even_when_empty(
        self, conversation_service, providers, mock_cfg: MagicMock
    ) -> None:
        """設定値が空文字でもプロバイダが登録されていればキー自体は存在する。"""
        providers(media_base_url=lambda: mock_cfg.media_base_url)
        mock_cfg.media_base_url = ""

        context = conversation_service.build_tool_context()

        assert context == {"media_base_url": ""}

    def test_providers_are_reevaluated_on_each_call(
        self, conversation_service, providers, mock_cfg: MagicMock
    ) -> None:
        """プロバイダは呼び出しのたびに評価される（レジストリは値をキャッシュしない）。"""
        provider = MagicMock(side_effect=["1回目", "2回目"])
        providers(obsidian_client=provider)

        assert conversation_service.build_tool_context()["obsidian_client"] == "1回目"
        assert conversation_service.build_tool_context()["obsidian_client"] == "2回目"


class TestClientTypeDispatch:
    """`client_type` による「対話クライアント / task」の振り分けのテスト。

    コアが列挙する非対話の種別は "task" だけで、それ以外はすべて対話
    クライアントとして扱う（ツール一時無効化と `conversation_prompt` の付与）。
    """

    @pytest.fixture
    def fake_tools(self) -> dict:
        """`run_conversation` に渡すツールレジストリのダミー。"""
        return {"test_tool": {"schema": {}, "execute": AsyncMock()}}

    @pytest.fixture
    def calls(
        self, conversation_service, monkeypatch: pytest.MonkeyPatch
    ) -> dict[str, AsyncMock]:
        """LLM 呼び出し 2 経路をモックに差し替えて返す。

        `llm_tools` が空になった場合は `chat_to_llm`（非ストリーム）へ
        フォールバックするため、どちらが呼ばれたかでツール無効化を判定できる。
        """
        without_tools = AsyncMock(return_value="fallback reply")
        with_tools = AsyncMock(return_value={
            "content": "tool loop reply",
            "finish_reason": "stop",
            "tool_calls": None,
            "raw_message": {"role": "assistant", "content": "tool loop reply"},
        })
        monkeypatch.setattr(conversation_service, "chat_to_llm", without_tools)
        monkeypatch.setattr(conversation_service, "chat_to_llm_with_tools", with_tools)
        return {"without_tools": without_tools, "with_tools": with_tools}

    def _set_tools_disabled(
        self, conversation_service, monkeypatch: pytest.MonkeyPatch, disabled: bool
    ) -> None:
        """`is_tools_disabled` の戻り値を差し替える。"""
        monkeypatch.setattr(
            conversation_service, "is_tools_disabled", lambda: disabled
        )

    def _extra_prompt(self, mock_memory_manager_instance: MagicMock) -> str:
        """`build_system_prompt` に渡された `extra_prompt` を取り出す。"""
        return mock_memory_manager_instance.build_system_prompt.call_args.kwargs[
            "extra_prompt"
        ]

    # --- ツール一時無効化（!disable_tools）の効き先 ---

    @pytest.mark.parametrize("client_type", ["discord", "lilla-client", "future-client"])
    async def test_tools_disabled_clears_tools_for_conversational_clients(
        self,
        conversation_service,
        calls: dict[str, AsyncMock],
        fake_tools: dict,
        monkeypatch: pytest.MonkeyPatch,
        client_type: str,
    ) -> None:
        """対話クライアントでは llm_tools が空になる（"task" 以外はすべて対象）。"""
        self._set_tools_disabled(conversation_service, monkeypatch, True)

        await conversation_service.run_conversation(fake_tools, client_type=client_type)

        calls["without_tools"].assert_awaited_once()
        calls["with_tools"].assert_not_awaited()

    async def test_tools_disabled_does_not_clear_tools_for_task(
        self,
        conversation_service,
        calls: dict[str, AsyncMock],
        fake_tools: dict,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """task 実行では is_tools_disabled() が True でも llm_tools は空にならない。"""
        self._set_tools_disabled(conversation_service, monkeypatch, True)

        await conversation_service.run_conversation(fake_tools, client_type="task")

        calls["with_tools"].assert_awaited_once()
        calls["without_tools"].assert_not_awaited()

    @pytest.mark.parametrize(
        "client_type", ["discord", "lilla-client", "future-client", "task"]
    )
    async def test_tools_kept_when_not_disabled(
        self,
        conversation_service,
        calls: dict[str, AsyncMock],
        fake_tools: dict,
        monkeypatch: pytest.MonkeyPatch,
        client_type: str,
    ) -> None:
        """ツール無効化が OFF なら client_type によらず llm_tools は保たれる。"""
        self._set_tools_disabled(conversation_service, monkeypatch, False)

        await conversation_service.run_conversation(fake_tools, client_type=client_type)

        calls["with_tools"].assert_awaited_once()
        calls["without_tools"].assert_not_awaited()

    # --- conversation_prompt の付与先 ---

    @pytest.mark.parametrize("client_type", ["discord", "lilla-client", "future-client"])
    async def test_conversation_prompt_added_for_conversational_clients(
        self,
        conversation_service,
        calls: dict[str, AsyncMock],
        fake_tools: dict,
        mock_cfg: MagicMock,
        mock_memory_manager_instance: MagicMock,
        client_type: str,
    ) -> None:
        """対話クライアントには conversation_prompt が付与される。"""
        mock_cfg.conversation_prompt = "会話プロンプト"

        await conversation_service.run_conversation(fake_tools, client_type=client_type)

        assert self._extra_prompt(mock_memory_manager_instance) == "会話プロンプト"

    async def test_conversation_prompt_omitted_for_task(
        self,
        conversation_service,
        calls: dict[str, AsyncMock],
        fake_tools: dict,
        mock_cfg: MagicMock,
        mock_memory_manager_instance: MagicMock,
    ) -> None:
        """task 実行には conversation_prompt を付与しない（空文字列）。"""
        mock_cfg.conversation_prompt = "会話プロンプト"

        await conversation_service.run_conversation(fake_tools, client_type="task")

        assert self._extra_prompt(mock_memory_manager_instance) == ""


class TestConversationStartHook:
    """`run_conversation` 冒頭の会話開始フック呼び出しのテスト。"""

    @pytest.fixture(autouse=True)
    def _isolate_hooks(self, conversation_service, make_extension, use_extensions):
        """会話開始フックを拡張として差し込むヘルパーを返す。"""
        def _register(**hooks):
            use_extensions(make_extension("hook-pack", conversation_start_hooks=hooks))

        use_extensions()
        return _register

    @pytest.fixture
    def mock_chat(self, conversation_service, monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
        """ツールなし経路（chat_to_llm）をモックに差し替える。"""
        mock = AsyncMock(return_value="返答")
        monkeypatch.setattr(conversation_service, "chat_to_llm", mock)
        return mock

    async def test_registered_hook_is_awaited_with_ws_clients(
        self, conversation_service, _isolate_hooks, mock_chat: AsyncMock
    ) -> None:
        """登録済みフックは ws_clients を渡して await される。"""
        hook = AsyncMock()
        _isolate_hooks(**{"lilla-client": hook})
        ws_clients = {MagicMock()}

        await conversation_service.run_conversation(
            {}, client_type="lilla-client", ws_clients=ws_clients
        )

        hook.assert_awaited_once_with(ws_clients)

    async def test_hook_receives_none_when_no_ws_clients(
        self, conversation_service, _isolate_hooks, mock_chat: AsyncMock
    ) -> None:
        """ws_clients が渡されなければフックには None が渡る。"""
        hook = AsyncMock()
        _isolate_hooks(**{"lilla-client": hook})

        await conversation_service.run_conversation({}, client_type="lilla-client")

        hook.assert_awaited_once_with(None)

    @pytest.mark.parametrize("client_type", ["discord", "task"])
    async def test_hook_not_called_for_other_client_types(
        self, conversation_service, _isolate_hooks, mock_chat: AsyncMock, client_type: str
    ) -> None:
        """別の client_type に登録されたフックは呼ばれない。"""
        hook = AsyncMock()
        _isolate_hooks(**{"lilla-client": hook})

        await conversation_service.run_conversation({}, client_type=client_type)

        hook.assert_not_awaited()

    @pytest.mark.parametrize("client_type", ["lilla-client", "discord", "task"])
    async def test_no_hook_registered_does_not_raise(
        self, conversation_service, _isolate_hooks, mock_chat: AsyncMock, client_type: str
    ) -> None:
        """フック未登録（コア単体起動相当）でも例外を出さず通常どおり応答する。"""
        result = await conversation_service.run_conversation({}, client_type=client_type)

        assert result == "返答"

    async def test_hook_exception_does_not_break_conversation(
        self, conversation_service, _isolate_hooks, mock_chat: AsyncMock
    ) -> None:
        """フックが例外を送出しても run_conversation は通常どおり返答を返す。"""
        hook = AsyncMock(side_effect=RuntimeError("フック失敗"))
        _isolate_hooks(**{"lilla-client": hook})

        result = await conversation_service.run_conversation({}, client_type="lilla-client")

        hook.assert_awaited_once()
        assert result == "返答"
