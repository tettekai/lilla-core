from __future__ import annotations

from functools import lru_cache

from bson import ObjectId
from lilla_core.repository.motor_client import create_motor_client
from pymongo import ASCENDING

from lilla_core.utils.datetime_utils import utc_now


@lru_cache(maxsize=1)
def get_user_memo_repo() -> "UserMemoRepository":
    """UserMemoRepository のシングルトンインスタンスを返す。

    初回呼び出し時に設定を読み込んでインスタンスを生成し、以降は同じインスタンスを返す。

    Returns
    -------
    UserMemoRepository
        UserMemoRepository のインスタンス
    """
    from lilla_core.core.config import get_config
    config = get_config()
    return UserMemoRepository(config.env.mongodb_uri, config.mongodb.db_name)


class UserMemoRepository:
    """ユーザーメモの永続化リポジトリ。"""

    def __init__(self, mongo_uri: str, db_name: str) -> None:
        client = create_motor_client(mongo_uri)
        self._collection = client[db_name]["user_memos"]

    async def init_collection(self) -> None:
        """コレクションの初期化を行う（起動時の一元初期化用の薄いオーケストレーション層）。"""
        await self.ensure_indexes()

    async def ensure_indexes(self) -> None:
        """インデックスを作成する。"""
        await self._collection.create_index([("created_at", ASCENDING)])

    async def get_active(self) -> list[dict]:
        """enabled=True のメモを作成日時順で返す。"""
        cursor = self._collection.find(
            {"enabled": True},
            {"_id": 1, "content": 1, "enabled": 1, "created_at": 1, "updated_at": 1},
        ).sort("created_at", ASCENDING)
        return await cursor.to_list(length=None)

    async def get_all(self) -> list[dict]:
        """全件を作成日時順で返す（ダッシュボード用）。"""
        cursor = self._collection.find(
            {},
            {"_id": 1, "content": 1, "enabled": 1, "created_at": 1, "updated_at": 1},
        ).sort("created_at", ASCENDING)
        return await cursor.to_list(length=None)

    async def add(self, content: str) -> dict:
        """メモを追加し、追加したドキュメントを返す。"""
        now = utc_now()
        doc = {"content": content, "enabled": True, "created_at": now, "updated_at": now}
        result = await self._collection.insert_one(doc)
        doc["_id"] = result.inserted_id
        return doc

    async def update(self, memo_id: str, content: str | None, enabled: bool | None) -> bool:
        """content または enabled を更新する。更新できた場合 True を返す。"""
        updates: dict = {"updated_at": utc_now()}
        if content is not None:
            updates["content"] = content
        if enabled is not None:
            updates["enabled"] = enabled
        result = await self._collection.update_one(
            {"_id": ObjectId(memo_id)}, {"$set": updates}
        )
        return result.matched_count > 0

    async def delete(self, memo_id: str) -> bool:
        """メモを削除する。削除できた場合 True を返す。"""
        result = await self._collection.delete_one({"_id": ObjectId(memo_id)})
        return result.deleted_count > 0
