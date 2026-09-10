"""services/image_attachment.py のテスト。"""
from __future__ import annotations

import base64
from unittest.mock import AsyncMock, MagicMock

import pytest

from lilla_core.services import image_attachment

_ONE_MB = 1024 * 1024


def _make_attachment(
    content_type: str = "image/png",
    url: str = "https://cdn.discordapp.com/test.png",
    size: int | None = 1024,
    filename: str = "test.png",
) -> MagicMock:
    """Discord の添付ファイルを模したモックを返す。"""
    attachment = MagicMock()
    attachment.content_type = content_type
    attachment.url = url
    attachment.size = size
    attachment.filename = filename
    return attachment


def _make_config(max_mb: object = 8) -> MagicMock:
    """画像サイズ上限だけを持つ設定モックを返す。"""
    config = MagicMock()
    config.bot.max_image_attachment_size_mb = max_mb
    config.proxy.resolve_url.return_value = None
    config.env.http_proxy_user = None
    config.env.http_proxy_pass = None
    return config


class TestFilterImageAttachments:
    def test_keeps_supported_types(self) -> None:
        """対応 MIME タイプの添付だけを残す。"""
        png = _make_attachment("image/png")
        pdf = _make_attachment("application/pdf")

        assert image_attachment.filter_image_attachments([png, pdf]) == [png]

    def test_ignores_parameters_and_case(self) -> None:
        """Content-Type のパラメータと大文字小文字を無視して判定する。"""
        attachment = _make_attachment("IMAGE/JPEG; charset=binary")

        assert image_attachment.filter_image_attachments([attachment]) == [attachment]

    def test_returns_empty_for_none(self) -> None:
        """添付が None でも空リストを返す。"""
        assert image_attachment.filter_image_attachments(None) == []

    def test_ignores_missing_content_type(self) -> None:
        """content_type を持たない添付は無視する。"""
        attachment = _make_attachment(content_type=None)

        assert image_attachment.filter_image_attachments([attachment]) == []


class TestResolveMaxImageBytes:
    def test_uses_configured_value(self) -> None:
        """設定値（MB）をバイトに換算して返す。"""
        assert image_attachment.resolve_max_image_bytes(_make_config(3)) == 3 * _ONE_MB

    @pytest.mark.parametrize("value", [None, 0, -1, "8", True])
    def test_falls_back_to_default(self, value: object) -> None:
        """未設定・不正値・0 以下の場合は既定値（8MB）を使う。"""
        expected = image_attachment.DEFAULT_MAX_IMAGE_ATTACHMENT_SIZE_MB * _ONE_MB

        assert image_attachment.resolve_max_image_bytes(_make_config(value)) == expected

    def test_uses_get_config_when_omitted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """config 省略時は get_config() から解決する。"""
        monkeypatch.setattr(image_attachment, "get_config", lambda: _make_config(2))

        assert image_attachment.resolve_max_image_bytes() == 2 * _ONE_MB


class TestBuildImageContentParts:
    async def test_returns_parts_for_valid_images(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """上限内の画像は data URL の image_url パートに変換される。"""
        download = AsyncMock(return_value=b"png-bytes")
        monkeypatch.setattr(image_attachment, "download_attachment_bytes", download)
        bot = MagicMock()
        notify = AsyncMock()
        monkeypatch.setattr(image_attachment, "notify_error", notify)

        parts = await image_attachment.build_image_content_parts(
            [_make_attachment("image/png"), _make_attachment("image/jpeg", filename="a.jpg")],
            bot,
            _make_config(),
        )

        assert parts is not None
        assert len(parts) == 2
        encoded = base64.b64encode(b"png-bytes").decode("utf-8")
        assert parts[0] == {
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{encoded}"},
        }
        assert parts[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
        assert download.await_count == 2
        notify.assert_not_awaited()

    async def test_guards_before_download_when_size_exceeds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """attachment.size が上限を超える場合はダウンロードせずに弾く。"""
        download = AsyncMock()
        monkeypatch.setattr(image_attachment, "download_attachment_bytes", download)
        notify = AsyncMock()
        monkeypatch.setattr(image_attachment, "notify_error", notify)

        result = await image_attachment.build_image_content_parts(
            [_make_attachment(size=9 * _ONE_MB)], MagicMock(), _make_config(8)
        )

        assert result is None
        download.assert_not_awaited()
        notify.assert_awaited_once()
        message = notify.await_args[0][2]
        assert "8MB" in message
        assert "test.png" in message

    async def test_notifies_on_download_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ダウンロード中の例外は notify_error に流し、例外は伝播させない。"""
        error = RuntimeError("network down")
        monkeypatch.setattr(
            image_attachment, "download_attachment_bytes", AsyncMock(side_effect=error)
        )
        notify = AsyncMock()
        monkeypatch.setattr(image_attachment, "notify_error", notify)

        result = await image_attachment.build_image_content_parts(
            [_make_attachment()], MagicMock(), _make_config()
        )

        assert result is None
        notify.assert_awaited_once()
        assert notify.await_args[0][2] is error

    async def test_guards_on_actual_bytes_exceeding_limit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """size が過少申告でも、実バイト数が上限を超えていれば弾く。"""
        monkeypatch.setattr(
            image_attachment,
            "download_attachment_bytes",
            AsyncMock(return_value=b"x" * (2 * _ONE_MB)),
        )
        notify = AsyncMock()
        monkeypatch.setattr(image_attachment, "notify_error", notify)

        result = await image_attachment.build_image_content_parts(
            [_make_attachment(size=10)], MagicMock(), _make_config(1)
        )

        assert result is None
        notify.assert_awaited_once()
        assert "1MB" in notify.await_args[0][2]

    async def test_aborts_all_when_one_image_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """複数画像のうち 1 件でも失敗したら全体を None にする。"""
        download = AsyncMock(return_value=b"ok")
        monkeypatch.setattr(image_attachment, "download_attachment_bytes", download)
        monkeypatch.setattr(image_attachment, "notify_error", AsyncMock())

        result = await image_attachment.build_image_content_parts(
            [_make_attachment(), _make_attachment(size=99 * _ONE_MB, filename="big.png")],
            MagicMock(),
            _make_config(8),
        )

        assert result is None
        assert download.await_count == 1

    async def test_uses_default_limit_when_config_missing_field(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """設定に項目が無い場合でも既定の 8MB でガードされる。"""

        class _EmptyEnv:
            http_proxy_user = None
            http_proxy_pass = None

        class _EmptyProxy:
            @staticmethod
            def resolve_url():
                return None

        class _EmptyConfig:
            """`bot` セクションを持たない（＝上限が未設定の）設定オブジェクト。"""

            env = _EmptyEnv()
            proxy = _EmptyProxy()

        download = AsyncMock()
        monkeypatch.setattr(image_attachment, "download_attachment_bytes", download)
        notify = AsyncMock()
        monkeypatch.setattr(image_attachment, "notify_error", notify)

        result = await image_attachment.build_image_content_parts(
            [_make_attachment(size=9 * _ONE_MB)], MagicMock(), _EmptyConfig()
        )

        assert result is None
        download.assert_not_awaited()
        assert "8MB" in notify.await_args[0][2]
