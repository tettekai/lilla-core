from __future__ import annotations

from enum import Enum
from functools import lru_cache

from bson import ObjectId
from pymongo import ASCENDING
from pymongo.errors import DuplicateKeyError

from lilla_core.repository.mongo_client import create_mongo_client
from lilla_core.utils.datetime_utils import utc_now

_PROJECTION = {
    "_id": 1,
    "name": 1,
    "description": 1,
    "prompt": 1,
    "enabled": 1,
    "created_at": 1,
    "updated_at": 1,
}


@lru_cache(maxsize=1)
def get_skill_repo() -> "SkillRepository":
    """SkillRepository のシングルトンインスタンスを返す。

    初回呼び出し時に設定を読み込んでインスタンスを生成し、以降は同じインスタンスを返す。

    Returns
    -------
    SkillRepository
        SkillRepository のインスタンス
    """
    from lilla_core.core.config import get_config
    config = get_config()
    return SkillRepository(config.env.mongodb_uri, config.mongodb.db_name)


class SkillUpdateResult(Enum):
    """`SkillRepository.update()` の結果。"""

    UPDATED = "updated"
    NOT_FOUND = "not_found"
    DUPLICATE_NAME = "duplicate_name"


class SkillRepository:
    """スキル（名前付きの再利用プロンプト）の永続化リポジトリ。

    `name` は呼び出し側で小文字へ正規化済みのものを受け取る前提で、ユニーク
    インデックスで重複登録を防ぐ。
    """

    def __init__(self, mongo_uri: str, db_name: str) -> None:
        client = create_mongo_client(mongo_uri)
        self._collection = client[db_name]["skills"]

    async def init_collection(self) -> None:
        """コレクションの初期化を行う（起動時の一元初期化用の薄いオーケストレーション層）。"""
        await self.ensure_indexes()

    async def ensure_indexes(self) -> None:
        """インデックスを作成する。"""
        await self._collection.create_index([("name", ASCENDING)], unique=True)

    async def get_all(self) -> list[dict]:
        """全件を名前順で返す（ダッシュボード用）。"""
        cursor = self._collection.find({}, _PROJECTION).sort("name", ASCENDING)
        return await cursor.to_list(length=None)

    async def get_enabled(self) -> list[dict]:
        """有効なスキルだけを名前順で返す（`!skill` の一覧用）。

        `enabled` を持たないドキュメントは有効として扱う。
        """
        cursor = self._collection.find(
            {"enabled": {"$ne": False}}, _PROJECTION
        ).sort("name", ASCENDING)
        return await cursor.to_list(length=None)

    async def find_by_name(self, name: str) -> dict | None:
        """正規化済みの名前でスキルを 1 件取得する。無ければ None を返す。"""
        return await self._collection.find_one({"name": name}, _PROJECTION)

    async def add(self, name: str, description: str, prompt: str) -> dict | None:
        """スキルを追加し、追加したドキュメントを返す。同名が既にあれば None を返す。"""
        now = utc_now()
        doc = {
            "name": name,
            "description": description,
            "prompt": prompt,
            "enabled": True,
            "created_at": now,
            "updated_at": now,
        }
        try:
            result = await self._collection.insert_one(doc)
        except DuplicateKeyError:
            return None
        doc["_id"] = result.inserted_id
        return doc

    async def update(
        self,
        skill_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        prompt: str | None = None,
        enabled: bool | None = None,
    ) -> SkillUpdateResult:
        """指定したフィールドだけを更新する（None のフィールドは変えない）。"""
        updates: dict = {"updated_at": utc_now()}
        if name is not None:
            updates["name"] = name
        if description is not None:
            updates["description"] = description
        if prompt is not None:
            updates["prompt"] = prompt
        if enabled is not None:
            updates["enabled"] = enabled
        try:
            result = await self._collection.update_one(
                {"_id": ObjectId(skill_id)}, {"$set": updates}
            )
        except DuplicateKeyError:
            return SkillUpdateResult.DUPLICATE_NAME
        if result.matched_count == 0:
            return SkillUpdateResult.NOT_FOUND
        return SkillUpdateResult.UPDATED

    async def delete(self, skill_id: str) -> bool:
        """スキルを削除する。削除できた場合 True を返す。"""
        result = await self._collection.delete_one({"_id": ObjectId(skill_id)})
        return result.deleted_count > 0
