"""会話履歴を期間・キーワード・チャンネルで検索するコア組み込み LLM ツール。

会話履歴そのものはチャンネルで分離せず全チャンネル横断のまま保存し、この
ツールが「あの部屋で何を話したか」を思い出すための絞り込みを提供する。

`${CONFIG_ROOT}/tools/` に `type: lilla_core.builtin_tools.llm_conversation_get` の
YAML を置くと opt-in で有効化できる（コアは自動では読み込まない）。
"""
from __future__ import annotations

import logging

from lilla_core.tool_support.tool_result import tool_error, tool_success
from lilla_core.utils.datetime_utils import local_timezone

logger = logging.getLogger(__name__)

SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_conversations",
        "description": "会話履歴を期間、キーワード等を指定して取得します。",
        "parameters": {
            "type": "object",
            "properties": {
                "datetime_range": {
                    "type": "string",
                    "description": (
                        "検索期間。以下の形式をすべてサポート:\n"
                        "・today\n"
                        "・yesterday\n"
                        "・last_7_days（last_30_days など任意の日数指定可）\n"
                        "・2026-04-20（単一日）\n"
                        "・2026-04-20/2026-04-26（日付範囲指定）\n"
                        "・2026-04-20 15:00:00/2026-04-21 03:00:00（日時範囲指定）\n"
                        "省略時は直近30件を返す。"
                    ),
                },
                "query": {
                    "type": "string",
                    "description": (
                        "検索キーワード。スペース区切りでAND検索（例: \"体調 睡眠\"）。"
                        "省略時は期間内の全履歴を返す。"
                        "datetime_range が広い場合はキーワードを指定すると絞り込みができる。"
                    ),
                },
                "role": {
                    "type": "string",
                    "enum": ["user", "assistant", "all"],
                    "description": "発言者の種類（デフォルト: all）",
                },
                "channel_name": {
                    "type": "string",
                    "description": (
                        "絞り込む Discord チャンネルの登録名。"
                        "設定に登録された別名であり、Discord の現在のチャンネル名ではない。"
                        "省略時は全チャンネル横断で検索する。"
                        "登録に無い名前を指定するとエラーになる。"
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "取得する最大件数（デフォルト: 30、最大: 30）",
                },
            },
            "required": [],
        },
    },
}

#: `limit` の上限（LLM が大きな値を渡してもここで頭打ちにする）。
MAX_LIMIT = 30


def _content_to_text(content) -> str:
    """`message.content` を検索結果表示用のテキストへ変換する。

    通常は文字列だが、画像添付時はコンテンツブロックのリストになるため、
    `type="text"` のブロックだけを連結して返す。

    Args:
        content: 会話メッセージの content。

    Returns:
        表示用のプレーンテキスト。
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "".join(parts)
    return str(content)


def _resolve_channel_id(channel_name: str) -> int:
    """`discord.channels` の登録名を Discord チャンネル ID へ解決する。

    突き合わせは前後の空白を除いた完全一致で、登録に無い名前は全件検索へ
    落とさずエラーにする（「その部屋の話」を求めたのに全部屋から返すのは
    問いへの答えになっていないため）。

    Args:
        channel_name: `discord.channels` の登録名。

    Returns:
        解決した Discord チャンネル ID。

    Raises:
        ValueError: 登録に無い名前、または登録の `channel_id` が整数でない場合。
    """
    from lilla_core.core.config import get_config

    entry = get_config().discord.find_channel_by_name(channel_name)
    if entry is None:
        raise ValueError(f"Unknown channel_name: {channel_name!r}")
    try:
        return int(entry.channel_id)
    except (TypeError, ValueError) as e:
        raise ValueError(
            f"Registered channel {entry.name!r} has a non-integer channel_id: "
            f"{entry.channel_id!r}"
        ) from e


async def execute(input: dict, context: dict) -> dict:
    """会話履歴を条件で検索して返す。

    Args:
        input: ツール入力。
            datetime_range: 検索期間（省略時は期間絞り込みなし）。
            query: スペース区切りの AND キーワード（省略時は絞り込みなし）。
            role: 発言者フィルタ（`user` / `assistant` / `all`）。
            channel_name: `discord.channels` の登録名（省略時は全チャンネル横断）。
            limit: 最大取得件数（既定 30、上限 30）。
        context: ツール実行コンテキスト（未使用）。

    Returns:
        標準結果辞書。`data` に検索条件と結果リストを入れる。
    """
    tool_name = SCHEMA["function"]["name"]
    try:
        datetime_range = input.get("datetime_range")
        query = input.get("query")
        role = input.get("role", "all")
        channel_name = input.get("channel_name")
        limit = min(int(input.get("limit", MAX_LIMIT)), MAX_LIMIT)

        discord_channel_id = None
        if channel_name:
            try:
                discord_channel_id = _resolve_channel_id(channel_name)
            except ValueError as e:
                logger.warning("get_conversations: %s", e)
                return tool_error(tool_name, None, str(e))

        tz = local_timezone()

        start = end = None
        if datetime_range:
            from lilla_core.tool_support.date_range import DateTimeRange

            dt_from, dt_to = DateTimeRange.parse(datetime_range).as_datetime()
            # DateTimeRange は「人間側の暦日」の naive な壁時計時刻を返すため、
            # UTC で保存されている `time` と比べる前に解決済みタイムゾーンで aware にする。
            start = dt_from.replace(tzinfo=tz)
            end = dt_to.replace(tzinfo=tz)

        from lilla_core.repository.conversation_repository import get_conversation_repo

        docs = await get_conversation_repo().search(
            start=start,
            end=end,
            keywords=query.split() if query else None,
            role=role if role in ("user", "assistant") else None,
            discord_channel_id=discord_channel_id,
            limit=limit,
        )

        results = [
            {
                # time は aware UTC で返るため、表示用に解決済みタイムゾーンへ変換する。
                "time": doc["time"].astimezone(tz).strftime("%Y-%m-%d %H:%M:%S"),
                "role": doc.get("message", {}).get("role", ""),
                "content": _content_to_text(doc.get("message", {}).get("content", "")),
            }
            for doc in docs
        ]

        memory_entry = (
            "会話履歴を取得しました"
            + (f"（{datetime_range}）" if datetime_range else "（期間指定なし）")
            + (f"（#{channel_name}）" if channel_name else "")
            + f"。{len(results)}件。"
        )

        return tool_success(
            tool_name,
            memory_entry,
            {
                "datetime_range": datetime_range,
                "query": query,
                "role": role,
                "channel_name": channel_name,
                "total": len(results),
                "results": results,
            },
        )

    except Exception as e:
        logger.error("get_conversations error: %s", e, exc_info=True)
        return tool_error(tool_name, None, str(e))
