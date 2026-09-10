import copy
import fnmatch
import ipaddress
import json
import logging
from collections.abc import AsyncGenerator
from typing import Any
from urllib.parse import urlparse

import aiohttp

from lilla_core.core.config import get_config

logger = logging.getLogger(__name__)

_MASKED_VALUE = "***MASKED***"
_SECRET_KEYS = {"client_secret", "refresh_token", "access_token"}


def _mask_secret_keys(data: dict[str, Any]) -> dict[str, Any]:
    """dict のトップレベルキーのうち秘匿情報キー（client_secret / refresh_token /
    access_token、大文字小文字無視）に一致する値をマスクしたコピーを返す。
    元の dict は変更しない。
    """
    masked = copy.deepcopy(data)
    for key in list(masked.keys()):
        if key.lower() in _SECRET_KEYS:
            masked[key] = _MASKED_VALUE
    return masked


def _sanitize_response_text_for_log(response_text: str) -> str:
    """DEBUGログ出力用にレスポンス本文（JSON文字列）をサニタイズする。
    JSON としてパースできる dict であれば秘匿情報キーをマスクして
    文字列化して返す。パースできない場合はそのまま返す（レスポンス本文は
    多様な形式を取り得るため、パース失敗時にログを諦めるより元の内容を
    出す方がデバッグ上有用と判断）。
    """
    try:
        parsed = json.loads(response_text)
    except (json.JSONDecodeError, TypeError):
        return response_text

    if not isinstance(parsed, dict):
        return response_text

    return json.dumps(_mask_secret_keys(parsed), ensure_ascii=False)


def _sanitize_request_data_for_log(data: Any) -> Any:
    """DEBUGログ出力用に data をサニタイズする。
    トップレベルの秘匿情報キー（client_secret / refresh_token / access_token）
    をマスクし、加えて messages リスト内の image_url ブロックの url を
    "[BASE64 OMITTED (N chars)]" に置換したコピーを返す。
    元の data は変更しない。
    """
    if not isinstance(data, dict):
        return data

    sanitized = _mask_secret_keys(data)
    messages = sanitized.get("messages")
    if not isinstance(messages, list):
        return sanitized

    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "image_url":
                image_url = block.get("image_url", {})
                url = image_url.get("url", "")
                if "base64," in url:
                    b64_part = url.split("base64,", 1)[1]
                    image_url["url"] = f"[BASE64 OMITTED ({len(b64_part)} chars)]"

    return sanitized


def _resolve_proxy(url: str) -> tuple[str | None, aiohttp.BasicAuth | None]:
    """リクエスト先 URL に応じたプロキシと認証情報を解決する。

    config からプロキシ URL とプロキシ認証情報を読み取り、
    認証情報が両方そろっていれば aiohttp.BasicAuth を生成する。
    URL が NO_PROXY にマッチする場合は (None, None) を返す。

    Returns
    -------
    tuple[str | None, aiohttp.BasicAuth | None]
        (proxy, proxy_auth) のタプル。
    """
    _config = get_config()
    proxy = _config.proxy.resolve_url()
    auth = (
        aiohttp.BasicAuth(_config.env.http_proxy_user, _config.env.http_proxy_pass)
        if _config.env.http_proxy_user and _config.env.http_proxy_pass
        else None
    )

    # NO_PROXY 判定
    if proxy and _is_no_proxy(url):
        return None, None

    return proxy, auth


async def send_http_request(
    url: str,
    method: str = "POST",
    data: dict[str, Any] | None = None,
    form_data: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    params: dict[str, str] | None = None,
    timeout: int = 300,
) -> str:
    """
    非同期HTTPリクエストを送信します。
    プロキシ / 認証を自動適用。

    data を指定すると JSON ボディ、form_data を指定するとフォームエンコードボディで送信します。
    両方指定した場合は form_data が優先されます。
    params を指定するとクエリパラメータとして URL に付加されます。
    ボディは POST / PUT / PATCH の場合のみ送信されます。
    """
    proxy, auth = _resolve_proxy(url)

    is_body_method = method.upper() in ("POST", "PUT", "PATCH")

    logger.info("%s %s", method.upper(), url)
    if logger.isEnabledFor(logging.DEBUG):
        if data is not None:
            logger.debug("Request body (JSON): %s", _sanitize_request_data_for_log(data))
        elif form_data is not None:
            logger.debug("Request body (form): %s", _mask_secret_keys(form_data))

    async with aiohttp.ClientSession() as session:
        async with session.request(
            method=method.upper(),
            url=url,
            json=data if is_body_method and not form_data else None,
            data=form_data if is_body_method and form_data else None,
            headers=headers,
            params=params,
            proxy=proxy,
            proxy_auth=auth,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            logger.info("Status: %s", resp.status)
            response_text = await resp.text()
            if resp.status >= 400:
                logger.error(
                    "HTTP error %s %s: %s",
                    resp.status,
                    url,
                    _sanitize_response_text_for_log(response_text),
                )
                raise aiohttp.ClientResponseError(
                    resp.request_info,
                    resp.history,
                    status=resp.status,
                    message=response_text,
                    headers=resp.headers,
                )
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug("Response body: %s", _sanitize_response_text_for_log(response_text))
            return response_text


async def stream_http_request(
    url: str,
    method: str = "POST",
    data: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 300,
) -> AsyncGenerator[bytes, None]:
    """
    非同期HTTPリクエストをストリーミングで送信し、チャンク単位でyieldします。
    プロキシ / 認証を自動適用。

    data を指定すると JSON ボディで送信します。
    ボディは POST / PUT / PATCH の場合のみ送信されます。
    """
    proxy, auth = _resolve_proxy(url)

    is_body_method = method.upper() in ("POST", "PUT", "PATCH")

    logger.info("%s %s (stream)", method.upper(), url)

    async with aiohttp.ClientSession() as session:
        async with session.request(
            method=method.upper(),
            url=url,
            json=data if is_body_method else None,
            headers=headers,
            proxy=proxy,
            proxy_auth=auth,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            resp.raise_for_status()
            async for chunk in resp.content:
                yield chunk


def _is_no_proxy(url: str) -> bool:
    """
    NO_PROXY にマッチするURLかをチェックします。
    NO_PROXY = "localhost,127.0.0.1,192.168.50.*" などの形式をサポート。
    """
    no_proxy = get_config().proxy.no_proxy
    if not no_proxy:
        return False

    no_proxy_list = [p.strip() for p in no_proxy.split(",") if p.strip()]

    parsed = urlparse(url)
    host = parsed.hostname
    if host is None:
        # file://path や相対パスなどホスト名を持たない URL は
        # どの NO_PROXY エントリにもマッチしない（プロキシ判定の対象外）。
        # ここで弾かないとワイルドカード判定の fnmatch(None, ...) が TypeError を送出する。
        return False

    for pattern in no_proxy_list:
        if "*" in pattern:
            if fnmatch.fnmatch(host, pattern):
                return True
        elif "/" in pattern:  # CIDR (例: 192.168.0.0/16)
            try:
                network = ipaddress.ip_network(pattern)
                ip = ipaddress.ip_address(host)
                if ip in network:
                    return True
            except ValueError:
                pass
        elif host == pattern:
            return True

    return False
