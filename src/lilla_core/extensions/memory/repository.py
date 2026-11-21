"""覚えておくこと（remember。`remember` コレクション）のリポジトリ。

日付（`YYYY-MM-DD`）を `_id` に 1 日 1 件持ち、記録は ``entries`` 配列に
``{"content": 本文, "created_at": 追記時刻}`` の形で追記順に並べる。追記は ``$push``
だけで行い、既存の記録を読み直して全体を置き換えることはしない。
"""
from __future__ import annotations

from functools import lru_cache

from lilla_core.repository.mongo_client import create_mongo_client
from lilla_core.utils.datetime_utils import utc_now

#: コレクション名。
COLLECTION_NAME = "remember"


@lru_cache(maxsize=1)
def get_remember_repo() -> "RememberRepository":
    """RememberRepository のシングルトンインスタンスを返す。"""
    from lilla_core.core.config import get_config

    config = get_config()
    return RememberRepository(config.env.mongodb_uri, config.mongodb.db_name)


class RememberRepository:
    """覚えておくこと（remember）のリポジトリ。ドキュメントは ``_id``=日付・``entries``=記録の配列。"""

    def __init__(self, mongo_uri: str, db_name: str) -> None:
        """RememberRepository を初期化する。

        Args:
            mongo_uri: MongoDB の接続 URI。
            db_name: 使用するデータベース名。
        """
        client = create_mongo_client(mongo_uri)
        self._collection = client[db_name][COLLECTION_NAME]

    async def init_collection(self) -> None:
        """コレクションの初期化を行う（日付が ``_id`` のため追加のインデックスは不要）。"""

    async def get(self, date: str) -> dict | None:
        """指定日の記録のドキュメントを返す（無ければ None）。

        Args:
            date: 記録の日付（``YYYY-MM-DD``）。
        """
        return await self._collection.find_one({"_id": date})

    async def append_if_absent(self, date: str, content: str) -> bool:
        """指定日の記録に ``content`` を 1 件追記する。同じ本文が既にあれば追記しない。

        ドキュメントが無ければ空の ``entries`` で作ってから、「同じ本文を含まない」を
        条件にした ``$push`` で足す。重複の判定と追記が 1 回の更新で行われるため、
        同時に呼ばれても既存の記録が消えたり、同じ本文が二重に入ったりしない。

        Args:
            date: 記録の日付（``YYYY-MM-DD``）。
            content: 追記する本文。

        Returns:
            追記したら True、同じ本文が既にあって追記しなかったら False。
        """
        now = utc_now()
        await self._collection.update_one(
            {"_id": date},
            {"$setOnInsert": {"entries": [], "created_at": now, "updated_at": now}},
            upsert=True,
        )
        result = await self._collection.update_one(
            {"_id": date, "entries.content": {"$ne": content}},
            {"$push": {"entries": {"content": content, "created_at": now}},
             "$set": {"updated_at": now}},
        )
        return result.modified_count == 1
