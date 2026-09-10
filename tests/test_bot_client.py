"""bot_client.py のテスト。

`python src/lilla_core/bot.py` のスクリプト実行時に bot.py が二重ロードされる問題を避けるため、
Discord Client の生成は bot_client のみが行う。ここではその共有インスタンスが
実 discord モジュール上で正しく構成されることを確認する。

他のテストが sys.modules["lilla_core.bot_client"] へ注入したモックの影響を受けず、また
逆に他のテストを汚染しないよう、実ファイルを別名で直接ロードして検証する。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_bot_client():
    """src/bot_client.py を sys.modules に登録せず別名でロードする。"""
    path = Path(__file__).parent.parent / "src" / "lilla_core" / "bot_client.py"
    spec = importlib.util.spec_from_file_location("bot_client_under_test", str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestBotClient:
    """bot_client モジュールが生成する共有 Client の検証。"""

    def test_bot_is_commands_bot_instance(self) -> None:
        """bot は discord.ext.commands.Bot のインスタンスである。"""
        from discord.ext import commands

        module = _load_bot_client()

        assert isinstance(module.bot, commands.Bot)

    def test_command_prefix_is_exclamation(self) -> None:
        """コマンドプレフィックスは "!" である。"""
        module = _load_bot_client()

        assert module.bot.command_prefix == "!"

    def test_message_content_intent_enabled(self) -> None:
        """message_content インテントが有効になっている。"""
        module = _load_bot_client()

        assert module.intents.message_content is True
        assert module.bot.intents.message_content is True

    def test_exposes_shared_bot_and_intents(self) -> None:
        """bot と intents をモジュール属性として公開している。"""
        module = _load_bot_client()

        assert hasattr(module, "bot")
        assert hasattr(module, "intents")
