"""commands/cleardirty.py のテスト。"""
from __future__ import annotations

import importlib
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

# 他のテストが discord をモックした状態で読み込まれていると、チャンネル解決処理が
# MagicMock の discord を掴んだままになり実例外を捕捉できない。実 discord を
# 掴み直させるため、読み込み済みの状態を捨てて import し直す
# （`from commands import cleardirty` はパッケージ属性の古いモジュールを返すため使わない）。
sys.modules.pop("lilla_core.commands.discord_util", None)
sys.modules.pop("lilla_core.commands.cleardirty", None)
cleardirty = importlib.import_module("lilla_core.commands.cleardirty")


def _make_message() -> MagicMock:
    msg = MagicMock()
    msg.reply = AsyncMock()
    return msg


def _make_http_error(error_class) -> Exception:
    """discord の HTTP 系例外インスタンスを生成する（response は最小限のモック）。"""
    return error_class(MagicMock(status=404, reason="Not Found"), "エラー")


def _patch_conversation_repo(
    monkeypatch: pytest.MonkeyPatch, record: dict | None
) -> MagicMock:
    """会話履歴リポジトリをモックに差し替え、find_latest_by_tag の戻り値を設定する。"""
    from lilla_core.repository import conversation_repository as repo_module

    repo = MagicMock()
    repo.find_latest_by_tag = AsyncMock(return_value=record)
    repo.delete = AsyncMock(return_value=True)
    monkeypatch.setattr(repo_module, "get_conversation_repo", lambda: repo)
    return repo


def _make_dirty_record(
    _id: str = "65f0000000000000000000aa",
    discord_channel_id: int | None = 555,
    discord_message_ids: list[int] | None = None,
) -> dict:
    """dirty タグ付き会話履歴ドキュメントのモックを返す。"""
    record: dict = {
        "_id": _id,
        "message": {"role": "assistant", "content": "結果です"},
        "tags": ["toolresult", "dirty"],
    }
    if discord_channel_id is not None:
        record["discord_channel_id"] = discord_channel_id
    if discord_message_ids is not None:
        record["discord_message_ids"] = discord_message_ids
    return record


def _make_bot_with_fetchable_messages(*message_ids: int) -> tuple[MagicMock, dict]:
    """fetch_message で削除可能なメッセージを返す Discord クライアントのモックを返す。"""
    messages = {}
    for mid in message_ids:
        target = MagicMock()
        target.delete = AsyncMock()
        messages[mid] = target

    channel = MagicMock()
    channel.fetch_message = AsyncMock(side_effect=lambda mid: messages[mid])
    bot = MagicMock()
    bot.get_channel = MagicMock(return_value=channel)
    return bot, messages


async def _run(msg: MagicMock, bot) -> None:
    """コマンドハンドラを呼び出す（arg / tools は未使用）。"""
    await cleardirty.handle_cleardirty(msg, "", {}, bot)


class TestHandleCleardirty:
    async def test_deletes_latest_dirty_history_and_discord_messages(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """dirty タグの最新 1 件と、対応する Discord メッセージを削除する。"""
        repo = _patch_conversation_repo(
            monkeypatch, _make_dirty_record(discord_message_ids=[1000, 1001])
        )
        bot, messages = _make_bot_with_fetchable_messages(1000, 1001)
        msg = _make_message()

        await _run(msg, bot)

        repo.find_latest_by_tag.assert_called_once_with("dirty")
        bot.get_channel.assert_called_once_with(555)
        messages[1000].delete.assert_called_once()
        messages[1001].delete.assert_called_once()
        repo.delete.assert_called_once_with("65f0000000000000000000aa")
        reply = msg.reply.call_args[0][0]
        assert "Discord 2 件" in reply

    async def test_deletes_only_one_history_entry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """1 回の実行で削除する会話履歴は 1 件だけ。"""
        repo = _patch_conversation_repo(monkeypatch, _make_dirty_record())
        bot, _messages = _make_bot_with_fetchable_messages()

        await _run(_make_message(), bot)

        assert repo.delete.call_count == 1

    async def test_replies_when_no_dirty_history(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """dirty タグの履歴がなければ削除せず案内だけ返す。"""
        repo = _patch_conversation_repo(monkeypatch, None)
        msg = _make_message()

        await _run(msg, MagicMock())

        repo.delete.assert_not_called()
        assert "見つかりません" in msg.reply.call_args[0][0]

    async def test_deletes_history_only_without_discord_info(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Discord メッセージ情報がない場合（lilla-client 経由等）は履歴のみ削除する。"""
        repo = _patch_conversation_repo(
            monkeypatch, _make_dirty_record(discord_channel_id=None)
        )
        bot = MagicMock()
        msg = _make_message()

        await _run(msg, bot)

        bot.get_channel.assert_not_called()
        repo.delete.assert_called_once_with("65f0000000000000000000aa")
        assert "Discord 0 件" in msg.reply.call_args[0][0]

    async def test_deletes_history_when_channel_unresolvable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """チャンネルを解決できない場合も履歴は削除する。"""
        import discord

        repo = _patch_conversation_repo(
            monkeypatch, _make_dirty_record(discord_message_ids=[1000])
        )
        bot = MagicMock()
        bot.get_channel = MagicMock(return_value=None)
        bot.fetch_channel = AsyncMock(side_effect=_make_http_error(discord.NotFound))
        msg = _make_message()

        await _run(msg, bot)

        repo.delete.assert_called_once_with("65f0000000000000000000aa")
        assert "Discord 0 件" in msg.reply.call_args[0][0]

    async def test_falls_back_to_fetch_channel_on_cache_miss(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """キャッシュミス時は fetch_channel で解決してメッセージを削除する。"""
        repo = _patch_conversation_repo(
            monkeypatch, _make_dirty_record(discord_message_ids=[1000])
        )
        bot, messages = _make_bot_with_fetchable_messages(1000)
        channel = bot.get_channel.return_value
        bot.get_channel = MagicMock(return_value=None)
        bot.fetch_channel = AsyncMock(return_value=channel)
        msg = _make_message()

        await _run(msg, bot)

        bot.fetch_channel.assert_called_once_with(555)
        messages[1000].delete.assert_called_once()
        repo.delete.assert_called_once_with("65f0000000000000000000aa")
        assert "Discord 1 件" in msg.reply.call_args[0][0]

    async def test_continues_when_discord_delete_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """個々の Discord メッセージ削除に失敗しても残りの削除と履歴削除は続行する。"""
        repo = _patch_conversation_repo(
            monkeypatch, _make_dirty_record(discord_message_ids=[1000, 1001])
        )
        bot, messages = _make_bot_with_fetchable_messages(1000, 1001)
        messages[1000].delete = AsyncMock(side_effect=Exception("not found"))
        msg = _make_message()

        await _run(msg, bot)

        messages[1001].delete.assert_called_once()
        repo.delete.assert_called_once_with("65f0000000000000000000aa")
        assert "Discord 1 件" in msg.reply.call_args[0][0]
