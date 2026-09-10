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
from lilla_core.ui.messages import t

logger = logging.getLogger(__name__)


@register_command("mongodata")
async def handle_mongodata(message, arg: str, tools: dict, bot) -> None:
    """`!mongodata <JSON>` の処理。正常時は INFO ログのみ、エラー時は notify_error で通知する。

    JSON 本体はコマンド名の後ろ（同じ行でも次の行でもよい）に置く::

        !mongodata {"collection": "asken_daily", "key": "date", "payload": {...}}

    Discord の 1 メッセージあたりの文字数上限を超える JSON は、添付ファイル
    （`.json` / `.txt`）で渡すこともできる。その場合コマンド行は `!mongodata` のみでよい。
    添付とテキストの両方に BODY がある場合は添付を優先する。

    JSON の後ろに続く文章は無視する（外部サービスが案内文を付けてくる場合がある）。

    エラーは元のチャンネルへ返信せず、ERROR ログとエラー通知チャンネルにのみ出力する。

    Args:
        message: Discord メッセージ。添付ファイルの BODY 解決に使用する。
        arg: コマンド名の後ろに続く引数文字列（JSON 本体）。
        tools: ツールレジストリ（未使用）。
        bot: Discord クライアント。エラー通知に使用する。
    """
    from lilla_core.core.config import get_config
    from lilla_core.repository.motor_client import create_motor_client

    config = get_config()

    body_text = await resolve_command_body(message, arg, bot, "!mongodata")
    if body_text is None:
        return

    try:
        decoder = json.JSONDecoder()
        body, _ = decoder.raw_decode(body_text.strip())
    except json.JSONDecodeError as e:
        await notify_error(bot, t("command.mongodata.json_parse_title"), e)
        return

    collection_name = body.get("collection", "").strip()
    if not collection_name:
        await notify_error(
            bot,
            t("command.mongodata.invalid_request_title"),
            t("command.mongodata.collection_required"),
        )
        return

    if collection_name not in config.commands.mongodata.allowed_collections:
        await notify_error(
            bot,
            t("command.mongodata.invalid_request_title"),
            t("command.mongodata.collection_not_allowed", collection=collection_name),
        )
        return

    payload = body.get("payload")
    if not isinstance(payload, dict):
        await notify_error(
            bot,
            t("command.mongodata.invalid_request_title"),
            t("command.mongodata.invalid_payload"),
        )
        return

    payload.pop("_id", None)

    key_field = body.get("key")

    try:
        client = create_motor_client(config.env.mongodb_uri)
        col = client[config.mongodb.db_name][collection_name]

        if key_field:
            key_value = payload.get(key_field)
            if key_value is None:
                await notify_error(
                    bot,
                    t("command.mongodata.invalid_request_title"),
                    t("command.mongodata.key_field_missing", key_field=key_field),
                )
                return
            await col.update_one(
                {key_field: key_value},
                {"$set": payload},
                upsert=True,
            )
            logger.info(
                "!mongodata: upsert completed collection=%s %s=%s",
                collection_name, key_field, key_value,
            )
        else:
            await col.insert_one(payload)
            logger.info("!mongodata: insert completed collection=%s", collection_name)

    except Exception as e:
        await notify_error(bot, t("command.mongodata.db_error_title"), e)
