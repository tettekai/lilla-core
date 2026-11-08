"""`!mongodata <JSON>` コマンド。

外部（データ収集ジョブなど）から届いた JSON を、ホワイトリストで許可された
MongoDB コレクションへ登録する。
"""
from __future__ import annotations

import json
import logging

from lilla_core.commands.attachment_body import resolve_command_body
from lilla_core.commands.registry import register_command
from lilla_core.core.error_notify import notify_error
from lilla_core.tool_support.mongodata import (
    CollectionNotAllowedError,
    KeyFieldMissingError,
    save_document,
)
from lilla_core.ui.messages import t

logger = logging.getLogger(__name__)


@register_command("mongodata")
async def handle_mongodata(ctx, arg: str, tools: dict, bot) -> None:
    """`!mongodata <JSON>` の処理。正常時は INFO ログのみ、エラー時は notify_error で通知する。

    JSON 本体はコマンド名の後ろ（同じ行でも次の行でもよい）に置く::

        !mongodata {"collection": "asken_daily", "key": "date", "payload": {...}}

    Discord の 1 メッセージあたりの文字数上限を超える JSON は、添付ファイル
    （`.json` / `.txt`）で渡すこともできる。その場合コマンド行は `!mongodata` のみでよい。
    添付とテキストの両方に BODY がある場合は添付を優先する。

    JSON の後ろに続く文章は無視する（外部サービスが案内文を付けてくる場合がある）。

    エラーは元のチャンネルへ返信せず、ERROR ログとエラー通知チャンネルにのみ出力し、
    `dispatch_command` の結果を error にする（Discord 以外の呼び出し元はこれで失敗を知る）。

    Args:
        ctx: コマンドコンテキスト。添付ファイル（`ctx.attachments`）の BODY 解決と
            失敗の記録に使用する。
        arg: コマンド名の後ろに続く引数文字列（JSON 本体）。
        tools: ツールレジストリ（未使用）。
        bot: Discord クライアント。エラー通知に使用する。
    """
    body_text = await resolve_command_body(ctx, arg, bot, "!mongodata")
    if body_text is None:
        ctx.mark_failed()
        return

    try:
        decoder = json.JSONDecoder()
        body, _ = decoder.raw_decode(body_text.strip())
    except json.JSONDecodeError as e:
        await notify_error(bot, t("command.mongodata.json_parse_title"), e)
        ctx.mark_failed()
        return

    collection_name = body.get("collection", "").strip()
    if not collection_name:
        await notify_error(
            bot,
            t("command.mongodata.invalid_request_title"),
            t("command.mongodata.collection_required"),
        )
        ctx.mark_failed()
        return

    payload = body.get("payload")
    if not isinstance(payload, dict):
        await notify_error(
            bot,
            t("command.mongodata.invalid_request_title"),
            t("command.mongodata.invalid_payload"),
        )
        ctx.mark_failed()
        return

    try:
        await save_document(collection_name, payload, key=body.get("key"))
    except CollectionNotAllowedError:
        await notify_error(
            bot,
            t("command.mongodata.invalid_request_title"),
            t("command.mongodata.collection_not_allowed", collection=collection_name),
        )
        ctx.mark_failed()
        return
    except KeyFieldMissingError as e:
        await notify_error(
            bot,
            t("command.mongodata.invalid_request_title"),
            t("command.mongodata.key_field_missing", key_field=e.key),
        )
        ctx.mark_failed()
        return
    except Exception as e:
        await notify_error(bot, t("command.mongodata.db_error_title"), e)
        ctx.mark_failed()
