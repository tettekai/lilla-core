"""services/attachment_download.py のテスト。"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from lilla_core.services import attachment_download

# テスト用のダミープロキシパスワード
_DUMMY_PROXY_PASS = "dummy-pass"  # noqa: S105


def _make_session_mock(read_bytes: bytes = b"image-data") -> tuple[MagicMock, MagicMock]:
    """aiohttp.ClientSession を模したモックと、その session モックを返す。"""
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.read = AsyncMock(return_value=read_bytes)

    resp_cm = AsyncMock()
    resp_cm.__aenter__ = AsyncMock(return_value=resp)
    resp_cm.__aexit__ = AsyncMock(return_value=False)

    session = MagicMock()
    session.get.return_value = resp_cm

    session_cm = AsyncMock()
    session_cm.__aenter__ = AsyncMock(return_value=session)
    session_cm.__aexit__ = AsyncMock(return_value=False)

    return MagicMock(return_value=session_cm), session


class TestNormalizeContentType:
    def test_lowercases_and_strips_parameters(self) -> None:
        """パラメータ付き・大文字混じりの Content-Type を正規化する。"""
        attachment = MagicMock()
        attachment.content_type = "Image/PNG; charset=binary"

        assert attachment_download.normalize_content_type(attachment) == "image/png"

    def test_returns_bare_media_type_as_is(self) -> None:
        """パラメータの無い Content-Type はそのまま小文字で返す。"""
        attachment = MagicMock()
        attachment.content_type = "application/json"

        assert attachment_download.normalize_content_type(attachment) == "application/json"

    def test_returns_empty_string_when_missing(self) -> None:
        """content_type を持たない添付は空文字列を返す。"""
        attachment = MagicMock()
        attachment.content_type = None

        assert attachment_download.normalize_content_type(attachment) == ""

    def test_returns_empty_string_when_not_str(self) -> None:
        """content_type が文字列でない場合も空文字列を返す。"""
        attachment = MagicMock()
        attachment.content_type = 123

        assert attachment_download.normalize_content_type(attachment) == ""


class TestDownloadAttachmentBytes:
    async def test_returns_bytes_from_response(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """正常レスポンスのとき bytes を返す。"""
        client_session, session = _make_session_mock()
        monkeypatch.setattr(attachment_download.aiohttp, "ClientSession", client_session)

        result = await attachment_download.download_attachment_bytes(
            "http://example.com/img.png", None, None
        )

        assert result == b"image-data"
        session.get.assert_called_once_with(
            "http://example.com/img.png", proxy=None, proxy_auth=None
        )

    async def test_passes_proxy_settings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """プロキシとプロキシ認証情報がそのまま aiohttp へ渡る。"""
        client_session, session = _make_session_mock()
        monkeypatch.setattr(attachment_download.aiohttp, "ClientSession", client_session)

        auth = attachment_download.aiohttp.BasicAuth("user", "pass")
        await attachment_download.download_attachment_bytes(
            "http://example.com/img.png", "http://proxy:3128", auth
        )

        session.get.assert_called_once_with(
            "http://example.com/img.png", proxy="http://proxy:3128", proxy_auth=auth
        )


class TestResolveProxySettings:
    def _patch_config(self, monkeypatch: pytest.MonkeyPatch, **attrs: object) -> None:
        """attachment_download が参照する設定を差し替える。"""
        config = MagicMock()
        config.proxy.resolve_url.return_value = attrs.get("proxy")
        config.env.http_proxy_user = attrs.get("http_proxy_user")
        config.env.http_proxy_pass = attrs.get("http_proxy_pass")
        monkeypatch.setattr(attachment_download, "get_config", lambda: config)

    def test_returns_proxy_with_auth(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ユーザーとパスワードが揃っていれば BasicAuth を組み立てる。"""
        self._patch_config(
            monkeypatch,
            proxy="http://proxy:3128",
            http_proxy_user="user",
            http_proxy_pass=_DUMMY_PROXY_PASS,
        )

        proxy, auth = attachment_download.resolve_proxy_settings()

        assert proxy == "http://proxy:3128"
        assert auth == attachment_download.aiohttp.BasicAuth("user", _DUMMY_PROXY_PASS)

    def test_returns_none_auth_without_credentials(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """認証情報が無い場合は proxy のみを返す。"""
        self._patch_config(monkeypatch, proxy="http://proxy:3128")

        proxy, auth = attachment_download.resolve_proxy_settings()

        assert proxy == "http://proxy:3128"
        assert auth is None

    def test_returns_none_without_proxy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """プロキシ未設定の場合は (None, None) を返す。"""
        self._patch_config(monkeypatch)

        assert attachment_download.resolve_proxy_settings() == (None, None)
