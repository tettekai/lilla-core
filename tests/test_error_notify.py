"""core/error_notify.py のテスト。"""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from lilla_core.core import error_notify


def _make_bot(channel_name: str | None = "error-log") -> tuple[MagicMock, MagicMock]:
    """エラーチャンネルを 1 つ持つ bot モックと、そのチャンネルモックを返す。"""
    channel = MagicMock()
    channel.name = channel_name
    channel.send = AsyncMock()
    bot = MagicMock()
    bot.get_all_channels.return_value = [channel]
    return bot, channel


def _patch_config(monkeypatch: pytest.MonkeyPatch, error_channel: str | None) -> None:
    """error_notify が関数内で import する get_config を差し替える。"""
    from lilla_core.core import config as core_config

    config = MagicMock()
    config.discord.error_channel = error_channel
    monkeypatch.setattr(core_config, "get_config", lambda: config)


class TestNotifyError:
    async def test_sends_to_error_channel(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """error_channel にコンテキストとエラー内容を連結して送信する。"""
        _patch_config(monkeypatch, "error-log")
        bot, channel = _make_bot()

        await error_notify.notify_error(bot, "テスト処理エラー", "boom")

        channel.send.assert_called_once_with("テスト処理エラー: boom")

    async def test_logs_error(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """ERROR ログにコンテキストとエラー内容を出力する。"""
        _patch_config(monkeypatch, "error-log")
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
        _patch_config(monkeypatch, "error-log")
        bot, channel = _make_bot()

        with caplog.at_level(logging.ERROR, logger="lilla_core.core.error_notify"):
            await error_notify.notify_error(bot, "DB操作エラー", ValueError("boom"))

        record = next(r for r in caplog.records if r.levelno == logging.ERROR)
        assert record.exc_info is not None
        channel.send.assert_called_once_with("DB操作エラー: boom")

    async def test_no_error_channel_configured_only_warns(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """error_channel 未設定なら WARNING ログのみでクラッシュしない。"""
        _patch_config(monkeypatch, None)
        bot, channel = _make_bot()

        with caplog.at_level(logging.WARNING, logger="lilla_core.core.error_notify"):
            await error_notify.notify_error(bot, "テスト処理エラー", "boom")

        channel.send.assert_not_called()
        assert any(r.levelno == logging.WARNING for r in caplog.records)

    async def test_channel_not_found_only_warns(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """設定されたチャンネルが見つからない場合も WARNING ログのみ。"""
        _patch_config(monkeypatch, "error-log")
        bot, channel = _make_bot(channel_name="other-channel")

        with caplog.at_level(logging.WARNING, logger="lilla_core.core.error_notify"):
            await error_notify.notify_error(bot, "テスト処理エラー", "boom")

        channel.send.assert_not_called()
        assert any("Error channel not found" in r.message for r in caplog.records)

    async def test_bot_is_none_only_warns(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """bot が None でも例外を投げない。"""
        _patch_config(monkeypatch, "error-log")

        with caplog.at_level(logging.WARNING, logger="lilla_core.core.error_notify"):
            await error_notify.notify_error(None, "テスト処理エラー", "boom")

        assert any(r.levelno == logging.WARNING for r in caplog.records)

    async def test_send_failure_is_swallowed(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Discord 送信に失敗しても例外を投げず WARNING ログに留める。"""
        _patch_config(monkeypatch, "error-log")
        bot, channel = _make_bot()
        channel.send = AsyncMock(side_effect=Exception("send failed"))

        with caplog.at_level(logging.WARNING, logger="lilla_core.core.error_notify"):
            await error_notify.notify_error(bot, "テスト処理エラー", "boom")

        assert any("Failed to notify error channel" in r.message for r in caplog.records)
