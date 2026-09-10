"""Discord の画像添付を LLM へ渡す content parts（data URL）へ変換する共通処理。

Discord は最大 500MB（Nitro）までの添付を許すため、受け取った画像をそのまま
ダウンロード → base64 化 → LLM ボディへ埋め込みすると、メモリ圧迫や LLM
リクエストの失敗を招く。そのためコマンドの BODY 添付（`commands/attachment_body.py`）と
同様に、ダウンロード前後でサイズ上限のガードを行う。

上限超過・ダウンロード失敗・base64 化の失敗はいずれも `core.error_notify.notify_error`
で通知し、`None` を返して呼び出し元の処理を中断させる（例外は呼び出し元へ伝播させない）。
Discord のイベントハンドラより手前で例外が漏れると discord.py の内部ログに落ちるだけで
ユーザーへのフィードバックが無くなるため、通知はこのモジュールの責務とする。
"""
from __future__ import annotations

import base64
import logging

from lilla_core.core.config import get_config
from lilla_core.core.error_notify import notify_error
from lilla_core.ui.messages import t
from lilla_core.services.attachment_download import (
    download_attachment_bytes,
    normalize_content_type,
    resolve_proxy_settings,
)

logger = logging.getLogger(__name__)

# LLM へ画像として渡す MIME タイプ
SUPPORTED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}

# `bot.max_image_attachment_size_mb` が未設定・不正値のときに使う既定値（MB）
DEFAULT_MAX_IMAGE_ATTACHMENT_SIZE_MB = 8

_BYTES_PER_MB = 1024 * 1024

def filter_image_attachments(attachments: object) -> list:
    """添付ファイルのうち、LLM へ画像として渡せるものだけを抜き出す。

    Args:
        attachments: Discord メッセージの添付ファイル列。None でも構わない。

    Returns:
        対応 MIME タイプの添付ファイルのリスト（順序は元のまま）。
    """
    if not attachments:
        return []
    return [a for a in attachments if normalize_content_type(a) in SUPPORTED_IMAGE_TYPES]


def resolve_max_image_bytes(config: object | None = None) -> int:
    """画像添付として受け付ける最大サイズ（バイト）を設定から解決する。

    設定値が未設定・整数でない・0 以下のいずれかの場合は既定値（8MB）を使う。

    Args:
        config: 使用する設定オブジェクト。省略した場合は `get_config()` で取得する。

    Returns:
        最大サイズ（バイト）。
    """
    if config is None:
        config = get_config()
    size_mb = getattr(getattr(config, "bot", None), "max_image_attachment_size_mb", None)
    # bool は int のサブクラスなので明示的に除外する
    if not isinstance(size_mb, int) or isinstance(size_mb, bool) or size_mb <= 0:
        if size_mb is not None:
            logger.warning(
                "bot.max_image_attachment_size_mb is invalid; using default value (%dMB): %r",
                DEFAULT_MAX_IMAGE_ATTACHMENT_SIZE_MB, size_mb,
            )
        size_mb = DEFAULT_MAX_IMAGE_ATTACHMENT_SIZE_MB
    return size_mb * _BYTES_PER_MB


def _format_size_error(filename: str, size: int, max_bytes: int) -> str:
    """サイズ超過エラーの本文を組み立てる。

    Args:
        filename: 添付ファイル名。
        size: 実際のサイズ（バイト）。
        max_bytes: 上限サイズ（バイト）。

    Returns:
        上限値を含むエラーメッセージ。
    """
    return t(
        "image_attachment.too_large",
        max_mb=f"{max_bytes / _BYTES_PER_MB:.0f}",
        filename=filename or t("image_attachment.unknown_filename"),
        size=size,
        max_size=max_bytes,
    )


async def build_image_content_parts(
    attachments: list, bot: object, config: object | None = None
) -> list[dict] | None:
    """画像添付をダウンロードし、LLM へ渡す `image_url` パートのリストを返す。

    ダウンロード前に Discord から渡される `attachment.size` で早期にガードし、
    値を過信しないようダウンロード後の実バイト数でも同じ上限を検証する。
    いずれかの画像で失敗した場合は `notify_error` で通知したうえで None を返し、
    中途半端な内容を LLM へ渡さない。

    Args:
        attachments: 画像として扱う添付ファイルのリスト（`filter_image_attachments` の結果）。
        bot: Discord クライアント。エラー通知に使う。
        config: 使用する設定オブジェクト。省略した場合は `get_config()` で取得する。

    Returns:
        `{"type": "image_url", ...}` のリスト。1 件でも失敗した場合は None。
    """
    max_bytes = resolve_max_image_bytes(config)
    proxy, proxy_auth = resolve_proxy_settings(config)

    parts: list[dict] = []
    for attachment in attachments:
        filename = getattr(attachment, "filename", "") or ""

        size = getattr(attachment, "size", None)
        if isinstance(size, int) and not isinstance(size, bool) and size > max_bytes:
            await notify_error(
                bot,
                t("image_attachment.size_error_title"),
                _format_size_error(filename, size, max_bytes),
            )
            return None

        try:
            image_bytes = await download_attachment_bytes(attachment.url, proxy, proxy_auth)
        except Exception as e:
            await notify_error(bot, t("image_attachment.download_error_title"), e)
            return None

        # Discord 側から渡される size を過信せず、実バイト数でも検証する
        if len(image_bytes) > max_bytes:
            await notify_error(
                bot,
                t("image_attachment.size_error_title"),
                _format_size_error(filename, len(image_bytes), max_bytes),
            )
            return None

        base64_image = base64.b64encode(image_bytes).decode("utf-8")
        parts.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:{normalize_content_type(attachment)};base64,{base64_image}"
            },
        })

    return parts
