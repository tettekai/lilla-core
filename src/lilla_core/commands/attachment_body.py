"""コマンドの BODY（本文）をテキストと添付ファイルの両方から解決する共通処理。

Discord の 1 メッセージあたりの文字数上限（約 2000 文字）により、外部エージェントから
届く大きな JSON や結果本文が途中で切れてしまうことがある。その対策として、
`!mongodata` / `!toolresult` はコマンド本文に BODY を書く従来の方式に加えて、
BODY を添付ファイルで渡す方式もサポートする。

添付ファイルがある場合は添付を優先し、無ければテキスト側の BODY を使う。
どちらも無い場合や添付を読み取れない場合は `core.error_notify.notify_error` で通知し、
None を返して呼び出し元の処理を中断させる（元チャンネルへの返信はしない）。
"""
from __future__ import annotations

import logging

from lilla_core.core.error_notify import notify_error
from lilla_core.services.attachment_download import (
    download_attachment_bytes,
    normalize_content_type,
    resolve_proxy_settings,
)
from lilla_core.ui.messages import t

logger = logging.getLogger(__name__)

# 添付 BODY として受け付ける最大サイズ（1MB）
MAX_ATTACHMENT_SIZE = 1024 * 1024

# テキストとして読めると判断する拡張子
_TEXT_EXTENSIONS = (".json", ".txt")

# テキストとして読めると判断する Content-Type
_TEXT_CONTENT_TYPES = ("text/", "application/json")


def _is_textual_attachment(attachment: object) -> bool:
    """添付ファイルをテキストとして読み取ってよいかを判定する。

    拡張子（`.json` / `.txt`）と Content-Type（`text/*` / `application/json`）の
    どちらか一方が一致すればテキストとして扱う。

    Args:
        attachment: Discord の添付ファイル。

    Returns:
        テキストとして読み取ってよければ True。
    """
    filename = getattr(attachment, "filename", None)
    if isinstance(filename, str) and filename.lower().endswith(_TEXT_EXTENSIONS):
        return True

    content_type = normalize_content_type(attachment)
    return content_type.startswith(_TEXT_CONTENT_TYPES)


def _get_attachments(message: object) -> list:
    """メッセージから添付ファイルのリストを取り出す。

    添付を持たないメッセージオブジェクトが渡されても落ちないよう、
    取得できない場合は空リストを返す。
    """
    attachments = getattr(message, "attachments", None)
    if not attachments:
        return []
    return list(attachments)


async def _read_attachment_text(attachment: object, bot: object, error_title: str) -> str | None:
    """添付ファイルをダウンロードして UTF-8 のテキストとして返す。

    非テキスト添付・サイズ超過・ダウンロード失敗・UTF-8 デコード失敗はいずれも
    `notify_error` で通知し、None を返す（例外は呼び出し元に伝播させない）。

    Args:
        attachment: Discord の添付ファイル。
        bot: Discord クライアント。エラー通知に使う。
        error_title: エラー通知の見出しに使うコマンド名（例: `"!mongodata"`）。

    Returns:
        添付ファイルの内容。読み取れなかった場合は None。
    """
    filename = getattr(attachment, "filename", "") or ""

    if not _is_textual_attachment(attachment):
        await notify_error(
            bot,
            t("attachment.error_title", command=error_title),
            t(
                "attachment.not_text",
                filename=filename,
                content_type=(
                    normalize_content_type(attachment)
                    or t("attachment.content_type_unknown")
                ),
            ),
        )
        return None

    size = getattr(attachment, "size", None)
    if isinstance(size, int) and size > MAX_ATTACHMENT_SIZE:
        await notify_error(
            bot,
            t("attachment.error_title", command=error_title),
            t(
                "attachment.too_large",
                filename=filename,
                size=size,
                max_size=MAX_ATTACHMENT_SIZE,
            ),
        )
        return None

    proxy, proxy_auth = resolve_proxy_settings()
    try:
        raw = await download_attachment_bytes(attachment.url, proxy, proxy_auth)
    except Exception as e:
        await notify_error(bot, t("attachment.download_error_title", command=error_title), e)
        return None

    if len(raw) > MAX_ATTACHMENT_SIZE:
        await notify_error(
            bot,
            t("attachment.error_title", command=error_title),
            t(
                "attachment.too_large",
                filename=filename,
                size=len(raw),
                max_size=MAX_ATTACHMENT_SIZE,
            ),
        )
        return None

    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as e:
        await notify_error(
            bot,
            t("attachment.error_title", command=error_title),
            t("attachment.decode_failed", filename=filename, error=e),
        )
        return None


async def resolve_command_body(
    message: object, text_body: str, bot: object, error_title: str
) -> str | None:
    """コマンドの BODY をテキストと添付ファイルから解決する。

    優先順位は次のとおり。

    1. 添付ファイルがあればその内容を BODY とする（複数ある場合は先頭の 1 つのみ）
    2. 添付が無ければテキスト側の BODY を使う
    3. どちらも無ければ `notify_error` で通知し None を返す

    Args:
        message: コマンドを含む Discord メッセージ。
        text_body: コマンド引数から得たテキスト側の BODY 候補。
        bot: Discord クライアント。エラー通知に使う。
        error_title: エラー通知の見出しに使うコマンド名（例: `"!mongodata"`）。

    Returns:
        解決した BODY 文字列。解決できなかった場合は None。
    """
    attachments = _get_attachments(message)
    if attachments:
        if len(attachments) > 1:
            logger.info(
                "%s: Multiple attachments found; using only the first one as BODY (%d files)",
                error_title, len(attachments),
            )
        return await _read_attachment_text(attachments[0], bot, error_title)

    if text_body and text_body.strip():
        return text_body

    await notify_error(
        bot,
        t("attachment.body_missing_title", command=error_title),
        t("attachment.body_missing"),
    )
    return None
