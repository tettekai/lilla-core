"""commands/registry.py と commands/__init__.py（動的ロード）のテスト。"""
from __future__ import annotations

import sys
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

    def test_duplicate_name_raises(self) -> None:
        """同名コマンドを別のハンドラで登録しようとすると fail-fast する。"""
        first, second = AsyncMock(), AsyncMock()
        registry.register_command("dummy")(first)

        with pytest.raises(ValueError, match="already registered"):
            registry.register_command("dummy")(second)

        assert registry.get_command_handler("dummy") is first

    def test_same_handler_can_be_registered_twice(self) -> None:
        """同じハンドラの再登録は許容する（モジュールの再 exec を衝突にしない）。"""
        async def handler(message, arg, tools, bot):
            return None

        registry.register_command("dummy")(handler)
        registry.register_command("dummy")(handler)

        assert registry.get_command_handler("dummy") is handler


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


class TestExtensionCommandPackages:
    """拡張の `command_packages()` から追加コマンドが登録されること。"""

    @pytest.fixture
    def command_package(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> str:
        """`@register_command` を持つモジュールを 1 つ含むパッケージを用意する。"""
        package_dir = tmp_path / "extra_commands"
        package_dir.mkdir()
        (package_dir / "__init__.py").write_text("", encoding="utf-8")
        (package_dir / "hello.py").write_text(
            "from lilla_core.commands.registry import register_command\n"
            "\n"
            "\n"
            '@register_command("hello")\n'
            "async def handle_hello(message, arg, tools, bot):\n"
            "    return None\n",
            encoding="utf-8",
        )
        monkeypatch.syspath_prepend(str(tmp_path))
        monkeypatch.delitem(sys.modules, "extra_commands", raising=False)
        monkeypatch.delitem(sys.modules, "extra_commands.hello", raising=False)
        return "extra_commands"

    def test_registers_commands_from_extension_package(
        self, command_package: str, make_extension, use_extensions
    ) -> None:
        """拡張が指定したパッケージのコマンドがレジストリへ入る。"""
        use_extensions(make_extension("pack", command_packages=[command_package]))

        names = load_all_commands()

        assert "hello" in names
        assert registry.get_command_handler("hello") is not None

    def test_missing_package_raises(self, make_extension, use_extensions) -> None:
        """import できないパッケージを指定したら起動が失敗する。"""
        use_extensions(make_extension("pack", command_packages=["no_such_command_pkg"]))

        with pytest.raises(ImportError):
            load_all_commands()
