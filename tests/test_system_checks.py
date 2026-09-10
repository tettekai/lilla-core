"""services/system_checks.py のテスト。"""
from __future__ import annotations

import asyncio
import importlib
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

system_checks = importlib.import_module("lilla_core.services.system_checks")


def _make_mongo_client(command: AsyncMock) -> MagicMock:
    """admin.command が指定モックになる Motor クライアントのモックを返す。"""
    client = MagicMock()
    client.admin.command = command
    return client


@pytest.fixture
def mongo_command() -> AsyncMock:
    """`admin.command("ping")` のモック。"""
    return AsyncMock(return_value={"ok": 1})


@pytest.fixture
def with_mocked_mongo(mongo_command: AsyncMock):
    """check_mongodb が使う lilla_core.core.config / repository.motor_client を差し替える。"""
    cfg = MagicMock()
    cfg.env.mongodb_uri = "mongodb://localhost:27017"
    client = _make_mongo_client(mongo_command)
    with patch.dict(
        sys.modules,
        {
            "lilla_core.core.config": MagicMock(get_config=lambda: cfg),
            "lilla_core.repository.motor_client": MagicMock(
                create_motor_client=MagicMock(return_value=client)
            ),
        },
    ):
        yield


class TestCheckProcessAlive:
    async def test_always_ok(self) -> None:
        """常に成功し、経過時間も記録される。"""
        result = await system_checks.check_process_alive()

        assert result.name == "process_alive"
        assert result.ok is True
        assert result.elapsed_ms >= 0


class TestCheckMongodb:
    async def test_ok_when_ping_succeeds(self, with_mocked_mongo, mongo_command: AsyncMock) -> None:
        """ping に応答すれば成功する。"""
        result = await system_checks.check_mongodb()

        assert result.name == "mongodb"
        assert result.ok is True
        mongo_command.assert_awaited_once_with("ping")

    async def test_ng_when_ping_raises(self, mongo_command: AsyncMock, with_mocked_mongo) -> None:
        """例外は捕捉され、ok=False の結果として返る（例外は伝播しない）。"""
        mongo_command.side_effect = RuntimeError("connection refused")

        result = await system_checks.check_mongodb()

        assert result.ok is False
        assert "RuntimeError" in result.detail
        assert "connection refused" in result.detail

    async def test_ng_on_timeout(
        self, mongo_command: AsyncMock, with_mocked_mongo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """タイムアウトしても例外を投げず ok=False を返す。"""
        monkeypatch.setattr(system_checks, "MONGO_TIMEOUT_SECONDS", 0.01)

        async def _never_returns(*args, **kwargs):
            await asyncio.sleep(10)

        mongo_command.side_effect = _never_returns

        result = await system_checks.check_mongodb()

        assert result.ok is False
        assert "タイムアウト" in result.detail

    async def test_uses_configured_uri(self, with_mocked_mongo) -> None:
        """設定の mongodb_uri でクライアントを生成する。"""
        create = sys.modules["lilla_core.repository.motor_client"].create_motor_client

        await system_checks.check_mongodb()

        create.assert_called_once_with("mongodb://localhost:27017")


class TestCheckCommandRegistry:
    async def test_ok_when_commands_registered(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """1 件以上登録されていれば成功する。"""
        monkeypatch.setitem(
            sys.modules,
            "lilla_core.commands.registry",
            MagicMock(known_command_names=lambda: ["selftest", "model"]),
        )

        result = await system_checks.check_command_registry()

        assert result.name == "command_registry"
        assert result.ok is True
        assert "2" in result.detail

    async def test_ng_when_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """0 件なら失敗する。"""
        monkeypatch.setitem(
            sys.modules, "lilla_core.commands.registry", MagicMock(known_command_names=lambda: [])
        )

        result = await system_checks.check_command_registry()

        assert result.ok is False

    async def test_ng_when_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """例外は捕捉され ok=False として返る。"""
        def _boom():
            raise RuntimeError("broken registry")

        monkeypatch.setitem(
            sys.modules, "lilla_core.commands.registry", MagicMock(known_command_names=_boom)
        )

        result = await system_checks.check_command_registry()

        assert result.ok is False
        assert "broken registry" in result.detail


class TestCheckTaskTools:
    async def test_ok_when_tools_present(self) -> None:
        """1 件以上あれば成功する。"""
        result = await system_checks.check_task_tools({"task_a": object()})

        assert result.name == "task_tools"
        assert result.ok is True
        assert "1" in result.detail

    async def test_ng_when_empty(self) -> None:
        """0 件なら失敗する。"""
        result = await system_checks.check_task_tools({})

        assert result.ok is False

    async def test_ng_when_none(self) -> None:
        """None でも例外を投げず失敗として返る。"""
        result = await system_checks.check_task_tools(None)

        assert result.ok is False


@pytest.fixture
def llm_config() -> MagicMock:
    """check_llm が参照する設定モック。"""
    cfg = MagicMock()
    cfg.llm.default = "ollama-gemma3"
    cfg.conversation_prompt = "会話用プロンプト"
    return cfg


@pytest.fixture
def memory_manager() -> MagicMock:
    """build_system_prompt を持つ MemoryManager モック。"""
    manager = MagicMock()
    manager.build_system_prompt = AsyncMock(
        return_value="ヒミツのユーザーメモを含むシステムプロンプト"
    )
    return manager


@pytest.fixture
def chat_to_llm() -> AsyncMock:
    """lilla_core.api.llm_client.chat_to_llm のモック。"""
    return AsyncMock(return_value="pong")


@pytest.fixture
def active_llm_name() -> MagicMock:
    """lilla_core.core.runtime_state.get_active_llm_name のモック（既定は上書きなし）。"""
    return MagicMock(return_value=None)


@pytest.fixture
def with_mocked_llm(
    llm_config: MagicMock,
    memory_manager: MagicMock,
    chat_to_llm: AsyncMock,
    active_llm_name: MagicMock,
):
    """check_llm が使う依存モジュールを差し替える。"""
    encoding = MagicMock()
    encoding.encode = MagicMock(return_value=[0] * 42)
    with patch.dict(
        sys.modules,
        {
            "lilla_core.core.config": MagicMock(get_config=lambda: llm_config),
            "lilla_core.core.runtime_state": MagicMock(get_active_llm_name=active_llm_name),
            "lilla_core.services.memory_manager": MagicMock(
                get_memory_manager=lambda: memory_manager
            ),
            "lilla_core.api.llm_client": MagicMock(chat_to_llm=chat_to_llm),
            "tiktoken": MagicMock(get_encoding=MagicMock(return_value=encoding)),
        },
    ):
        yield


class TestCheckLlm:
    async def test_ok_and_reports_system_prompt_tokens(
        self, with_mocked_llm, chat_to_llm: AsyncMock, memory_manager: MagicMock
    ) -> None:
        """本番同様の経路でプロンプトを組み立て、疎通に成功する。"""
        result = await system_checks.check_llm()

        assert result.name == "llm"
        assert result.ok is True
        assert "system_prompt_tokens=42" in result.detail
        assert "provider=ollama-gemma3" in result.detail
        memory_manager.build_system_prompt.assert_awaited_once_with(
            extra_prompt="会話用プロンプト", client_type="discord"
        )
        chat_to_llm.assert_awaited_once()

    async def test_sends_minimal_user_message(
        self, with_mocked_llm, chat_to_llm: AsyncMock
    ) -> None:
        """user 発言は疎通確認用の最小限（ping）に絞る。"""
        await system_checks.check_llm()

        args, kwargs = chat_to_llm.call_args
        assert args[0] == "ping"
        assert kwargs["system_prompt"] == "ヒミツのユーザーメモを含むシステムプロンプト"

    async def test_uses_active_provider(
        self, with_mocked_llm, chat_to_llm: AsyncMock, active_llm_name: MagicMock
    ) -> None:
        """`!model` で切り替え中なら、そのプロバイダーをそのまま検証する。"""
        active_llm_name.return_value = "deepseek-pro"

        result = await system_checks.check_llm()

        assert chat_to_llm.call_args[1]["llm_name"] == "deepseek-pro"
        assert "provider=deepseek-pro" in result.detail

    async def test_detail_excludes_system_prompt_body(self, with_mocked_llm) -> None:
        """detail にシステムプロンプト本文を含めない。"""
        result = await system_checks.check_llm()

        assert "ヒミツのユーザーメモ" not in result.detail

    async def test_ng_when_prompt_build_fails(
        self, with_mocked_llm, memory_manager: MagicMock, chat_to_llm: AsyncMock
    ) -> None:
        """プロンプト組み立てに失敗したらその時点で ok=False を返す（LLM は呼ばない）。"""
        memory_manager.build_system_prompt.side_effect = RuntimeError("prompt broken")

        result = await system_checks.check_llm()

        assert result.ok is False
        assert "prompt broken" in result.detail
        chat_to_llm.assert_not_awaited()

    async def test_timeout_keeps_token_count(
        self, with_mocked_llm, chat_to_llm: AsyncMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """LLM 往復がタイムアウトしても system_prompt_tokens は detail に残る。"""
        monkeypatch.setattr(system_checks, "LLM_TIMEOUT_SECONDS", 0.01)

        async def _never_returns(*args, **kwargs):
            await asyncio.sleep(10)

        chat_to_llm.side_effect = _never_returns

        result = await system_checks.check_llm()

        assert result.ok is False
        assert "タイムアウト" in result.detail
        assert "system_prompt_tokens=42" in result.detail

    async def test_ng_when_roundtrip_fails(
        self, with_mocked_llm, chat_to_llm: AsyncMock
    ) -> None:
        """LLM 往復の例外は捕捉され ok=False として返る。"""
        chat_to_llm.side_effect = RuntimeError("llm down")

        result = await system_checks.check_llm()

        assert result.ok is False
        assert "llm down" in result.detail
        assert "system_prompt_tokens=42" in result.detail
