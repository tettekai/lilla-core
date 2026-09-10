"""commands/registry.py と commands/__init__.py（動的ロード）のテスト。"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from lilla_core.commands import load_all_commands, registry

# 現行コマンド（`src/commands/` にファイルを置くだけで増える）
_EXPECTED_COMMANDS = {"mongodata", "runtask", "cleardirty", "toolresult"}


@pytest.fixture(autouse=True)
def isolated_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """レジストリをコピーに差し替え、テスト内の登録が他テストへ漏れないようにする。"""
    monkeypatch.setattr(registry, "_KNOWN_COMMANDS", dict(registry._KNOWN_COMMANDS))


class TestRegisterCommand:
    def test_registers_handler_under_name(self) -> None:
        """デコレータでコマンド名にハンドラを登録する。"""
        handler = AsyncMock()
        registry.register_command("dummy")(handler)
        assert registry.get_command_handler("dummy") is handler

    def test_returns_original_function(self) -> None:
        """デコレータはハンドラ関数をそのまま返す。"""
        async def handler(message, arg, tools, bot):
            return None

        assert registry.register_command("dummy")(handler) is handler

    def test_duplicate_name_overwrites_and_warns(self, caplog) -> None:
        """同名コマンドの二重登録は警告ログを出して上書きする。"""
        import logging

        first, second = AsyncMock(), AsyncMock()
        registry.register_command("dummy")(first)
        with caplog.at_level(logging.WARNING):
            registry.register_command("dummy")(second)

        assert registry.get_command_handler("dummy") is second
        assert any("duplicated" in r.message for r in caplog.records)


class TestKnownCommandNames:
    def test_lists_registered_names(self) -> None:
        """登録済みのコマンド名を列挙する。"""
        registry.register_command("dummy")(AsyncMock())
        assert "dummy" in registry.known_command_names()

    def test_returns_a_copy(self) -> None:
        """戻り値のリストを変更してもレジストリには影響しない。"""
        names = registry.known_command_names()
        names.append("not_a_command")
        assert "not_a_command" not in registry.known_command_names()


class TestGetCommandHandler:
    def test_returns_none_for_unknown_name(self) -> None:
        """未登録のコマンド名には None を返す。"""
        assert registry.get_command_handler("no_such_command") is None


class TestLoadAllCommands:
    def test_loads_all_command_modules(self) -> None:
        """`src/commands/` 配下の全コマンドが登録される。"""
        names = set(load_all_commands())
        assert _EXPECTED_COMMANDS <= names

    def test_is_idempotent(self) -> None:
        """複数回呼んでも登録内容は変わらない。"""
        first = sorted(load_all_commands())
        second = sorted(load_all_commands())
        assert first == second
