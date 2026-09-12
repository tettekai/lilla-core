"""`lilla_core.core.extension` の `Extension` とロード・参照 API のテスト。"""
from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock

import pytest

from lilla_core.core import extension as ext_module
from lilla_core.core.extension import Extension


@pytest.fixture(autouse=True)
def _isolate_registry():
    """登録内容はプロセス全体で共有されるため、テストごとに前後で復元する。"""
    saved = ext_module.get_extensions()
    ext_module.reset_extensions()
    yield
    ext_module.set_extensions(saved)


def _make_module(name: str, attrs: dict) -> ModuleType:
    """`sys.modules` へ差し込むためのダミーモジュールを組み立てる。"""
    module = ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    return module


@pytest.fixture
def register_module(monkeypatch: pytest.MonkeyPatch):
    """import パスに対応するダミーモジュールを `sys.modules` へ差し込むヘルパー。"""
    def _register(name: str, **attrs) -> ModuleType:
        module = _make_module(name, attrs)
        monkeypatch.setitem(sys.modules, name, module)
        return module

    return _register


# ---------------------------------------------------------------------------
# TestExtensionDefaults
# ---------------------------------------------------------------------------


class TestExtensionDefaults:
    """貢献・フックのデフォルトが「空・何もしない」であること。"""

    def test_contribution_defaults_are_empty(self) -> None:
        """オーバーライドしなければ何も貢献しない。"""
        ext = Extension()
        assert ext.config_models() == {}
        assert ext.env_fields() == {}
        assert ext.tool_roots() == []
        assert ext.command_packages() == []
        assert ext.startup_repos() == []
        assert ext.tool_context_providers() == {}
        assert ext.result_deliveries() == {}
        assert ext.client_prompt_providers() == {}
        assert ext.conversation_start_hooks() == {}

    async def test_on_message_default_is_false(self) -> None:
        """デフォルトの `on_message` は「処理しなかった」を返す。"""
        assert await Extension().on_message(MagicMock()) is False

    async def test_setup_default_does_nothing(self) -> None:
        """デフォルトの `setup` は何もせず None を返す。"""
        assert await Extension().setup({}, {}, MagicMock()) is None


# ---------------------------------------------------------------------------
# TestLoadExtensions
# ---------------------------------------------------------------------------


class TestLoadExtensions:
    def test_empty_spec_loads_nothing(self) -> None:
        """未設定・空なら 0 個（コア単体起動）。"""
        assert ext_module.load_extensions("") == []
        assert ext_module.get_extensions() == []

    def test_reads_env_var_when_spec_is_none(
        self, register_module, make_extension, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`spec` 省略時は `LILLA_EXTENSIONS` を読む。"""
        register_module("dummy_pack", extension=make_extension("pack"))
        monkeypatch.setenv(ext_module.EXTENSIONS_ENV_VAR, "dummy_pack")

        loaded = ext_module.load_extensions()

        assert [e.name for e in loaded] == ["pack"]

    def test_loads_multiple_modules_in_order(
        self, register_module, make_extension
    ) -> None:
        """カンマ区切りで複数のモジュールを順に読む。"""
        register_module("pack_a", extension=make_extension("a"))
        register_module("pack_b", extension=make_extension("b"))

        loaded = ext_module.load_extensions("pack_a, pack_b")

        assert [e.name for e in loaded] == ["a", "b"]

    def test_missing_module_raises(self) -> None:
        """存在しないモジュールを指定すると起動が失敗する。"""
        with pytest.raises(ImportError):
            ext_module.load_extensions("no_such_extension_module")

    def test_module_without_extension_attr_raises(self, register_module) -> None:
        """`extension` 属性が無いモジュールは fail-fast。"""
        register_module("bad_pack", something_else=object())

        with pytest.raises(AttributeError, match="must export"):
            ext_module.load_extensions("bad_pack")

    def test_module_with_wrong_type_raises(self, register_module) -> None:
        """`extension` が `Extension` インスタンスでなければ fail-fast。"""
        register_module("bad_pack", extension=object())

        with pytest.raises(TypeError, match="Extension instance"):
            ext_module.load_extensions("bad_pack")

    def test_same_module_twice_raises_on_duplicate_name(
        self, register_module, make_extension
    ) -> None:
        """同じモジュールを 2 度並べると name 重複で落ちる。"""
        register_module("pack_a", extension=make_extension("a"))

        with pytest.raises(ValueError, match="Duplicate extension name"):
            ext_module.load_extensions("pack_a,pack_a")


# ---------------------------------------------------------------------------
# TestValidation
# ---------------------------------------------------------------------------


class TestValidation:
    def test_missing_name_raises(self) -> None:
        """`name` 未設定は型注釈では防げないのでロード時に落とす。"""
        with pytest.raises(ValueError, match="non-empty 'name'"):
            ext_module.set_extensions([Extension()])

    def test_blank_name_raises(self, make_extension) -> None:
        """空白だけの `name` も落とす。"""
        with pytest.raises(ValueError, match="non-empty 'name'"):
            ext_module.set_extensions([make_extension("   ")])

    def test_duplicate_name_raises(self, make_extension) -> None:
        """`name` の重複は fail-fast。"""
        with pytest.raises(ValueError, match="Duplicate extension name"):
            ext_module.set_extensions([make_extension("same"), make_extension("same")])

    def test_duplicate_tool_context_key_raises(self, make_extension) -> None:
        """ツール context プロバイダのキー重複は fail-fast。"""
        first = make_extension("a", tool_context_providers={"client": lambda: 1})
        second = make_extension("b", tool_context_providers={"client": lambda: 2})

        with pytest.raises(ValueError, match="Duplicate tool context provider key"):
            ext_module.set_extensions([first, second])

    def test_duplicate_client_prompt_client_type_raises(self, make_extension) -> None:
        """client prompt provider の client_type 重複は fail-fast。"""
        first = make_extension("a", client_prompt_providers={"web": lambda: "x"})
        second = make_extension("b", client_prompt_providers={"web": lambda: "y"})

        with pytest.raises(ValueError, match="Duplicate client prompt provider key"):
            ext_module.set_extensions([first, second])

    def test_duplicate_conversation_start_hook_raises(self, make_extension) -> None:
        """conversation start hook の client_type 重複は fail-fast。"""
        first = make_extension("a", conversation_start_hooks={"web": AsyncMock()})
        second = make_extension("b", conversation_start_hooks={"web": AsyncMock()})

        with pytest.raises(ValueError, match="Duplicate conversation start hook key"):
            ext_module.set_extensions([first, second])

    def test_duplicate_result_delivery_raises(self, make_extension) -> None:
        """result delivery の client_type 重複は fail-fast。"""
        first = make_extension("a", result_deliveries={"web": AsyncMock()})
        second = make_extension("b", result_deliveries={"web": AsyncMock()})

        with pytest.raises(ValueError, match="Duplicate result delivery key"):
            ext_module.set_extensions([first, second])

    def test_discord_result_delivery_is_reserved(self, make_extension) -> None:
        """"discord" はコアが配送を持つ予約キーで、拡張は登録できない。"""
        ext = make_extension("a", result_deliveries={"discord": AsyncMock()})

        with pytest.raises(ValueError, match="reserved key"):
            ext_module.set_extensions([ext])

    def test_discord_client_prompt_is_not_reserved(self, make_extension) -> None:
        """"discord" のプロンプトは内蔵デフォルトがあるだけで、上書きは許す。"""
        provider = lambda: "custom"
        ext_module.set_extensions(
            [make_extension("a", client_prompt_providers={"discord": provider})]
        )

        assert ext_module.get_client_prompt_provider("discord") is provider

    def test_nothing_is_registered_when_validation_fails(self, make_extension) -> None:
        """検証に失敗したら登録内容を一切残さない。"""
        ext_module.set_extensions([make_extension("ok", tool_context_providers={"a": lambda: 1})])

        with pytest.raises(ValueError):
            ext_module.set_extensions([make_extension("dup"), make_extension("dup")])

        assert [e.name for e in ext_module.get_extensions()] == ["ok"]
        assert "a" in ext_module.get_tool_context_providers()


# ---------------------------------------------------------------------------
# TestAggregatedContributions
# ---------------------------------------------------------------------------


class TestAggregatedContributions:
    def test_startup_repos_are_concatenated_in_load_order(self, make_extension) -> None:
        """起動時リポジトリはロード順に連結される。"""
        first, second, third = MagicMock(), MagicMock(), MagicMock()
        ext_module.set_extensions([
            make_extension("a", startup_repos=[first, second]),
            make_extension("b", startup_repos=[third]),
        ])

        assert ext_module.get_startup_repos() == [first, second, third]

    def test_tool_roots_are_concatenated_in_load_order(self, make_extension) -> None:
        """ツール探索ルートはロード順に連結される。"""
        ext_module.set_extensions([
            make_extension("a", tool_roots=[Path("/one")]),
            make_extension("b", tool_roots=[Path("/two")]),
        ])

        assert ext_module.get_tool_roots() == [Path("/one"), Path("/two")]

    def test_command_packages_drop_duplicates(self, make_extension) -> None:
        """同じパッケージを複数の拡張が指定しても 1 度だけ返す。"""
        ext_module.set_extensions([
            make_extension("a", command_packages=["pkg.one", "pkg.two"]),
            make_extension("b", command_packages=["pkg.two", "pkg.three"]),
        ])

        assert ext_module.get_command_packages() == ["pkg.one", "pkg.two", "pkg.three"]

    def test_lookups_return_none_when_unregistered(self) -> None:
        """未登録の client_type はどの参照でも None。"""
        assert ext_module.get_result_delivery("web") is None
        assert ext_module.get_client_prompt_provider("web") is None
        assert ext_module.get_conversation_start_hook("web") is None

    def test_tool_context_providers_returns_a_copy(self, make_extension) -> None:
        """戻り値を書き換えても登録内容には影響しない。"""
        ext_module.set_extensions([make_extension("a", tool_context_providers={"x": lambda: 1})])

        providers = ext_module.get_tool_context_providers()
        providers["y"] = lambda: 2

        assert "y" not in ext_module.get_tool_context_providers()


# ---------------------------------------------------------------------------
# TestRunSetupHooks
# ---------------------------------------------------------------------------


class TestRunSetupHooks:
    async def test_awaits_each_setup_in_load_order(self, make_extension) -> None:
        """`setup()` はロード順に await される。"""
        order = []
        first = AsyncMock(side_effect=lambda *_a: order.append("a"))
        second = AsyncMock(side_effect=lambda *_a: order.append("b"))
        ext_module.set_extensions([
            make_extension("a", setup=first),
            make_extension("b", setup=second),
        ])

        tools, llm_tools, bot = {}, {}, MagicMock()
        await ext_module.run_setup_hooks(tools, llm_tools, bot)

        assert order == ["a", "b"]
        first.assert_awaited_once_with(tools, llm_tools, bot)

    async def test_exception_propagates(self, make_extension) -> None:
        """`setup()` の例外は握りつぶさず起動を止める。"""
        ext_module.set_extensions([
            make_extension("a", setup=AsyncMock(side_effect=RuntimeError("boom")))
        ])

        with pytest.raises(RuntimeError, match="boom"):
            await ext_module.run_setup_hooks({}, {}, MagicMock())


# ---------------------------------------------------------------------------
# TestDispatchOnMessage
# ---------------------------------------------------------------------------


class TestDispatchOnMessage:
    @pytest.fixture
    def mock_notify_error(self, monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
        """エラー通知をモックに差し替える（遅延 import 先を直接差し替える）。"""
        from lilla_core.core import error_notify

        mock = AsyncMock()
        monkeypatch.setattr(error_notify, "notify_error", mock)
        return mock

    async def test_returns_false_without_extensions(self) -> None:
        """拡張が 0 個なら常に False で、通常フローへ進む。"""
        assert await ext_module.dispatch_on_message(MagicMock()) is False

    async def test_chains_until_one_returns_true(self, make_extension) -> None:
        """先の拡張が True を返したら後続は呼ばれない。"""
        first = AsyncMock(return_value=True)
        second = AsyncMock(return_value=False)
        ext_module.set_extensions([
            make_extension("a", on_message=first),
            make_extension("b", on_message=second),
        ])

        assert await ext_module.dispatch_on_message(MagicMock()) is True
        first.assert_awaited_once()
        second.assert_not_awaited()

    async def test_falls_through_when_all_return_false(self, make_extension) -> None:
        """全員が False なら False（通常フローへ進む）。"""
        first = AsyncMock(return_value=False)
        second = AsyncMock(return_value=False)
        ext_module.set_extensions([
            make_extension("a", on_message=first),
            make_extension("b", on_message=second),
        ])

        assert await ext_module.dispatch_on_message(MagicMock()) is False
        first.assert_awaited_once()
        second.assert_awaited_once()

    async def test_exception_stops_chain_and_normal_flow(
        self, make_extension, mock_notify_error: AsyncMock
    ) -> None:
        """例外時は後続も通常フローも進まない（True を返す）。"""
        failing = AsyncMock(side_effect=RuntimeError("boom"))
        later = AsyncMock(return_value=False)
        ext_module.set_extensions([
            make_extension("a", on_message=failing),
            make_extension("b", on_message=later),
        ])

        assert await ext_module.dispatch_on_message(MagicMock()) is True
        later.assert_not_awaited()

    async def test_exception_notifies_error(
        self, make_extension, mock_notify_error: AsyncMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """例外は ERROR ログとエラー通知チャンネルの両方へ出す。"""
        error = RuntimeError("boom")
        ext_module.set_extensions([
            make_extension("broken-pack", on_message=AsyncMock(side_effect=error))
        ])
        bot = MagicMock()

        with caplog.at_level("ERROR"):
            await ext_module.dispatch_on_message(MagicMock(), bot)

        assert "broken-pack" in caplog.text
        mock_notify_error.assert_awaited_once()
        assert mock_notify_error.await_args[0][0] is bot
        assert "broken-pack" in mock_notify_error.await_args[0][1]
        assert mock_notify_error.await_args[0][2] is error
