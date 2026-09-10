"""command_handler.py のテスト（コマンドのディスパッチと抽出）。

個別コマンドのロジックは `src/commands/` 配下にあり、
それぞれ `tests/test_command_*.py` でテストする。
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from lilla_core.commands import load_all_commands, registry
from lilla_core.handlers import command_handler

_UUID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"

# ディスパッチ・抽出はレジストリの内容に依存するため、実コマンドを読み込んでおく
load_all_commands()


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------


def _make_message() -> MagicMock:
    msg = MagicMock()
    msg.reply = AsyncMock()
    return msg


@pytest.fixture()
def isolated_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """レジストリをコピーに差し替え、テスト内の登録が他テストへ漏れないようにする。"""
    monkeypatch.setattr(registry, "_KNOWN_COMMANDS", dict(registry._KNOWN_COMMANDS))


@pytest.fixture()
def mock_notify_error(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """エラー通知（ERROR ログ + error_channel）をモックに差し替える。"""
    mock = AsyncMock()
    monkeypatch.setattr(command_handler, "notify_error", mock)
    return mock


# ---------------------------------------------------------------------------
# TestHandleCommand
# ---------------------------------------------------------------------------


class TestHandleCommand:
    async def test_empty_command_replies_error(self) -> None:
        """コマンド未入力時の案内はエラーではないので従来通り reply する。"""
        msg = _make_message()
        await command_handler.handle_command(msg, "!", {}, MagicMock())
        msg.reply.assert_called_once()
        assert "コマンド" in msg.reply.call_args[0][0]

    async def test_dispatches_to_registered_handler(self, isolated_registry: None) -> None:
        """コマンド名の完全一致でレジストリのハンドラへ委譲する。"""
        handler = AsyncMock()
        registry.register_command("runtask")(handler)
        msg = _make_message()
        tools = {"task_tool": {"trigger": "task"}}
        bot = MagicMock()

        await command_handler.handle_command(msg, "!runtask my_tool", tools, bot)

        handler.assert_called_once_with(msg, "my_tool", tools, bot)

    async def test_passes_empty_arg_without_argument(self, isolated_registry: None) -> None:
        """引数のないコマンドには空文字列が渡る。"""
        handler = AsyncMock()
        registry.register_command("cleardirty")(handler)
        msg = _make_message()
        bot = MagicMock()

        await command_handler.handle_command(msg, "!cleardirty", {}, bot)

        handler.assert_called_once_with(msg, "", {}, bot)

    async def test_passes_multiline_arg(self, isolated_registry: None) -> None:
        """2 行目以降も引数としてそのまま渡る（toolresult の本文など）。"""
        handler = AsyncMock()
        registry.register_command("toolresult")(handler)
        msg = _make_message()

        await command_handler.handle_command(msg, f"!toolresult {_UUID}\n本文", {}, MagicMock())

        assert handler.call_args[0][1] == f"{_UUID}\n本文"

    async def test_splits_only_once(self, isolated_registry: None) -> None:
        """分割は最初の空白 1 回だけで、引数内の空白は保持される。"""
        handler = AsyncMock()
        registry.register_command("mongodata")(handler)
        msg = _make_message()

        await command_handler.handle_command(msg, '!mongodata {"a": 1, "b": 2}', {}, MagicMock())

        assert handler.call_args[0][1] == '{"a": 1, "b": 2}'

    async def test_old_colon_format_is_unknown_command(
        self, mock_notify_error: AsyncMock
    ) -> None:
        """廃止したコロン付き形式は未知コマンドとして扱われる。"""
        msg = _make_message()
        await command_handler.handle_command(msg, "!runtask:my_tool", {}, MagicMock())
        mock_notify_error.assert_called_once()
        assert "未知" in mock_notify_error.call_args[0][2]
        msg.reply.assert_not_called()

    async def test_unknown_command_notifies_error(self, mock_notify_error: AsyncMock) -> None:
        """未知コマンドは元チャンネルに返信せず、エラー通知のみ行う。"""
        msg = _make_message()
        bot = MagicMock()
        await command_handler.handle_command(msg, "!unknown", {}, bot)
        mock_notify_error.assert_called_once()
        assert mock_notify_error.call_args[0][0] is bot
        assert "未知" in mock_notify_error.call_args[0][2]
        msg.reply.assert_not_called()

    async def test_unknown_command_lists_registered_commands(
        self, mock_notify_error: AsyncMock
    ) -> None:
        """案内文（2 行目）には登録済みコマンドが並ぶ。"""
        msg = _make_message()
        await command_handler.handle_command(msg, "!cleardm", {}, MagicMock())
        supported = mock_notify_error.call_args[0][2].split("\n")[1]
        for name in registry.known_command_names():
            assert f"!{name}" in supported
        assert "!cleardm" not in supported

    async def test_exception_in_handler_notifies_error(
        self, isolated_registry: None, mock_notify_error: AsyncMock
    ) -> None:
        """ハンドラ内の例外も元チャンネルに返信せず、エラー通知のみ行う。"""
        error = Exception("boom")
        registry.register_command("runtask")(AsyncMock(side_effect=error))
        msg = _make_message()
        bot = MagicMock()
        await command_handler.handle_command(msg, "!runtask x", {}, bot)
        mock_notify_error.assert_called_once_with(
            bot, "コマンド処理中にエラーが発生しました", error
        )
        msg.reply.assert_not_called()


# ---------------------------------------------------------------------------
# TestExtractCommandContent
# ---------------------------------------------------------------------------


class TestExtractCommandContent:
    def test_extracts_from_hermes_header(self) -> None:
        content = (
            "Cronjob Response: askendaily\n"
            "(job_id: 9466f746a7fe)\n"
            "-------------\n"
            '!mongodata\n'
            '{ "collection": "x" }'
        )
        result = command_handler.extract_command_content(content)
        assert result == '!mongodata\n{ "collection": "x" }'

    def test_plain_command_without_header(self) -> None:
        content = "!runtask foo"
        assert command_handler.extract_command_content(content) == "!runtask foo"

    def test_normal_message_with_exclamation_returns_none(self) -> None:
        content = "やったー！すごい！"
        assert command_handler.extract_command_content(content) is None

    def test_no_known_command_returns_none(self) -> None:
        content = "!unknown command here"
        assert command_handler.extract_command_content(content) is None

    def test_bare_exclamation_returns_none(self) -> None:
        """`!` だけの行はコマンド名が空なので既知コマンドにならない。"""
        assert command_handler.extract_command_content("!") is None

    def test_old_colon_format_returns_none(self) -> None:
        """廃止したコロン付き形式は既知コマンドとして抽出されない。"""
        assert command_handler.extract_command_content("!runtask: foo") is None

    def test_cleardirty_command(self) -> None:
        assert command_handler.extract_command_content("!cleardirty") == "!cleardirty"

    def test_cleardm_is_not_a_known_command(self) -> None:
        """廃止した !cleardm は既知コマンドとして抽出されない。"""
        assert command_handler.extract_command_content("!cleardm") is None

    def test_toolresult_command_keeps_body(self) -> None:
        """!toolresult は 1 行目以降の本文も含めて抽出される。"""
        content = f"ヘッダー\n!toolresult {_UUID}\n結果本文\n2行目"
        assert command_handler.extract_command_content(content) == (
            f"!toolresult {_UUID}\n結果本文\n2行目"
        )

    def test_uses_registry_as_source_of_truth(self, isolated_registry: None) -> None:
        """既知コマンド判定はレジストリを参照する（定数リストを持たない）。"""
        registry.register_command("newcommand")(AsyncMock())
        assert command_handler.extract_command_content("!newcommand 引数") == "!newcommand 引数"
