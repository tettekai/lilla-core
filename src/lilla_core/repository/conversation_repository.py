from __future__ import annotations

import re
from datetime import datetime
from functools import lru_cache

from bson import ObjectId
from lilla_core.repository.motor_client import create_motor_client
from pymongo import ASCENDING, DESCENDING

from lilla_core.utils.datetime_utils import utc_now


@lru_cache(maxsize=1)
def get_conversation_repo() -> "ConversationRepository":
    """ConversationRepository のシングルトンインスタンスを返す。

    初回呼び出し時に設定を読み込んでインスタンスを生成し、以降は同じインスタンスを返す。

    Returns
    -------
    ConversationRepository
        ConversationRepository のインスタンス
    """
    from lilla_core.core.config import get_config
    config = get_config()
    return ConversationRepository(
        config.env.mongodb_uri, config.mongodb.db_name, config.memory.conversation_ttl_hours
    )


class ConversationRepository:
    def __init__(self, mongo_uri: str, db_name: str, ttl_hours: int = 72) -> None:
        client = create_motor_client(mongo_uri)
        self._collection = client[db_name]["conversations"]
        self._ttl_hours = ttl_hours

    async def init_collection(self) -> None:
        """コレクションの初期化を行う（起動時の一元初期化用の薄いオーケストレーション層）。"""
        await self.ensure_indexes()

    async def ensure_indexes(self) -> None:
        """インデックスを作成します。ttl_hours が 0 の場合は TTL なしの通常インデックスを作成します。

        あわせて `(discord_channel_id, time)` の複合インデックスを作成します
        （チャンネル単位・期間指定の取得に使います。`discord_channel_id` を持たない
        既存ドキュメントも通常のインデックスに含まれます）。
        """
        if self._ttl_hours == 0:
            await self._collection.create_index([("time", ASCENDING)])
        else:
            await self._collection.create_index(
                [("time", ASCENDING)],
                expireAfterSeconds=self._ttl_hours * 3600,
            )
        await self._collection.create_index(
            [("discord_channel_id", ASCENDING), ("time", ASCENDING)],
        )

    async def load(self, max_turns: int) -> list[dict]:
        """会話履歴を新しい順に最大 max_turns 件取得し、時系列順に返します。"""
        cursor = self._collection.find(
            {},
            {"_id": 0, "message": 1, "time": 1},
        ).sort("time", DESCENDING).limit(max_turns)
        docs = await cursor.to_list(length=max_turns)
        docs.reverse()
        return [doc["message"] for doc in docs]

    async def load_with_time(self, max_turns: int) -> list[dict]:
        """時刻付きで会話履歴を返す。戻り値は {"message": {...}, "time": datetime} のリスト。"""
        cursor = self._collection.find(
            {},
            {"_id": 0, "message": 1, "time": 1},
        ).sort("time", DESCENDING).limit(max_turns)
        docs = await cursor.to_list(length=max_turns)
        docs.reverse()
        return docs

    async def load_with_time_since(self, since: datetime, max_turns: int) -> list[dict]:
        """since 以降の会話を時系列順に返す。max_turns を超える場合は末尾（直近）を残す。"""
        cursor = self._collection.find(
            {"time": {"$gte": since}},
            {"_id": 0, "message": 1, "time": 1},
        ).sort("time", ASCENDING)
        docs = await cursor.to_list(length=None)
        if len(docs) > max_turns:
            docs = docs[-max_turns:]
        return docs

    async def load_by_channel_between(
        self,
        discord_channel_id: int,
        start: datetime,
        end: datetime,
        max_turns: int,
    ) -> list[dict]:
        """指定チャンネルの `[start, end)` の会話を時系列順に返す。

        `discord_channel_id` を持たない既存ドキュメントは対象外（穴埋めはしない）。
        `max_turns` を超える場合は末尾（直近）を残す。

        Args:
            discord_channel_id: 対象の Discord チャンネル ID。
            start: 取得範囲の下限（この時刻を含む。UTC 推奨）。
            end: 取得範囲の上限（この時刻を含まない。UTC 推奨）。
            max_turns: 返す最大件数。

        Returns:
            {"message": {...}, "time": datetime} のリスト（古い順）。
        """
        cursor = self._collection.find(
            {
                "discord_channel_id": discord_channel_id,
                "time": {"$gte": start, "$lt": end},
            },
            {"_id": 0, "message": 1, "time": 1},
        ).sort("time", ASCENDING)
        docs = await cursor.to_list(length=None)
        if len(docs) > max_turns:
            docs = docs[-max_turns:]
        return docs

    async def search(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
        keywords: list[str] | None = None,
        role: str | None = None,
        discord_channel_id: int | None = None,
        limit: int = 30,
    ) -> list[dict]:
        """条件を指定して会話履歴を新しい順に検索する。

        条件はすべて任意で、指定されたものだけを AND で重ねる。`keywords` は
        `message.content` に対する部分一致（大文字小文字を区別しない）で、
        すべてを含むものだけを返す。利用者・LLM 由来の文字列をそのまま正規表現
        として解釈させないよう、キーワードはエスケープしてから渡す。

        Args:
            start: 取得範囲の下限（この時刻を含む。UTC 推奨）。`None` なら下限なし。
            end: 取得範囲の上限（この時刻を含む。UTC 推奨）。`None` なら上限なし。
            keywords: すべて含むべきキーワードのリスト。`None` / 空なら絞り込まない。
            role: 絞り込む発言者（`"user"` / `"assistant"` など）。`None` なら絞り込まない。
            discord_channel_id: 絞り込む Discord チャンネル ID。`None` なら全チャンネル横断。
            limit: 返す最大件数。

        Returns:
            {"message": {...}, "time": datetime} のリスト（新しい順）。
        """
        mongo_filter: dict = {}

        if start is not None or end is not None:
            time_filter: dict = {}
            if start is not None:
                time_filter["$gte"] = start
            if end is not None:
                time_filter["$lte"] = end
            mongo_filter["time"] = time_filter

        if keywords:
            mongo_filter["$and"] = [
                {"message.content": {"$regex": re.escape(keyword), "$options": "i"}}
                for keyword in keywords
            ]

        if role:
            mongo_filter["message.role"] = role

        if discord_channel_id is not None:
            mongo_filter["discord_channel_id"] = discord_channel_id

        cursor = self._collection.find(
            mongo_filter,
            {"_id": 0, "message": 1, "time": 1},
        ).sort("time", DESCENDING).limit(limit)
        return await cursor.to_list(length=limit)

    async def list_for_client(self, limit: int, offset: int) -> list[dict]:
        """クライアント向けに会話履歴を新しい順で返す。

        戻り値は {"_id": str, "time": datetime, "message": {...}} のリスト。
        """
        cursor = (
            self._collection.find(
                {},
                {"_id": 1, "message": 1, "time": 1},
            )
            .sort("time", DESCENDING)
            .skip(offset)
            .limit(limit)
        )
        docs = await cursor.to_list(length=limit)
        return [
            {
                "_id": str(doc["_id"]),
                "time": doc["time"],
                "message": doc.get("message", {}),
            }
            for doc in docs
        ]

    async def save(
        self,
        message: dict,
        tags: list[str] | None = None,
        discord_channel_id: int | None = None,
        discord_message_ids: list[int] | None = None,
    ) -> str:
        """メッセージを会話履歴に保存し、保存したドキュメントの _id を返します。

        Args:
            message: 保存する会話メッセージ（role / content）。
            tags: エントリの分類タグ。空・None の場合はフィールドを保存しない。
            discord_channel_id: 対応する Discord メッセージのチャンネル ID。
            discord_message_ids: 対応する Discord メッセージの ID リスト
                （長文が複数チャンクに分割された場合に備えてリストで持つ）。

        Returns:
            保存したドキュメントの _id（文字列）。
        """
        doc: dict = {
            "message": message,
            "time": utc_now(),
        }
        if tags:
            doc["tags"] = tags
        if discord_channel_id is not None:
            doc["discord_channel_id"] = discord_channel_id
        if discord_message_ids:
            doc["discord_message_ids"] = discord_message_ids
        result = await self._collection.insert_one(doc)
        return str(result.inserted_id)

    async def find_latest_by_tag(self, tag: str) -> dict | None:
        """指定タグを持つ会話履歴のうち最新の 1 件を返します。

        Args:
            tag: 検索対象のタグ（例: "dirty"）。

        Returns:
            該当ドキュメント（_id を含む）。存在しない場合は None。
        """
        cursor = self._collection.find({"tags": tag}).sort("time", DESCENDING).limit(1)
        docs = await cursor.to_list(length=1)
        return docs[0] if docs else None

    async def delete(self, id: str) -> bool:
        """指定 ID の会話履歴を削除します。

        Args:
            id: 削除するドキュメントの _id（文字列）。

        Returns:
            削除できた場合は True、該当がなければ False。
        """
        result = await self._collection.delete_one({"_id": ObjectId(id)})
        return result.deleted_count > 0
