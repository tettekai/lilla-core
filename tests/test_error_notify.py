"""core/error_notify.py のテスト。"""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from lilla_core.core import error_notify


def _make_bot(channel_id: int | None = 111) -> tuple[MagicMock, MagicMock]:
    """エラーチャンネルを ID で解決できる bot モックと、そのチャンネルモックを返す。"""
    channel = MagicMock()
    channel.send = AsyncMock()
    bot = MagicMock()
    bot.get_channel.side_effect = lambda cid: channel if cid == channel_id else None
    return bot, channel


def _patch_config(monkeypatch: pytest.MonkeyPatch, error_channel_id: str | None) -> None:
    """error_notify が関数内で import する get_config を差し替える。"""
    from lilla_core.core import config as core_config

    config = MagicMock()
    config.discord.error_channel_id = error_channel_id
    monkeypatch.setattr(core_config, "get_config", lambda: config)


class TestNotifyError:
    async def test_sends_to_error_channel(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """error_channel_id にコンテキストとエラー内容を連結して送信する。"""
        _patch_config(monkeypatch, "111")
        bot, channel = _make_bot()

        await error_notify.notify_error(bot, "テスト処理エラー", "boom")

        channel.send.assert_called_once_with("テスト処理エラー: boom")

    async def test_logs_error(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """ERROR ログにコンテキストとエラー内容を出力する。"""
        _patch_config(monkeypatch, "111")
        bot, _ = _make_bot()

        with caplog.at_level(logging.ERROR, logger="lilla_core.core.error_notify"):
            await error_notify.notify_error(bot, "テスト処理エラー", "boom")

        assert any(
            r.levelno == logging.ERROR and "テスト処理エラー: boom" in r.getMessage()
            for r in caplog.records
        )

    async def test_exception_logs_traceback(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """例外を渡した場合はトレースバックもログに残す。"""
        _patch_config(monkeypatch, "111")
        bot, channel = _make_bot()

        with caplog.at_level(logging.ERROR, logger="lilla_core.core.error_notify"):
            await error_notify.notify_error(bot, "DB操作エラー", ValueError("boom"))

        record = next(r for r in caplog.records if r.levelno == logging.ERROR)
        assert record.exc_info is not None
        channel.send.assert_called_once_with("DB操作エラー: boom")

    async def test_no_error_channel_configured_only_warns(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """error_channel_id 未設定なら WARNING ログのみでクラッシュしない。"""
        _patch_config(monkeypatch, None)
        bot, channel = _make_bot()

        with caplog.at_level(logging.WARNING, logger="lilla_core.core.error_notify"):
            await error_notify.notify_error(bot, "テスト処理エラー", "boom")

        channel.send.assert_not_called()
        assert any(r.levelno == logging.WARNING for r in caplog.records)

    async def test_channel_not_found_only_warns(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """設定された ID のチャンネルが見つからない場合も WARNING ログのみ。"""
        _patch_config(monkeypatch, "111")
        bot, channel = _make_bot(channel_id=222)

        with caplog.at_level(logging.WARNING, logger="lilla_core.core.error_notify"):
            await error_notify.notify_error(bot, "テスト処理エラー", "boom")

        channel.send.assert_not_called()
        assert any("Error channel not found" in r.message for r in caplog.records)

    async def test_invalid_channel_id_only_warns(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """error_channel_id が整数に変換できない場合も WARNING ログのみ。"""
        _patch_config(monkeypatch, "not-a-number")
        bot, channel = _make_bot()

        with caplog.at_level(logging.WARNING, logger="lilla_core.core.error_notify"):
            await error_notify.notify_error(bot, "テスト処理エラー", "boom")

        channel.send.assert_not_called()
        assert any("Error channel ID is invalid" in r.message for r in caplog.records)

    async def test_bot_is_none_only_warns(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """bot が None でも例外を投げない。"""
        _patch_config(monkeypatch, "111")

        with caplog.at_level(logging.WARNING, logger="lilla_core.core.error_notify"):
            await error_notify.notify_error(None, "テスト処理エラー", "boom")

        assert any(r.levelno == logging.WARNING for r in caplog.records)

    async def test_send_failure_is_swallowed(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Discord 送信に失敗しても例外を投げず WARNING ログに留める。"""
        _patch_config(monkeypatch, "111")
        bot, channel = _make_bot()
        channel.send = AsyncMock(side_effect=Exception("send failed"))

        with caplog.at_level(logging.WARNING, logger="lilla_core.core.error_notify"):
            await error_notify.notify_error(bot, "テスト処理エラー", "boom")

        assert any("Failed to notify error channel" in r.message for r in caplog.records)
