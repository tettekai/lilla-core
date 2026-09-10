"""commands/mongodata.py のテスト。"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from lilla_core.commands import attachment_body, mongodata


def _make_message(attachments: list | None = None) -> MagicMock:
    msg = MagicMock()
    msg.reply = AsyncMock()
    msg.attachments = attachments if attachments is not None else []
    return msg


def _make_attachment(
    filename: str = "body.json",
    content_type: str | None = "application/json",
    size: int = 100,
) -> MagicMock:
    """Discord の添付ファイルを模したモックを返す。"""
    attachment = MagicMock()
    attachment.filename = filename
    attachment.content_type = content_type
    attachment.size = size
    attachment.url = f"https://cdn.discordapp.com/{filename}"
    return attachment


def _make_config(allowed=None) -> MagicMock:
    """mongodata 用の設定モックを返す。"""
    config = MagicMock()
    config.commands.mongodata.allowed_collections = allowed if allowed is not None else ["asken_daily"]
    config.env.mongodb_uri = "mongodb://localhost:27017"
    config.mongodb.db_name = "lilla"
    return config


def _patch_mongo(monkeypatch: pytest.MonkeyPatch, config: MagicMock):
    """get_config と create_motor_client をパッチし、collection モックを返す。"""
    from lilla_core.core import config as core_config

    monkeypatch.setattr(core_config, "get_config", lambda: config)

    col = MagicMock()
    col.update_one = AsyncMock()
    col.insert_one = AsyncMock()

    client = MagicMock()
    client.__getitem__.return_value.__getitem__.return_value = col

    from lilla_core.repository import motor_client as motor_client_module

    monkeypatch.setattr(motor_client_module, "create_motor_client", lambda *a, **kw: client)
    return col


@pytest.fixture(autouse=True)
def mock_notify_error(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """エラー通知（ERROR ログ + error_channel）をモックに差し替える。

    BODY 解決（`commands.attachment_body`）側の通知も同じモックへ集約する。
    """
    mock = AsyncMock()
    monkeypatch.setattr(mongodata, "notify_error", mock)
    monkeypatch.setattr(attachment_body, "notify_error", mock)
    return mock


@pytest.fixture
def mock_download(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """添付ファイルのダウンロードをモックに差し替える。"""
    monkeypatch.setattr(attachment_body, "resolve_proxy_settings", lambda: (None, None))
    mock = AsyncMock(return_value=b"")
    monkeypatch.setattr(attachment_body, "download_attachment_bytes", mock)
    return mock


def _notified_text(mock_notify_error: AsyncMock) -> str:
    """notify_error に渡された「コンテキスト: エラー内容」を連結して返す。"""
    context, error = mock_notify_error.call_args[0][1:3]
    return f"{context}: {error}"


async def _run(msg: MagicMock, arg: str, bot: MagicMock | None = None) -> None:
    """コマンドハンドラを呼び出す（未使用の tools はダミー）。"""
    await mongodata.handle_mongodata(msg, arg, {}, bot or MagicMock())


class TestHandleMongodata:
    async def test_invalid_json_notifies_error(
        self, monkeypatch: pytest.MonkeyPatch, mock_notify_error: AsyncMock
    ) -> None:
        _patch_mongo(monkeypatch, _make_config())
        msg = _make_message()
        await _run(msg, "{invalid")
        mock_notify_error.assert_called_once()
        assert "パース" in _notified_text(mock_notify_error)
        msg.reply.assert_not_called()

    async def test_empty_arg_notifies_error(
        self, monkeypatch: pytest.MonkeyPatch, mock_notify_error: AsyncMock
    ) -> None:
        """本文にも添付にも JSON が無い場合は BODY 未検出として通知する。"""
        _patch_mongo(monkeypatch, _make_config())
        msg = _make_message()
        await _run(msg, "")
        mock_notify_error.assert_called_once()
        assert "BODY" in _notified_text(mock_notify_error)
        msg.reply.assert_not_called()

    async def test_missing_collection_notifies_error(
        self, monkeypatch: pytest.MonkeyPatch, mock_notify_error: AsyncMock
    ) -> None:
        _patch_mongo(monkeypatch, _make_config())
        msg = _make_message()
        await _run(msg, '{"payload": {}}')
        mock_notify_error.assert_called_once()
        assert "collection" in _notified_text(mock_notify_error)
        msg.reply.assert_not_called()

    async def test_disallowed_collection_notifies_error(
        self, monkeypatch: pytest.MonkeyPatch, mock_notify_error: AsyncMock
    ) -> None:
        _patch_mongo(monkeypatch, _make_config(allowed=["other"]))
        msg = _make_message()
        await _run(msg, '{"collection": "asken_daily", "payload": {}}')
        mock_notify_error.assert_called_once()
        assert "許可されていない" in _notified_text(mock_notify_error)
        msg.reply.assert_not_called()

    async def test_invalid_payload_notifies_error(
        self, monkeypatch: pytest.MonkeyPatch, mock_notify_error: AsyncMock
    ) -> None:
        _patch_mongo(monkeypatch, _make_config())
        msg = _make_message()
        await _run(msg, '{"collection": "asken_daily", "payload": "notdict"}')
        mock_notify_error.assert_called_once()
        assert "payload" in _notified_text(mock_notify_error)
        msg.reply.assert_not_called()

    async def test_insert_without_key_no_reply(
        self, monkeypatch: pytest.MonkeyPatch, mock_notify_error: AsyncMock
    ) -> None:
        col = _patch_mongo(monkeypatch, _make_config())
        msg = _make_message()
        await _run(msg, '{"collection": "asken_daily", "payload": {"a": 1}}')
        col.insert_one.assert_called_once_with({"a": 1})
        col.update_one.assert_not_called()
        msg.reply.assert_not_called()
        mock_notify_error.assert_not_called()

    async def test_json_on_next_line_no_reply(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """JSON がコマンド名の次の行にある場合も受け付ける。"""
        col = _patch_mongo(monkeypatch, _make_config())
        msg = _make_message()
        await _run(msg, '\n{"collection": "asken_daily", "payload": {"a": 1}}')
        col.insert_one.assert_called_once_with({"a": 1})
        msg.reply.assert_not_called()

    async def test_trailing_text_after_json_no_reply(self, monkeypatch: pytest.MonkeyPatch) -> None:
        col = _patch_mongo(monkeypatch, _make_config())
        msg = _make_message()
        await _run(
            msg,
            '{"collection": "asken_daily", "payload": {"a": 1}}\n\n'
            'To stop or manage this job, send me a new message '
            '(e.g. "stop reminder askendaily").',
        )
        col.insert_one.assert_called_once_with({"a": 1})
        msg.reply.assert_not_called()

    async def test_upsert_with_key_no_reply(self, monkeypatch: pytest.MonkeyPatch) -> None:
        col = _patch_mongo(monkeypatch, _make_config())
        msg = _make_message()
        await _run(
            msg,
            '{"collection": "asken_daily", "key": "date", '
            '"payload": {"date": "2026-05-27", "v": 1}}',
        )
        col.update_one.assert_called_once_with(
            {"date": "2026-05-27"},
            {"$set": {"date": "2026-05-27", "v": 1}},
            upsert=True,
        )
        col.insert_one.assert_not_called()
        msg.reply.assert_not_called()

    async def test_key_missing_in_payload_notifies_error(
        self, monkeypatch: pytest.MonkeyPatch, mock_notify_error: AsyncMock
    ) -> None:
        col = _patch_mongo(monkeypatch, _make_config())
        msg = _make_message()
        await _run(msg, '{"collection": "asken_daily", "key": "date", "payload": {"v": 1}}')
        mock_notify_error.assert_called_once()
        assert "date" in _notified_text(mock_notify_error)
        msg.reply.assert_not_called()
        col.update_one.assert_not_called()

    async def test_id_field_is_stripped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        col = _patch_mongo(monkeypatch, _make_config())
        msg = _make_message()
        await _run(msg, '{"collection": "asken_daily", "payload": {"_id": "x", "a": 1}}')
        col.insert_one.assert_called_once_with({"a": 1})
        msg.reply.assert_not_called()

    async def test_db_error_notifies_error(
        self, monkeypatch: pytest.MonkeyPatch, mock_notify_error: AsyncMock
    ) -> None:
        col = _patch_mongo(monkeypatch, _make_config())
        col.insert_one = AsyncMock(side_effect=Exception("boom"))
        msg = _make_message()
        await _run(msg, '{"collection": "asken_daily", "payload": {"a": 1}}')
        mock_notify_error.assert_called_once()
        assert "DB操作" in _notified_text(mock_notify_error)
        msg.reply.assert_not_called()

    async def test_insert_from_attachment_body(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_download: AsyncMock,
        mock_notify_error: AsyncMock,
    ) -> None:
        """本文に JSON が無くても、添付ファイルの JSON で登録できる。"""
        col = _patch_mongo(monkeypatch, _make_config())
        mock_download.return_value = (
            '{"collection": "asken_daily", "payload": {"a": 1}}'.encode()
        )
        msg = _make_message([_make_attachment()])

        await _run(msg, "")

        col.insert_one.assert_called_once_with({"a": 1})
        mock_notify_error.assert_not_called()

    async def test_attachment_takes_precedence_over_text(
        self, monkeypatch: pytest.MonkeyPatch, mock_download: AsyncMock
    ) -> None:
        """本文と添付の両方に JSON がある場合は添付が使われる。"""
        col = _patch_mongo(monkeypatch, _make_config())
        mock_download.return_value = (
            '{"collection": "asken_daily", "payload": {"from": "attachment"}}'.encode()
        )
        msg = _make_message([_make_attachment()])

        await _run(msg, '{"collection": "asken_daily", "payload": {"from": "text"}}')

        col.insert_one.assert_called_once_with({"from": "attachment"})

    async def test_invalid_json_in_attachment_notifies_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_download: AsyncMock,
        mock_notify_error: AsyncMock,
    ) -> None:
        """添付ファイルの中身が JSON でない場合はパースエラーとして通知する。"""
        col = _patch_mongo(monkeypatch, _make_config())
        mock_download.return_value = b"{invalid"
        msg = _make_message([_make_attachment()])

        await _run(msg, "")

        assert "パース" in _notified_text(mock_notify_error)
        col.insert_one.assert_not_called()

    async def test_non_text_attachment_notifies_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_download: AsyncMock,
        mock_notify_error: AsyncMock,
    ) -> None:
        """テキストとして読めない添付は通知して登録しない。"""
        col = _patch_mongo(monkeypatch, _make_config())
        msg = _make_message([_make_attachment(filename="photo.png", content_type="image/png")])

        await _run(msg, "")

        assert "テキストとして読み取れない" in _notified_text(mock_notify_error)
        mock_download.assert_not_called()
        col.insert_one.assert_not_called()

    async def test_oversized_attachment_notifies_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_download: AsyncMock,
        mock_notify_error: AsyncMock,
    ) -> None:
        """1MB を超える添付は通知して登録しない。"""
        col = _patch_mongo(monkeypatch, _make_config())
        msg = _make_message([
            _make_attachment(size=attachment_body.MAX_ATTACHMENT_SIZE + 1)
        ])

        await _run(msg, "")

        assert "大きすぎます" in _notified_text(mock_notify_error)
        col.insert_one.assert_not_called()

    async def test_non_utf8_attachment_notifies_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_download: AsyncMock,
        mock_notify_error: AsyncMock,
    ) -> None:
        """UTF-8 として読めない添付は通知して登録しない。"""
        col = _patch_mongo(monkeypatch, _make_config())
        mock_download.return_value = "あ".encode("cp932")
        msg = _make_message([_make_attachment()])

        await _run(msg, "")

        assert "UTF-8" in _notified_text(mock_notify_error)
        col.insert_one.assert_not_called()

    async def test_passes_bot_to_notify_error(
        self, monkeypatch: pytest.MonkeyPatch, mock_notify_error: AsyncMock
    ) -> None:
        """通知先の解決に使う bot がそのまま notify_error へ渡る。"""
        _patch_mongo(monkeypatch, _make_config())
        bot = MagicMock()
        await _run(_make_message(), "{invalid", bot=bot)
        assert mock_notify_error.call_args[0][0] is bot
