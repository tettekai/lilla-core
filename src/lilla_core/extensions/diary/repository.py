"""日記ドラフト（`diary_drafts` コレクション）のリポジトリ。

日付（`YYYY-MM-DD`）を `_id` に、日記の Markdown 本文をそのまま 1 件保存する。
欄ごとの構造化データは持たない（見出しの増減はテンプレファイルの変更で済ませる）。
"""
from __future__ import annotations

from functools import lru_cache

from lilla_core.repository.mongo_client import create_mongo_client
from lilla_core.utils.datetime_utils import utc_now


@lru_cache(maxsize=1)
def get_diary_draft_repo() -> "DiaryDraftRepository":
    """DiaryDraftRepository のシングルトンインスタンスを返す。

    Returns
    -------
    DiaryDraftRepository
        DiaryDraftRepository のインスタンス
    """
    from lilla_core.core.config import get_config
    config = get_config()
    return DiaryDraftRepository(config.env.mongodb_uri, config.mongodb.db_name)


class DiaryDraftRepository:
    """日記ドラフトのリポジトリ。ドキュメントは ``_id``=日付・``content``=Markdown 本文。"""

    def __init__(self, mongo_uri: str, db_name: str) -> None:
        """DiaryDraftRepository を初期化する。

        Args:
            mongo_uri: MongoDB の接続 URI。
            db_name: 使用するデータベース名。
        """
        client = create_mongo_client(mongo_uri)
        self._collection = client[db_name]["diary_drafts"]

    async def init_collection(self) -> None:
        """コレクションの初期化を行う（日付が ``_id`` のため追加のインデックスは不要）。"""

    async def get(self, date: str) -> dict | None:
        """指定日のドラフトを返す。

        Args:
            date: 日記の日付（``YYYY-MM-DD``）。

        Returns:
            ドラフトのドキュメント。無ければ None。
        """
        return await self._collection.find_one({"_id": date})

    async def create_if_absent(self, date: str, content: str) -> dict:
        """指定日のドラフトが無ければ ``content`` で作り、保存済みのドキュメントを返す。

        upsert の ``$setOnInsert`` で作るため、同時に呼ばれても先に作られたものが残る。

        Args:
            date: 日記の日付（``YYYY-MM-DD``）。
            content: 新規作成時に保存する Markdown 本文。

        Returns:
            保存されているドラフトのドキュメント。
        """
        now = utc_now()
        await self._collection.update_one(
            {"_id": date},
            {"$setOnInsert": {"content": content, "created_at": now, "updated_at": now}},
            upsert=True,
        )
        return await self._collection.find_one({"_id": date})

    async def set_content(self, date: str, content: str) -> None:
        """指定日のドラフト本文を丸ごと置き換える（無ければ作る）。

        Args:
            date: 日記の日付（``YYYY-MM-DD``）。
            content: 保存する Markdown 本文。
        """
        now = utc_now()
        await self._collection.update_one(
            {"_id": date},
            {"$set": {"content": content, "updated_at": now},
             "$setOnInsert": {"created_at": now}},
            upsert=True,
        )
