"""Discord 添付ファイルのダウンロード共通処理。

Discord CDN からの取得はプロキシ設定を尊重する必要があるため、画像添付
（`services/image_attachment.py`）とコマンドの BODY 添付
（`commands/attachment_body.py`）の双方からこのモジュールを使う。

添付のダウンロードはレスポンスをテキストではなくバイト列で受け取る必要があるため、
`core.http_util.send_http_request`（戻り値が str）ではなくここで aiohttp を直接使う。
プロキシ / プロキシ認証は `core.http_util` と同じ設定値から解決する。
"""
from __future__ import annotations

import aiohttp

from lilla_core.core.config import get_config


def normalize_content_type(attachment: object) -> str:
    """添付ファイルの Content-Type を小文字・パラメータ無しに正規化する。

    ``image/png; charset=binary`` のようにパラメータ付きで渡ってきても、
    メディアタイプ部分（``image/png``）だけを小文字で取り出す。画像添付
    （`services/image_attachment.py`）と BODY 添付（`commands/attachment_body.py`）の
    双方で同じ判定式を共有するためにここへ一元化する。

    Args:
        attachment: Discord の添付ファイル。

    Returns:
        正規化した Content-Type。取得できない（文字列でない）場合は空文字列。
    """
    content_type = getattr(attachment, "content_type", None)
    if not isinstance(content_type, str):
        return ""
    return content_type.split(";")[0].strip().lower()


def resolve_proxy_settings(config: object | None = None) -> tuple[str | None, dict[str, str] | None]:
    """設定からプロキシ URL とプロキシ認証ヘッダーを解決する。

    Args:
        config: 使用する設定オブジェクト。省略した場合は `get_config()` で取得する
            （読み込み済みの設定を持っている呼び出し元は、それをそのまま渡せる）。

    Returns:
        (プロキシ URL, プロキシ認証ヘッダー) のタプル。認証情報が揃っていない場合の
        2 番目の要素は None。ヘッダーは `Proxy-Authorization` 1 つで、aiohttp 3.14 で
        非推奨になった `proxy_auth` / `BasicAuth` の代わりに `proxy_headers` へ渡す。
    """
    if config is None:
        config = get_config()
    proxy_headers = (
        {
            "Proxy-Authorization": aiohttp.encode_basic_auth(
                config.env.http_proxy_user, config.env.http_proxy_pass, encoding="latin-1"
            )
        }
        if config.env.http_proxy_user and config.env.http_proxy_pass
        else None
    )
    return config.proxy.resolve_url(), proxy_headers


async def download_attachment_bytes(
    url: str, proxy: str | None, proxy_headers: dict[str, str] | None
) -> bytes:
    """プロキシ経由で添付ファイルをダウンロードし、バイト列を返す。

    Args:
        url: 添付ファイルの URL（Discord CDN）。
        proxy: 経由するプロキシ URL。未設定の場合は None。
        proxy_headers: プロキシへ送るヘッダー（`Proxy-Authorization`）。不要な場合は None。

    Returns:
        ダウンロードしたファイルの内容（バイト列）。

    Raises:
        aiohttp.ClientError: 通信に失敗した場合、または 4xx / 5xx が返った場合。
    """
    async with aiohttp.ClientSession() as session:
        async with session.get(url, proxy=proxy, proxy_headers=proxy_headers) as resp:
            resp.raise_for_status()
            return await resp.read()
