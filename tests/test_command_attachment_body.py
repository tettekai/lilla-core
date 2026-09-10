"""commands/attachment_body.py のテスト。"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from lilla_core.commands import attachment_body


def _make_attachment(
    filename: str = "body.json",
    content_type: str | None = "application/json",
    size: int = 10,
    url: str = "https://cdn.discordapp.com/body.json",
) -> MagicMock:
    """Discord の添付ファイルを模したモックを返す。"""
    attachment = MagicMock()
    attachment.filename = filename
    attachment.content_type = content_type
    attachment.size = size
    attachment.url = url
    return attachment


def _make_message(attachments: list | None = None) -> MagicMock:
    """添付ファイルつき（または無し）の Discord メッセージモックを返す。"""
    message = MagicMock()
    message.attachments = attachments if attachments is not None else []
    return message


@pytest.fixture(autouse=True)
def mock_notify_error(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """エラー通知をモックに差し替える。"""
    mock = AsyncMock()
    monkeypatch.setattr(attachment_body, "notify_error", mock)
    return mock


@pytest.fixture(autouse=True)
def mock_proxy_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """プロキシ解決が実設定を読まないようにする。"""
    monkeypatch.setattr(
        attachment_body, "resolve_proxy_settings", lambda: ("http://proxy:3128", None)
    )


@pytest.fixture
def mock_download(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """添付ダウンロードをモックに差し替える（既定は UTF-8 の JSON を返す）。"""
    mock = AsyncMock(return_value='{"a": 1}'.encode())
    monkeypatch.setattr(attachment_body, "download_attachment_bytes", mock)
    return mock


def _notified_text(mock_notify_error: AsyncMock) -> str:
    """notify_error に渡された「コンテキスト: エラー内容」を連結して返す。"""
    context, error = mock_notify_error.call_args[0][1:3]
    return f"{context}: {error}"


class TestResolveCommandBody:
    async def test_uses_text_body_without_attachment(
        self, mock_notify_error: AsyncMock
    ) -> None:
        """添付が無ければテキスト側の BODY をそのまま返す。"""
        result = await attachment_body.resolve_command_body(
            _make_message(), '{"a": 1}', MagicMock(), "!mongodata"
        )

        assert result == '{"a": 1}'
        mock_notify_error.assert_not_called()

    async def test_uses_attachment_when_present(
        self, mock_download: AsyncMock, mock_notify_error: AsyncMock
    ) -> None:
        """添付があればその内容を BODY として返す。"""
        message = _make_message([_make_attachment()])

        result = await attachment_body.resolve_command_body(
            message, "", MagicMock(), "!mongodata"
        )

        assert result == '{"a": 1}'
        mock_notify_error.assert_not_called()

    async def test_attachment_takes_precedence_over_text(
        self, mock_download: AsyncMock
    ) -> None:
        """テキストと添付の両方に BODY がある場合は添付が優先される。"""
        message = _make_message([_make_attachment()])

        result = await attachment_body.resolve_command_body(
            message, '{"text": true}', MagicMock(), "!mongodata"
        )

        assert result == '{"a": 1}'

    async def test_uses_only_first_attachment(self, mock_download: AsyncMock) -> None:
        """添付が複数ある場合は先頭の 1 つだけをダウンロードする。"""
        message = _make_message([
            _make_attachment(url="https://cdn.discordapp.com/first.json"),
            _make_attachment(url="https://cdn.discordapp.com/second.json"),
        ])

        await attachment_body.resolve_command_body(message, "", MagicMock(), "!mongodata")

        mock_download.assert_called_once()
        assert mock_download.call_args[0][0] == "https://cdn.discordapp.com/first.json"

    async def test_passes_proxy_settings_to_download(self, mock_download: AsyncMock) -> None:
        """ダウンロードにはプロキシ設定が渡される。"""
        message = _make_message([_make_attachment()])

        await attachment_body.resolve_command_body(message, "", MagicMock(), "!mongodata")

        assert mock_download.call_args[0][1] == "http://proxy:3128"

    async def test_notifies_when_no_body_anywhere(self, mock_notify_error: AsyncMock) -> None:
        """テキストにも添付にも BODY が無ければ通知して None を返す。"""
        bot = MagicMock()

        result = await attachment_body.resolve_command_body(
            _make_message(), "", bot, "!mongodata"
        )

        assert result is None
        mock_notify_error.assert_called_once()
        assert mock_notify_error.call_args[0][0] is bot
        assert "BODY" in _notified_text(mock_notify_error)

    async def test_notifies_when_text_body_is_whitespace_only(
        self, mock_notify_error: AsyncMock
    ) -> None:
        """空白のみのテキスト BODY は「BODY 無し」として扱う。"""
        result = await attachment_body.resolve_command_body(
            _make_message(), "   \n  ", MagicMock(), "!mongodata"
        )

        assert result is None
        assert "BODY" in _notified_text(mock_notify_error)

    async def test_notifies_for_non_text_attachment(
        self, mock_download: AsyncMock, mock_notify_error: AsyncMock
    ) -> None:
        """テキストとして読めない添付は通知して None を返す。"""
        message = _make_message([
            _make_attachment(filename="photo.png", content_type="image/png")
        ])

        result = await attachment_body.resolve_command_body(
            message, "", MagicMock(), "!mongodata"
        )

        assert result is None
        mock_download.assert_not_called()
        assert "テキストとして読み取れない" in _notified_text(mock_notify_error)

    async def test_notifies_when_attachment_too_large(
        self, mock_download: AsyncMock, mock_notify_error: AsyncMock
    ) -> None:
        """サイズが上限（1MB）を超える添付はダウンロードせず通知する。"""
        message = _make_message([
            _make_attachment(size=attachment_body.MAX_ATTACHMENT_SIZE + 1)
        ])

        result = await attachment_body.resolve_command_body(
            message, "", MagicMock(), "!mongodata"
        )

        assert result is None
        mock_download.assert_not_called()
        assert "大きすぎます" in _notified_text(mock_notify_error)

    async def test_notifies_when_downloaded_body_too_large(
        self, mock_download: AsyncMock, mock_notify_error: AsyncMock
    ) -> None:
        """size が実際より小さく申告されていても、取得後のサイズ超過を検出する。"""
        mock_download.return_value = b"x" * (attachment_body.MAX_ATTACHMENT_SIZE + 1)
        message = _make_message([_make_attachment(size=10)])

        result = await attachment_body.resolve_command_body(
            message, "", MagicMock(), "!mongodata"
        )

        assert result is None
        assert "大きすぎます" in _notified_text(mock_notify_error)

    async def test_notifies_when_not_utf8(
        self, mock_download: AsyncMock, mock_notify_error: AsyncMock
    ) -> None:
        """UTF-8 としてデコードできない添付は通知して None を返す（置換はしない）。"""
        mock_download.return_value = "結果です".encode("cp932")
        message = _make_message([_make_attachment(filename="body.txt", content_type="text/plain")])

        result = await attachment_body.resolve_command_body(
            message, "", MagicMock(), "!toolresult"
        )

        assert result is None
        assert "UTF-8" in _notified_text(mock_notify_error)

    async def test_notifies_when_download_fails(
        self, mock_download: AsyncMock, mock_notify_error: AsyncMock
    ) -> None:
        """ダウンロード失敗は例外を投げずに通知して None を返す。"""
        mock_download.side_effect = Exception("boom")
        message = _make_message([_make_attachment()])

        result = await attachment_body.resolve_command_body(
            message, "", MagicMock(), "!toolresult"
        )

        assert result is None
        assert "取得エラー" in _notified_text(mock_notify_error)

    async def test_accepts_txt_extension_with_unknown_content_type(
        self, mock_download: AsyncMock
    ) -> None:
        """Content-Type が不明でも拡張子が .txt なら受け付ける。"""
        mock_download.return_value = "結果本文".encode()
        message = _make_message([
            _make_attachment(filename="result.txt", content_type=None)
        ])

        result = await attachment_body.resolve_command_body(
            message, "", MagicMock(), "!toolresult"
        )

        assert result == "結果本文"

    async def test_accepts_text_content_type_with_unknown_extension(
        self, mock_download: AsyncMock
    ) -> None:
        """拡張子が対象外でも Content-Type が text/* なら受け付ける。"""
        mock_download.return_value = "結果本文".encode()
        message = _make_message([
            _make_attachment(filename="result.md", content_type="text/markdown; charset=utf-8")
        ])

        result = await attachment_body.resolve_command_body(
            message, "", MagicMock(), "!toolresult"
        )

        assert result == "結果本文"

    async def test_handles_message_without_attachments_attribute(self) -> None:
        """attachments を持たないメッセージでもテキスト BODY を返す。"""
        message = object()

        result = await attachment_body.resolve_command_body(
            message, "本文", MagicMock(), "!toolresult"
        )

        assert result == "本文"
