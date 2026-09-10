"""core/runtime_state.py のテスト。"""
from __future__ import annotations

import importlib
import sys

import pytest

runtime_state = importlib.import_module("lilla_core.core.runtime_state")


@pytest.fixture(autouse=True)
def reset_state():
    """テストごとにグローバル状態を初期化する（テスト間の汚染防止）。"""
    runtime_state.reset_active_llm_name()
    runtime_state.set_tools_disabled(False)
    yield
    runtime_state.reset_active_llm_name()
    runtime_state.set_tools_disabled(False)


class TestRuntimeState:
    def test_default_is_none(self) -> None:
        """初期状態では上書きされていない（None）。"""
        assert runtime_state.get_active_llm_name() is None

    def test_set_and_get(self) -> None:
        """設定した名前がそのまま取得できる。"""
        runtime_state.set_active_llm_name("deepseek-pro")

        assert runtime_state.get_active_llm_name() == "deepseek-pro"

    def test_set_overwrites_previous_value(self) -> None:
        """再設定すると後から設定した名前で上書きされる。"""
        runtime_state.set_active_llm_name("deepseek-pro")
        runtime_state.set_active_llm_name("ollama-gemma3")

        assert runtime_state.get_active_llm_name() == "ollama-gemma3"

    def test_reset_clears_value(self) -> None:
        """リセットすると None に戻る。"""
        runtime_state.set_active_llm_name("deepseek-pro")
        runtime_state.reset_active_llm_name()

        assert runtime_state.get_active_llm_name() is None

    def test_reset_is_idempotent(self) -> None:
        """未設定の状態でリセットしてもエラーにならない。"""
        runtime_state.reset_active_llm_name()
        runtime_state.reset_active_llm_name()

        assert runtime_state.get_active_llm_name() is None

    def test_state_is_shared_across_imports(self) -> None:
        """同じモジュールを import し直しても状態はグローバルに共有される。"""
        runtime_state.set_active_llm_name("deepseek-pro")
        reimported = sys.modules["lilla_core.core.runtime_state"]

        assert reimported.get_active_llm_name() == "deepseek-pro"

    def test_tools_disabled_default_is_false(self) -> None:
        """初期状態ではツールは無効化されていない。"""
        assert runtime_state.is_tools_disabled() is False

    def test_set_tools_disabled_true(self) -> None:
        """無効化フラグを設定すると True になる。"""
        runtime_state.set_tools_disabled(True)

        assert runtime_state.is_tools_disabled() is True

    def test_set_tools_disabled_false_resets(self) -> None:
        """無効化フラグを False に戻すと有効に戻る。"""
        runtime_state.set_tools_disabled(True)
        runtime_state.set_tools_disabled(False)

        assert runtime_state.is_tools_disabled() is False
