"""commands/model.py のテスト。"""
from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

model_command = importlib.import_module("lilla_core.commands.model")
runtime_state = importlib.import_module("lilla_core.core.runtime_state")


@pytest.fixture(autouse=True)
def reset_state():
    """テストごとに上書き状態を初期化する（テスト間の汚染防止）。"""
    runtime_state.reset_active_llm_name()
    yield
    runtime_state.reset_active_llm_name()


@pytest.fixture
def mock_config(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """llm_providers / llm_default を持つ設定モックへ差し替える。"""
    config = MagicMock()
    config.llm.default = "ollama-gemma3"
    config.llm.providers = {"ollama-gemma3": MagicMock(), "deepseek-pro": MagicMock()}
    monkeypatch.setattr(model_command, "get_config", lambda: config)
    return config


def _make_message() -> MagicMock:
    msg = MagicMock()
    msg.reply = AsyncMock()
    return msg


async def _run(msg: MagicMock, arg: str) -> None:
    """コマンドハンドラを呼び出す（tools / bot は未使用）。"""
    await model_command.handle_model(msg, arg, {}, MagicMock())


class TestHandleModel:
    async def test_switches_to_known_provider(self, mock_config: MagicMock) -> None:
        """定義済みのプロバイダー名を指定すると切り替わる。"""
        msg = _make_message()

        await _run(msg, "deepseek-pro")

        assert runtime_state.get_active_llm_name() == "deepseek-pro"
        msg.reply.assert_called_once_with("モデルを deepseek-pro に切り替えました")

    async def test_strips_surrounding_whitespace(self, mock_config: MagicMock) -> None:
        """引数前後の空白は無視される。"""
        msg = _make_message()

        await _run(msg, "  deepseek-pro  ")

        assert runtime_state.get_active_llm_name() == "deepseek-pro"

    async def test_resets_to_default_without_arg(self, mock_config: MagicMock) -> None:
        """引数なしの場合はデフォルトモデルに戻す。"""
        runtime_state.set_active_llm_name("deepseek-pro")
        msg = _make_message()

        await _run(msg, "")

        assert runtime_state.get_active_llm_name() is None
        msg.reply.assert_called_once_with("モデルを ollama-gemma3 に戻しました")

    async def test_resets_to_default_with_blank_arg(self, mock_config: MagicMock) -> None:
        """空白のみの引数もリセット扱いにする。"""
        runtime_state.set_active_llm_name("deepseek-pro")
        msg = _make_message()

        await _run(msg, "   ")

        assert runtime_state.get_active_llm_name() is None

    async def test_rejects_unknown_provider(self, mock_config: MagicMock) -> None:
        """未定義の名前を指定した場合は状態を変えずエラーを返す。"""
        runtime_state.set_active_llm_name("deepseek-pro")
        msg = _make_message()

        await _run(msg, "unknown-model")

        assert runtime_state.get_active_llm_name() == "deepseek-pro"
        reply = msg.reply.call_args[0][0]
        assert "モデル 'unknown-model' は見つかりません" in reply
        assert "deepseek-pro" in reply
        assert "ollama-gemma3" in reply

    async def test_lists_available_models_sorted(self, mock_config: MagicMock) -> None:
        """エラー時の利用可能モデル一覧は名前順で列挙する。"""
        msg = _make_message()

        await _run(msg, "unknown-model")

        assert "利用可能なモデル: deepseek-pro, ollama-gemma3" in msg.reply.call_args[0][0]

    async def test_handles_empty_providers(self, mock_config: MagicMock) -> None:
        """プロバイダーが 1 つも定義されていない場合もエラー返信で完結する。"""
        mock_config.llm.providers = {}
        msg = _make_message()

        await _run(msg, "deepseek-pro")

        assert runtime_state.get_active_llm_name() is None
        assert "見つかりません" in msg.reply.call_args[0][0]

    async def test_registered_in_command_registry(self) -> None:
        """`!model` としてコマンドレジストリに登録されている。"""
        from lilla_core.commands.registry import get_command_handler

        assert get_command_handler("model") is model_command.handle_model


class TestHandleModelLocale:
    """`ui.locale` によって返信の言語が切り替わることの検証。"""

    async def test_replies_in_english_when_locale_is_en(
        self, mock_config: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`ui.locale: en` なら英語のカタログの文言で返信する。"""
        from lilla_core.core import config as config_module
        from lilla_core.ui import messages

        monkeypatch.setattr(
            config_module,
            "get_config",
            lambda: SimpleNamespace(ui=SimpleNamespace(locale="en")),
        )
        messages.clear_cache()
        msg = _make_message()

        await _run(msg, "deepseek-pro")

        msg.reply.assert_called_once_with("Switched the model to deepseek-pro")
