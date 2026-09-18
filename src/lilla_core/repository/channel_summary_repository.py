from __future__ import annotations

from functools import lru_cache

from lilla_core.repository.motor_client import create_motor_client
from pymongo import ASCENDING

from lilla_core.utils.datetime_utils import utc_now


@lru_cache(maxsize=1)
def get_channel_summary_repo() -> "ChannelSummaryRepository":
    """ChannelSummaryRepository のシングルトンインスタンスを返す。

    初回呼び出し時に設定を読み込んでインスタンスを生成し、以降は同じインスタンスを返す。

    Returns
    -------
    ChannelSummaryRepository
        ChannelSummaryRepository のインスタンス
    """
    from lilla_core.core.config import get_config
    config = get_config()
    return ChannelSummaryRepository(config.env.mongodb_uri, config.mongodb.db_name)


class ChannelSummaryRepository:
    """登録 Discord チャンネルごとの「部屋のノート」（要約）を保持するリポジトリ。

    1 チャンネルにつき最新の要約 1 件だけを持つ（`discord_channel_id` がユニーク）。
    会話履歴のような TTL は持たず、次の要約で上書きされるまで残る。
    """

    def __init__(self, mongo_uri: str, db_name: str) -> None:
        """ChannelSummaryRepository を初期化する。

        Args:
            mongo_uri: MongoDB の接続 URI。
            db_name: 使用するデータベース名。
        """
        client = create_motor_client(mongo_uri)
        self._collection = client[db_name]["channel_summaries"]

    async def init_collection(self) -> None:
        """コレクションの初期化を行う（起動時の一元初期化用の薄いオーケストレーション層）。"""
        await self.ensure_indexes()

    async def ensure_indexes(self) -> None:
        """インデックスを作成する。

        - `discord_channel_id` のユニークインデックス（upsert 用）
        """
        await self._collection.create_index(
            [("discord_channel_id", ASCENDING)],
            unique=True,
        )

    async def upsert(
        self,
        discord_channel_id: int,
        channel_name: str,
        summary: str,
        summary_date: str,
    ) -> None:
        """チャンネルの要約を保存する（1 チャンネル 1 ドキュメントの upsert）。

        Args:
            discord_channel_id: 対象の Discord チャンネル ID。
            channel_name: `discord.channels` の登録名（Discord の現在名とは限らない）。
            summary: 要約本文。
            summary_date: 要約の対象日（解決済みタイムゾーンの暦日、ISO 日付文字列）。
        """
        await self._collection.update_one(
            {"discord_channel_id": discord_channel_id},
            {
                "$set": {
                    "discord_channel_id": discord_channel_id,
                    "channel_name": channel_name,
                    "summary": summary,
                    "summary_date": summary_date,
                    "updated_at": utc_now(),
                }
            },
            upsert=True,
        )

    async def find_by_channel_id(self, discord_channel_id: int) -> dict | None:
        """指定チャンネルの要約を 1 件返す。

        Args:
            discord_channel_id: 対象の Discord チャンネル ID。

        Returns:
            該当ドキュメント（`_id` は含まない）。存在しない場合は None。
        """
        return await self._collection.find_one(
            {"discord_channel_id": discord_channel_id},
            {"_id": 0},
        )
