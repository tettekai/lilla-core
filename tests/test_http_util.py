"""http_util.py のテスト。
aiohttp の実際の通信はモック、環境変数は .env を読まずテスト用の値を使う。
"""
from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest


@pytest.fixture
def with_mocked_modules():
    """依存モジュールを patch.dict で差し替える。"""
    # aiohttp は conftest が実モジュールを先にロードする。
    # ここは lilla_core.core.config だけ差し替え、ClientResponseError は本物を使う。
    with patch.dict(
        sys.modules,
        {"lilla_core.core.config": MagicMock(get_config=lambda: MagicMock())},
    ):
        yield


@pytest.fixture
def http_util(with_mocked_modules):
    """patch.dict 有効後に http_util をロードする。"""
    sys.modules.pop("lilla_core.core.http_util", None)
    from lilla_core.core import http_util as loaded
    yield loaded
    sys.modules.pop("lilla_core.core.http_util", None)


# ---------------------------------------------------------------------------
# フィクスチャ
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def default_config(http_util, monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """各テスト用のデフォルト設定（プロキシなし・NO_PROXY なし）。
    テストから受け取って属性を上書きすることで任意の設定を再現できる。
    """
    cfg = MagicMock()
    cfg.proxy.resolve_url.return_value = None
    cfg.env.http_proxy_user = None
    cfg.env.http_proxy_pass = None
    cfg.proxy.no_proxy = None
    monkeypatch.setattr(http_util, "get_config", lambda: cfg)
    return cfg


@pytest.fixture()
def session_mock(http_util, monkeypatch: pytest.MonkeyPatch) -> tuple[MagicMock, MagicMock]:
    """aiohttp の ClientSession をモックし (mock_session, mock_resp) を返す。

    mock_resp.text() の返り値は "response body" がデフォルト。
    各テストで mock_resp を直接操作して変更できる。
    """
    mock_resp = MagicMock()
    mock_resp.text = AsyncMock(return_value="response body")
    mock_resp.raise_for_status = MagicMock()
    mock_resp.status = 200
    mock_resp.request_info = MagicMock()
    mock_resp.history = MagicMock()
    mock_resp.headers = MagicMock()

    # `async with session.request(...) as resp:` 用のコンテキストマネージャ
    resp_cm = AsyncMock()
    resp_cm.__aenter__ = AsyncMock(return_value=mock_resp)
    resp_cm.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.request.return_value = resp_cm

    # `async with aiohttp.ClientSession() as session:` 用のコンテキストマネージャ
    session_cm = AsyncMock()
    session_cm.__aenter__ = AsyncMock(return_value=mock_session)
    session_cm.__aexit__ = AsyncMock(return_value=False)

    mock_cs = MagicMock(return_value=session_cm)
    monkeypatch.setattr(http_util.aiohttp, "ClientSession", mock_cs)
    monkeypatch.setattr(http_util.aiohttp, "BasicAuth", MagicMock())
    monkeypatch.setattr(http_util.aiohttp, "ClientTimeout", MagicMock())

    return mock_session, mock_resp


# ---------------------------------------------------------------------------
# TestIsNoProxy
# ---------------------------------------------------------------------------


class TestIsNoProxy:
    def test_no_proxy_none_returns_false(self, http_util, default_config: MagicMock) -> None:
        default_config.proxy.no_proxy = None
        assert http_util._is_no_proxy("http://example.com") is False

    def test_empty_string_returns_false(self, http_util, default_config: MagicMock) -> None:
        default_config.proxy.no_proxy = ""
        assert http_util._is_no_proxy("http://example.com") is False

    def test_exact_host_match(self, http_util, default_config: MagicMock) -> None:
        default_config.proxy.no_proxy = "example.com"
        assert http_util._is_no_proxy("http://example.com/path") is True

    def test_exact_host_no_match(self, http_util, default_config: MagicMock) -> None:
        default_config.proxy.no_proxy = "other.com"
        assert http_util._is_no_proxy("http://example.com") is False

    def test_wildcard_subdomain_match(self, http_util, default_config: MagicMock) -> None:
        default_config.proxy.no_proxy = "*.example.com"
        assert http_util._is_no_proxy("http://api.example.com") is True

    def test_wildcard_no_match(self, http_util, default_config: MagicMock) -> None:
        default_config.proxy.no_proxy = "*.example.com"
        assert http_util._is_no_proxy("http://other.com") is False

    def test_wildcard_trailing_match(self, http_util, default_config: MagicMock) -> None:
        default_config.proxy.no_proxy = "192.168.50.*"
        assert http_util._is_no_proxy("http://192.168.50.1") is True

    def test_wildcard_trailing_no_match(self, http_util, default_config: MagicMock) -> None:
        default_config.proxy.no_proxy = "192.168.50.*"
        assert http_util._is_no_proxy("http://192.168.60.1") is False

    def test_cidr_match(self, http_util, default_config: MagicMock) -> None:
        default_config.proxy.no_proxy = "192.168.0.0/16"
        assert http_util._is_no_proxy("http://192.168.50.1/path") is True

    def test_cidr_no_match(self, http_util, default_config: MagicMock) -> None:
        default_config.proxy.no_proxy = "192.168.0.0/16"
        assert http_util._is_no_proxy("http://10.0.0.1") is False

    def test_multiple_entries_first_matches(self, http_util, default_config: MagicMock) -> None:
        default_config.proxy.no_proxy = "localhost,127.0.0.1,example.com"
        assert http_util._is_no_proxy("http://localhost") is True

    def test_multiple_entries_no_match(self, http_util, default_config: MagicMock) -> None:
        default_config.proxy.no_proxy = "localhost,127.0.0.1"
        assert http_util._is_no_proxy("http://example.com") is False

    def test_hostless_url_with_wildcard_returns_false(
        self, http_util, default_config: MagicMock
    ) -> None:
        # ホスト名を持たない URL（file:// など）はワイルドカード指定でも
        # マッチせず、fnmatch(None, ...) の TypeError を送出しないこと。
        default_config.proxy.no_proxy = "*.example.com"
        assert http_util._is_no_proxy("file:///etc/hosts") is False

    def test_hostless_url_with_cidr_returns_false(
        self, http_util, default_config: MagicMock
    ) -> None:
        # ホスト名を持たない URL は CIDR 指定でもマッチしないこと。
        default_config.proxy.no_proxy = "192.168.0.0/16"
        assert http_util._is_no_proxy("mailto:user@example.com") is False


# ---------------------------------------------------------------------------
# TestResolveProxy
# ---------------------------------------------------------------------------


class TestResolveProxy:
    def test_no_proxy_configured_returns_none(
        self, http_util, default_config: MagicMock
    ) -> None:
        """プロキシ未設定なら (None, None) を返す。"""
        proxy, auth = http_util._resolve_proxy("http://example.com")
        assert proxy is None
        assert auth is None

    def test_proxy_without_credentials_returns_proxy_only(
        self, http_util, default_config: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """プロキシのみ設定されていれば proxy を返し auth は None。"""
        default_config.proxy.resolve_url.return_value = "http://proxy:8080"
        monkeypatch.setattr(http_util.aiohttp, "BasicAuth", MagicMock())
        proxy, auth = http_util._resolve_proxy("http://example.com")
        assert proxy == "http://proxy:8080"
        assert auth is None

    def test_proxy_with_credentials_returns_basic_auth(
        self, http_util, default_config: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """認証情報がそろっていれば BasicAuth を生成して返す。"""
        default_config.proxy.resolve_url.return_value = "http://proxy:8080"
        default_config.env.http_proxy_user = "user"
        default_config.env.http_proxy_pass = "pass"
        basic_auth = MagicMock()
        monkeypatch.setattr(http_util.aiohttp, "BasicAuth", basic_auth)
        proxy, auth = http_util._resolve_proxy("http://example.com")
        assert proxy == "http://proxy:8080"
        basic_auth.assert_called_once_with("user", "pass")
        assert auth is basic_auth.return_value

    def test_no_proxy_match_returns_none(
        self, http_util, default_config: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """URL が NO_PROXY にマッチすれば (None, None) を返す。"""
        default_config.proxy.resolve_url.return_value = "http://proxy:8080"
        default_config.env.http_proxy_user = "user"
        default_config.env.http_proxy_pass = "pass"
        default_config.proxy.no_proxy = "example.com"
        monkeypatch.setattr(http_util.aiohttp, "BasicAuth", MagicMock())
        proxy, auth = http_util._resolve_proxy("http://example.com/api")
        assert proxy is None
        assert auth is None


# ---------------------------------------------------------------------------
# TestSendHttpRequest
# ---------------------------------------------------------------------------


class TestSendHttpRequest:
    async def test_returns_response_text(
        self, http_util, session_mock: tuple[MagicMock, MagicMock]
    ) -> None:
        result = await http_util.send_http_request("http://example.com/api")
        assert result == "response body"

    async def test_error_status_raises_client_response_error_with_body(
        self, http_util, session_mock: tuple[MagicMock, MagicMock]
    ) -> None:
        """400以上のステータスでは ClientResponseError が送出され、message にボディが入る。"""
        import aiohttp

        _, mock_resp = session_mock
        mock_resp.status = 400
        mock_resp.text = AsyncMock(return_value='{"error": "invalid_grant"}')

        with pytest.raises(aiohttp.ClientResponseError) as exc_info:
            await http_util.send_http_request("http://example.com/api")

        assert exc_info.value.status == 400
        assert exc_info.value.message == '{"error": "invalid_grant"}'

    async def test_error_status_logs_body(
        self,
        http_util,
        session_mock: tuple[MagicMock, MagicMock],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """400以上のステータスではレスポンスボディが ERROR レベルでログ出力される。"""
        import aiohttp
        import logging

        _, mock_resp = session_mock
        mock_resp.status = 500
        mock_resp.text = AsyncMock(return_value="internal error detail")

        with caplog.at_level(logging.ERROR, logger=http_util.logger.name):
            with pytest.raises(aiohttp.ClientResponseError):
                await http_util.send_http_request("http://example.com/api")

        assert "internal error detail" in caplog.text

    async def test_error_status_logs_masked_secret_keys(
        self,
        http_util,
        session_mock: tuple[MagicMock, MagicMock],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """ERRORログでもレスポンスボディの秘匿情報キーはマスクされる（DEBUGログとの一貫性）。"""
        import aiohttp
        import json
        import logging

        _, mock_resp = session_mock
        mock_resp.status = 400
        mock_resp.text = AsyncMock(
            return_value=json.dumps({"error": "invalid_grant", "access_token": "leaked"})
        )

        with caplog.at_level(logging.ERROR, logger=http_util.logger.name):
            with pytest.raises(aiohttp.ClientResponseError):
                await http_util.send_http_request("http://example.com/api")

        assert "leaked" not in caplog.text
        assert "***MASKED***" in caplog.text

    async def test_get_request_sends_no_body(
        self, http_util, session_mock: tuple[MagicMock, MagicMock]
    ) -> None:
        mock_session, _ = session_mock
        await http_util.send_http_request("http://example.com", method="GET")
        assert mock_session.request.call_args.kwargs["json"] is None

    async def test_post_request_sends_json_body(
        self, http_util, session_mock: tuple[MagicMock, MagicMock]
    ) -> None:
        mock_session, _ = session_mock
        data = {"key": "value"}
        await http_util.send_http_request("http://example.com", method="POST", data=data)
        assert mock_session.request.call_args.kwargs["json"] == data

    async def test_put_request_sends_json_body(
        self, http_util, session_mock: tuple[MagicMock, MagicMock]
    ) -> None:
        mock_session, _ = session_mock
        data = {"key": "value"}
        await http_util.send_http_request("http://example.com", method="PUT", data=data)
        assert mock_session.request.call_args.kwargs["json"] == data

    async def test_patch_request_sends_json_body(
        self, http_util, session_mock: tuple[MagicMock, MagicMock]
    ) -> None:
        """PATCH でも data が JSON ボディとして送信される（Google Tasks 完了更新の回帰テスト）。"""
        mock_session, _ = session_mock
        data = {"status": "completed"}
        await http_util.send_http_request("http://example.com", method="PATCH", data=data)
        assert mock_session.request.call_args.kwargs["json"] == data

    async def test_method_uppercased(
        self, http_util, session_mock: tuple[MagicMock, MagicMock]
    ) -> None:
        mock_session, _ = session_mock
        await http_util.send_http_request("http://example.com", method="post")
        assert mock_session.request.call_args.kwargs["method"] == "POST"

    async def test_no_proxy_by_default(
        self, http_util, session_mock: tuple[MagicMock, MagicMock]
    ) -> None:
        mock_session, _ = session_mock
        await http_util.send_http_request("http://example.com")
        assert mock_session.request.call_args.kwargs["proxy"] is None

    async def test_proxy_used_when_configured(
        self,
        http_util,
        session_mock: tuple[MagicMock, MagicMock],
        default_config: MagicMock,
    ) -> None:
        mock_session, _ = session_mock
        default_config.proxy.resolve_url.return_value = "http://proxy:8080"

        await http_util.send_http_request("http://example.com")

        assert mock_session.request.call_args.kwargs["proxy"] == "http://proxy:8080"

    async def test_no_proxy_match_removes_proxy(
        self,
        http_util,
        session_mock: tuple[MagicMock, MagicMock],
        default_config: MagicMock,
    ) -> None:
        """no_proxy にマッチするURLへのリクエストはプロキシを通さない"""
        mock_session, _ = session_mock
        default_config.proxy.resolve_url.return_value = "http://proxy:8080"
        default_config.proxy.no_proxy = "example.com"

        await http_util.send_http_request("http://example.com/api")

        assert mock_session.request.call_args.kwargs["proxy"] is None
        assert mock_session.request.call_args.kwargs["proxy_auth"] is None

    async def test_proxy_auth_set_when_credentials_given(
        self,
        http_util,
        session_mock: tuple[MagicMock, MagicMock],
        default_config: MagicMock,
    ) -> None:
        """プロキシ認証情報が設定されていれば BasicAuth が作られる"""
        mock_session, _ = session_mock
        default_config.proxy.resolve_url.return_value = "http://proxy:8080"
        default_config.env.http_proxy_user = "user"
        default_config.env.http_proxy_pass = "pass"

        await http_util.send_http_request("http://example.com")

        http_util.aiohttp.BasicAuth.assert_called_once_with("user", "pass")
        # proxy_auth には BasicAuth の戻り値が渡される
        assert mock_session.request.call_args.kwargs["proxy_auth"] is not None

    async def test_timeout_passed_to_client_timeout(
        self,
        http_util,
        session_mock: tuple[MagicMock, MagicMock],
    ) -> None:
        """timeout 値が ClientTimeout(total=...) に渡される"""
        await http_util.send_http_request("http://example.com", timeout=60)

        http_util.aiohttp.ClientTimeout.assert_called_with(total=60)

    async def test_post_with_form_data_sends_data_not_json(
        self, http_util, session_mock: tuple[MagicMock, MagicMock]
    ) -> None:
        """form_data を指定すると data に渡され json は None になる"""
        mock_session, _ = session_mock
        form = {"key": "value"}
        await http_util.send_http_request("http://example.com", method="POST", form_data=form)
        assert mock_session.request.call_args.kwargs["data"] == form
        assert mock_session.request.call_args.kwargs["json"] is None

    async def test_form_data_takes_priority_over_data(
        self, http_util, session_mock: tuple[MagicMock, MagicMock]
    ) -> None:
        """form_data と data が両方指定された場合は form_data が優先される"""
        mock_session, _ = session_mock
        form = {"form_key": "form_value"}
        await http_util.send_http_request(
            "http://example.com",
            method="POST",
            data={"json_key": "json_value"},
            form_data=form,
        )
        assert mock_session.request.call_args.kwargs["data"] == form
        assert mock_session.request.call_args.kwargs["json"] is None


# ---------------------------------------------------------------------------
# TestStreamHttpRequest
# ---------------------------------------------------------------------------


async def _async_gen(*items):
    """テスト用の非同期ジェネレータ。"""
    for item in items:
        yield item


@pytest.fixture()
def stream_mock(http_util, monkeypatch: pytest.MonkeyPatch) -> tuple[MagicMock, MagicMock]:
    """stream_http_request 用の aiohttp モック (mock_session, mock_resp) を返す。

    mock_resp.content はテスト側で _async_gen(...) を使って設定する。
    """
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()

    resp_cm = AsyncMock()
    resp_cm.__aenter__ = AsyncMock(return_value=mock_resp)
    resp_cm.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.request.return_value = resp_cm

    session_cm = AsyncMock()
    session_cm.__aenter__ = AsyncMock(return_value=mock_session)
    session_cm.__aexit__ = AsyncMock(return_value=False)

    mock_cs = MagicMock(return_value=session_cm)
    monkeypatch.setattr(http_util.aiohttp, "ClientSession", mock_cs)
    monkeypatch.setattr(http_util.aiohttp, "BasicAuth", MagicMock())
    monkeypatch.setattr(http_util.aiohttp, "ClientTimeout", MagicMock())

    return mock_session, mock_resp


class TestStreamHttpRequest:
    async def test_yields_chunks(
        self, http_util, stream_mock: tuple[MagicMock, MagicMock]
    ) -> None:
        """レスポンスのチャンクを順番にyieldする。"""
        _, mock_resp = stream_mock
        mock_resp.content = _async_gen(b"hello", b"world")

        chunks = [c async for c in http_util.stream_http_request("http://example.com")]

        assert chunks == [b"hello", b"world"]

    async def test_raise_for_status_called(
        self, http_util, stream_mock: tuple[MagicMock, MagicMock]
    ) -> None:
        """raise_for_status が呼ばれる。"""
        _, mock_resp = stream_mock
        mock_resp.content = _async_gen()

        async for _ in http_util.stream_http_request("http://example.com"):
            pass

        mock_resp.raise_for_status.assert_called_once()

    async def test_post_sends_json_body(
        self, http_util, stream_mock: tuple[MagicMock, MagicMock]
    ) -> None:
        """POST時に data が json として送信される。"""
        mock_session, mock_resp = stream_mock
        mock_resp.content = _async_gen()
        data = {"key": "value"}

        async for _ in http_util.stream_http_request(
            "http://example.com", method="POST", data=data
        ):
            pass

        assert mock_session.request.call_args.kwargs["json"] == data

    async def test_no_proxy_by_default(
        self, http_util, stream_mock: tuple[MagicMock, MagicMock]
    ) -> None:
        """プロキシ設定がない場合は proxy=None で送信される。"""
        mock_session, mock_resp = stream_mock
        mock_resp.content = _async_gen()

        async for _ in http_util.stream_http_request("http://example.com"):
            pass

        assert mock_session.request.call_args.kwargs["proxy"] is None

    async def test_proxy_used_when_configured(
        self,
        http_util,
        stream_mock: tuple[MagicMock, MagicMock],
        default_config: MagicMock,
    ) -> None:
        """プロキシ設定がある場合は proxy に渡される。"""
        mock_session, mock_resp = stream_mock
        mock_resp.content = _async_gen()
        default_config.proxy.resolve_url.return_value = "http://proxy:8080"

        async for _ in http_util.stream_http_request("http://example.com"):
            pass

        assert mock_session.request.call_args.kwargs["proxy"] == "http://proxy:8080"


# ---------------------------------------------------------------------------
# TestSanitizeRequestDataForLog
# ---------------------------------------------------------------------------


class TestSanitizeRequestDataForLog:
    def test_non_dict_returned_unchanged(self, http_util) -> None:
        """dict 以外はそのまま返す。"""
        assert http_util._sanitize_request_data_for_log("string") == "string"
        assert http_util._sanitize_request_data_for_log(None) is None
        assert http_util._sanitize_request_data_for_log([1, 2]) == [1, 2]

    def test_no_messages_key_returned_unchanged(self, http_util) -> None:
        """messages キーがない dict はそのまま返す。"""
        data = {"model": "gpt-4", "temperature": 0.7}
        result = http_util._sanitize_request_data_for_log(data)
        assert result == data

    def test_messages_not_list_returned_unchanged(self, http_util) -> None:
        """messages が list でない場合はそのまま返す。"""
        data = {"messages": "not a list"}
        result = http_util._sanitize_request_data_for_log(data)
        assert result["messages"] == "not a list"

    def test_text_content_not_modified(self, http_util) -> None:
        """text タイプのコンテンツブロックは変更されない。"""
        data = {
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "hello"}]}
            ]
        }
        result = http_util._sanitize_request_data_for_log(data)
        assert result["messages"][0]["content"][0]["text"] == "hello"

    def test_string_content_not_modified(self, http_util) -> None:
        """content が文字列のメッセージは変更されない。"""
        data = {
            "messages": [
                {"role": "user", "content": "hello"}
            ]
        }
        result = http_util._sanitize_request_data_for_log(data)
        assert result["messages"][0]["content"] == "hello"

    def test_base64_image_url_is_masked(self, http_util) -> None:
        """image_url ブロックの base64 データがマスクされる。"""
        b64 = "abc123" * 100
        data = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "見て"},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64}"},
                        },
                    ],
                }
            ]
        }
        result = http_util._sanitize_request_data_for_log(data)
        masked_url = result["messages"][0]["content"][1]["image_url"]["url"]
        assert masked_url == f"[BASE64 OMITTED ({len(b64)} chars)]"

    def test_original_data_not_mutated(self, http_util) -> None:
        """元の data は変更されない（deep copy を使っていること）。"""
        b64 = "abc123"
        data = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64}"},
                        }
                    ],
                }
            ]
        }
        http_util._sanitize_request_data_for_log(data)
        assert data["messages"][0]["content"][0]["image_url"]["url"] == f"data:image/png;base64,{b64}"

    def test_non_base64_image_url_not_masked(self, http_util) -> None:
        """base64 でない image_url はマスクされない。"""
        data = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": "https://example.com/image.png"},
                        }
                    ],
                }
            ]
        }
        result = http_util._sanitize_request_data_for_log(data)
        assert result["messages"][0]["content"][0]["image_url"]["url"] == "https://example.com/image.png"

    def test_secret_keys_masked(self, http_util) -> None:
        """トップレベルの client_secret / refresh_token / access_token はマスクされる。"""
        data = {
            "grant_type": "refresh_token",
            "client_secret": "s3cr3t",
            "refresh_token": "r3fr3sh",
            "access_token": "acc3ss",
        }
        result = http_util._sanitize_request_data_for_log(data)
        assert result["client_secret"] == "***MASKED***"
        assert result["refresh_token"] == "***MASKED***"
        assert result["access_token"] == "***MASKED***"
        assert result["grant_type"] == "refresh_token"


# ---------------------------------------------------------------------------
# TestMaskSecretKeys
# ---------------------------------------------------------------------------


class TestMaskSecretKeys:
    def test_google_form_data_masks_only_secret_keys(self, http_util) -> None:
        """Google のトークンリフレッシュ form_data 形状で該当キーのみマスクされる。"""
        form_data = {
            "grant_type": "refresh_token",
            "client_id": "my-client-id",
            "client_secret": "my-client-secret",
            "refresh_token": "my-refresh-token",
        }
        result = http_util._mask_secret_keys(form_data)
        assert result["client_secret"] == "***MASKED***"
        assert result["refresh_token"] == "***MASKED***"
        assert result["grant_type"] == "refresh_token"
        assert result["client_id"] == "my-client-id"

    def test_withings_form_data_masks_only_secret_keys(self, http_util) -> None:
        """Withings のトークンリフレッシュ form_data 形状で該当キーのみマスクされる。"""
        form_data = {
            "action": "requesttoken",
            "grant_type": "refresh_token",
            "client_id": "withings-client-id",
            "client_secret": "withings-client-secret",
            "refresh_token": "withings-refresh-token",
        }
        result = http_util._mask_secret_keys(form_data)
        assert result["client_secret"] == "***MASKED***"
        assert result["refresh_token"] == "***MASKED***"
        assert result["action"] == "requesttoken"
        assert result["grant_type"] == "refresh_token"
        assert result["client_id"] == "withings-client-id"

    def test_authorization_code_not_masked(self, http_util) -> None:
        """認可コード（code キー）はマスク対象外。"""
        form_data = {
            "grant_type": "authorization_code",
            "code": "auth-code-value",
            "redirect_uri": "https://example.com/callback",
            "client_secret": "secret",
        }
        result = http_util._mask_secret_keys(form_data)
        assert result["code"] == "auth-code-value"
        assert result["redirect_uri"] == "https://example.com/callback"
        assert result["client_secret"] == "***MASKED***"

    def test_case_insensitive_key_match(self, http_util) -> None:
        """キー名の大文字小文字が異なっても一致すればマスクされる。"""
        form_data = {"Client_Secret": "s", "REFRESH_TOKEN": "r", "AccessToken": "not masked"}
        result = http_util._mask_secret_keys(form_data)
        assert result["Client_Secret"] == "***MASKED***"
        assert result["REFRESH_TOKEN"] == "***MASKED***"
        # "AccessToken" (no underscore) does not match "access_token" exactly.
        assert result["AccessToken"] == "not masked"

    def test_original_dict_not_mutated(self, http_util) -> None:
        """元の dict は変更されない。"""
        form_data = {"client_secret": "secret-value"}
        http_util._mask_secret_keys(form_data)
        assert form_data["client_secret"] == "secret-value"


# ---------------------------------------------------------------------------
# TestSanitizeResponseTextForLog
# ---------------------------------------------------------------------------


class TestSanitizeResponseTextForLog:
    def test_token_exchange_response_masked(self, http_util) -> None:
        """トークン交換成功時のレスポンス JSON で access_token / refresh_token がマスクされる。"""
        import json

        response_text = json.dumps(
            {
                "access_token": "ya29.abc",
                "refresh_token": "1//xyz",
                "expires_in": 3599,
                "token_type": "Bearer",
            }
        )
        result = http_util._sanitize_response_text_for_log(response_text)
        parsed = json.loads(result)
        assert parsed["access_token"] == "***MASKED***"
        assert parsed["refresh_token"] == "***MASKED***"
        assert parsed["expires_in"] == 3599
        assert parsed["token_type"] == "Bearer"

    def test_notion_error_response_code_not_masked(self, http_util) -> None:
        """Notion のエラーレスポンス形状で code はマスクされない。"""
        import json

        response_text = json.dumps({"code": "validation_error", "message": "invalid request"})
        result = http_util._sanitize_response_text_for_log(response_text)
        parsed = json.loads(result)
        assert parsed["code"] == "validation_error"

    def test_google_error_response_code_not_masked(self, http_util) -> None:
        """Google のエラーレスポンス（ネストした code）で code はマスクされない。"""
        import json

        response_text = json.dumps({"error": {"code": 400, "message": "Bad Request"}})
        result = http_util._sanitize_response_text_for_log(response_text)
        parsed = json.loads(result)
        assert parsed["error"]["code"] == 400

    def test_unparseable_body_returned_as_is(self, http_util) -> None:
        """JSON としてパースできない文字列はそのまま返す。"""
        response_text = "not json at all"
        result = http_util._sanitize_response_text_for_log(response_text)
        assert result == "not json at all"

    def test_non_dict_json_returned_as_is(self, http_util) -> None:
        """JSON としてパースできてもトップレベルが dict でなければそのまま返す。"""
        response_text = "[1, 2, 3]"
        result = http_util._sanitize_response_text_for_log(response_text)
        assert result == "[1, 2, 3]"

    def test_original_string_not_mutated(self, http_util) -> None:
        """元の文字列オブジェクトへの参照は変わらない（str は不変なので当然だが明示的に確認）。"""
        import json

        original = json.dumps({"access_token": "secret-value"})
        original_copy = original
        http_util._sanitize_response_text_for_log(original)
        assert original == original_copy
        assert "secret-value" in original
