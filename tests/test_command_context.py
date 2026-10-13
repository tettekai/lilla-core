"""commands/context.py のテスト（コマンドコンテキストの基底クラスと実行結果）。"""
from __future__ import annotations

import pytest

from lilla_core.commands.context import (
    COMMAND_STATUS_ERROR,
    COMMAND_STATUS_OK,
    DISCORD_CLIENT_TYPE,
    CommandContext,
    CommandResult,
)


class _RecordingContext(CommandContext):
    """送信内容を記録するだけのテスト用コンテキスト。"""

    def __init__(self, **kwargs) -> None:
        kwargs.setdefault("client_type", "test-client")
        super().__init__(**kwargs)
        self.sent: list[tuple[str, str]] = []

    async def deliver_reply(self, text: str) -> None:
        self.sent.append(("reply", text))

    async def deliver_error(self, text: str) -> None:
        self.sent.append(("error", text))

    async def deliver_conversation(self, content: str) -> None:
        self.sent.append(("conversation", content))


class TestCommandContext:
    def test_defaults(self) -> None:
        """既定ではオーナー扱いせず、添付は空で、Discord のメッセージを持たない。"""
        ctx = _RecordingContext()

        assert ctx.client_type == "test-client"
        assert ctx.author_id is None
        assert ctx.is_owner is False
        assert ctx.attachments == []
        assert ctx.discord_message is None

    def test_attachments_are_copied(self) -> None:
        """渡した添付のリストはコピーして持つ。"""
        attachments = ["a"]
        ctx = _RecordingContext(attachments=attachments)
        attachments.append("b")

        assert ctx.attachments == ["a"]

    def test_untouched_context_is_ok(self) -> None:
        """何も呼ばなければ ok で、返信も会話も無い。"""
        assert _RecordingContext().build_result() == CommandResult(status=COMMAND_STATUS_OK)

    async def test_reply_is_recorded_as_ok(self) -> None:
        """`reply` は実装へ委ね、結果は ok のまま返信ありになる。"""
        ctx = _RecordingContext()

        await ctx.reply("結果")

        assert ctx.sent == [("reply", "結果")]
        result = ctx.build_result()
        assert result.status == COMMAND_STATUS_OK
        assert result.replied is True
        assert result.ok is True

    async def test_reply_error_marks_error(self) -> None:
        """`reply_error` は実装へ委ね、結果を error にする。"""
        ctx = _RecordingContext()

        await ctx.reply_error("入力ミス")

        assert ctx.sent == [("error", "入力ミス")]
        result = ctx.build_result()
        assert result.status == COMMAND_STATUS_ERROR
        assert result.replied is True
        assert result.ok is False

    async def test_start_conversation_is_recorded(self) -> None:
        """`start_conversation` は実装へ委ね、会話開始ありを記録する。"""
        ctx = _RecordingContext()

        await ctx.start_conversation("内容")

        assert ctx.sent == [("conversation", "内容")]
        result = ctx.build_result()
        assert result.conversation_started is True
        assert result.replied is False

    def test_mark_failed_without_reply(self) -> None:
        """`mark_failed` は何も送らずに結果を error にする。"""
        ctx = _RecordingContext()

        ctx.mark_failed()

        assert ctx.sent == []
        assert ctx.build_result() == CommandResult(status=COMMAND_STATUS_ERROR)

    def test_cannot_instantiate_without_implementation(self) -> None:
        """送信本体を実装しないサブクラスは作れない。"""

        class Incomplete(CommandContext):
            pass

        with pytest.raises(TypeError):
            Incomplete(client_type="x")

    def test_discord_client_type(self) -> None:
        """Discord の `client_type` は会話側と同じ `discord`。"""
        assert DISCORD_CLIENT_TYPE == "discord"
