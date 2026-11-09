"""ノートを MongoDB に保存するリポジトリ。

1 ファイル = `notes` の 1 ドキュメント（`path` にユニークインデックス、`content` / `size`
（文字数）/ `created_at` / `updated_at`）。ディレクトリは持たず、パスの接頭辞から導く。

上書き（`overwrite`）・削除・画面からの復元の直前の内容は `notes_history` に 1 パス 1 件だけ
残す（`expire_at` の TTL で消える）。追記（`append`）は元の内容を消さないため残さない。

同時に書いたときは後勝ち（楽観的排他は持たない）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache

from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

from lilla_core.repository.mongo_client import create_mongo_client
from lilla_core.utils.datetime_utils import ensure_utc, utc_now

from .paths import prefix_regex

#: ノート本体のコレクション名。
NOTES_COLLECTION = "notes"
#: 直前の版のコレクション名。
HISTORY_COLLECTION = "notes_history"

_LIST_PROJECTION = {"_id": 0, "path": 1, "size": 1, "created_at": 1, "updated_at": 1}


@dataclass(frozen=True)
class NoteMeta:
    """本文を除いたノートの情報。"""

    path: str
    size: int
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True)
class Note:
    """ノート 1 件。"""

    path: str
    content: str
    created_at: datetime | None
    updated_at: datetime | None

    @property
    def size(self) -> int:
        """本文の文字数。"""
        return len(self.content)


@dataclass(frozen=True)
class NoteVersion:
    """直前の版 1 件。"""

    path: str
    content: str
    #: 版を残した理由（`overwrite` / `delete` / `restore`）。
    reason: str
    #: 版を残した日時。
    saved_at: datetime | None


class NoteExistsError(Exception):
    """`create` で既にある。"""


@lru_cache(maxsize=1)
def get_notes_repo() -> "NotesRepository":
    """NotesRepository のシングルトンを返す（初回に設定を読む）。"""
    from lilla_core.core.config import get_config

    config = get_config()
    return NotesRepository(config.env.mongodb_uri, config.mongodb.db_name)


def _meta(doc: dict) -> NoteMeta:
    """ドキュメントを `NoteMeta` にする。"""
    return NoteMeta(
        path=doc["path"],
        size=int(doc.get("size") or 0),
        created_at=_utc(doc.get("created_at")),
        updated_at=_utc(doc.get("updated_at")),
    )


def _note(doc: dict) -> Note:
    """ドキュメントを `Note` にする。"""
    return Note(
        path=doc["path"],
        content=doc.get("content") or "",
        created_at=_utc(doc.get("created_at")),
        updated_at=_utc(doc.get("updated_at")),
    )


def _utc(value) -> datetime | None:
    """datetime を timezone-aware な UTC にそろえる（無ければ None）。"""
    return ensure_utc(value) if isinstance(value, datetime) else None


class NotesRepository:
    """ノートと直前の版の永続化。"""

    def __init__(self, mongo_uri: str, db_name: str) -> None:
        client = create_mongo_client(mongo_uri)
        db = client[db_name]
        self._notes = db[NOTES_COLLECTION]
        self._history = db[HISTORY_COLLECTION]

    async def init_collection(self) -> None:
        """起動時にインデックスを作る。"""
        await self.ensure_indexes()

    async def ensure_indexes(self) -> None:
        """`path` のユニークインデックス・`updated_at`（同期用）・直前の版の TTL を作る。"""
        await self._notes.create_index([("path", ASCENDING)], unique=True)
        await self._notes.create_index([("updated_at", DESCENDING)])
        await self._history.create_index([("path", ASCENDING)], unique=True)
        await self._history.create_index([("expire_at", ASCENDING)], expireAfterSeconds=0)

    async def get(self, path: str) -> Note | None:
        """パスのノートを返す（無ければ None）。"""
        doc = await self._notes.find_one({"path": path})
        return _note(doc) if doc else None

    async def get_meta(self, path: str) -> NoteMeta | None:
        """パスのノートの情報（本文なし）を返す（無ければ None）。"""
        doc = await self._notes.find_one({"path": path}, _LIST_PROJECTION)
        return _meta(doc) if doc else None

    async def has_children(self, directory: str) -> bool:
        """ディレクトリの配下にノートが 1 つでもあれば True（空文字は全体）。"""
        doc = await self._notes.find_one(
            {"path": {"$regex": prefix_regex(directory)}}, {"_id": 1}
        )
        return doc is not None

    async def list_under(self, directory: str) -> list[NoteMeta]:
        """ディレクトリの配下（再帰）のノートの情報をパス順に返す（空文字は全件）。"""
        cursor = self._notes.find(
            {"path": {"$regex": prefix_regex(directory)}}, _LIST_PROJECTION
        ).sort("path", ASCENDING)
        return [_meta(doc) for doc in await cursor.to_list(length=None)]

    async def search(self, directory: str, query: str, limit: int) -> tuple[list[Note], bool]:
        """配下のノートのうち、本文に `query` を含むもの（大文字小文字を区別しない）を返す。

        Args:
            directory: 起点のディレクトリ（空文字は全体）。ファイルのパスでもよい。
            query: 探す文字列（正規表現としては解釈しない）。
            limit: 返すノートの上限。

        Returns:
            パス順のノートと、上限で切り詰めたか。
        """
        conditions: list[dict] = [
            {"content": {"$regex": re.escape(query), "$options": "i"}}
        ]
        if directory:
            conditions.append(
                {"$or": [{"path": directory}, {"path": {"$regex": prefix_regex(directory)}}]}
            )
        cursor = (
            self._notes.find({"$and": conditions}, {"_id": 0})
            .sort("path", ASCENDING)
            .limit(limit + 1)
        )
        docs = await cursor.to_list(length=None)
        return [_note(d) for d in docs[:limit]], len(docs) > limit

    async def create(self, path: str, content: str) -> None:
        """新しく作る。

        Raises:
            NoteExistsError: 既にある場合。
        """
        now = utc_now()
        try:
            await self._notes.insert_one(
                {
                    "path": path,
                    "content": content,
                    "size": len(content),
                    "created_at": now,
                    "updated_at": now,
                }
            )
        except DuplicateKeyError as e:
            raise NoteExistsError(path) from e

    async def overwrite(self, path: str, content: str, history_ttl: timedelta) -> bool:
        """丸ごと置き換える（無ければ作る）。置き換える前の内容は直前の版に残す。

        Returns:
            新しく作ったなら True。
        """
        now = utc_now()
        before = await self._notes.find_one_and_update(
            {"path": path},
            {
                "$set": {"content": content, "size": len(content), "updated_at": now},
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
            return_document=ReturnDocument.BEFORE,
        )
        if before is None:
            return True
        if before.get("content") != content:
            await self._save_version(path, before.get("content") or "", "overwrite", history_ttl)
        return False

    async def append(self, path: str, content: str) -> bool:
        """末尾に追記する（無ければ作る）。

        Returns:
            新しく作ったなら True。
        """
        now = utc_now()
        before = await self._notes.find_one_and_update(
            {"path": path},
            [
                {
                    "$set": {
                        "path": path,
                        "content": {"$concat": [{"$ifNull": ["$content", ""]}, content]},
                        "created_at": {"$ifNull": ["$created_at", now]},
                        "updated_at": now,
                    }
                },
                {"$set": {"size": {"$strLenCP": "$content"}}},
            ],
            upsert=True,
            return_document=ReturnDocument.BEFORE,
            projection={"_id": 1},
        )
        return before is None

    async def delete(self, path: str, history_ttl: timedelta) -> bool:
        """削除する。消す前の内容は直前の版に残す。

        Returns:
            消したなら True（無ければ False）。
        """
        before = await self._notes.find_one_and_delete({"path": path})
        if before is None:
            return False
        await self._save_version(path, before.get("content") or "", "delete", history_ttl)
        return True

    async def get_version(self, path: str) -> NoteVersion | None:
        """パスの直前の版を返す（無い・期限切れなら None）。"""
        doc = await self._history.find_one({"path": path})
        if not doc:
            return None
        expire_at = _utc(doc.get("expire_at"))
        if expire_at is not None and expire_at <= utc_now():
            # TTL の掃除は遅れることがあるため、期限はアプリ側でも見る。
            return None
        return NoteVersion(
            path=doc["path"],
            content=doc.get("content") or "",
            reason=doc.get("reason") or "",
            saved_at=_utc(doc.get("saved_at")),
        )

    async def restore(self, path: str, history_ttl: timedelta) -> bool:
        """直前の版を本体へ戻す。戻す前の今の内容（あれば）は直前の版として残す。

        Returns:
            戻したなら True（直前の版が無ければ False）。
        """
        version = await self.get_version(path)
        if version is None:
            return False
        now = utc_now()
        before = await self._notes.find_one_and_update(
            {"path": path},
            {
                "$set": {
                    "content": version.content,
                    "size": len(version.content),
                    "updated_at": now,
                },
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
            return_document=ReturnDocument.BEFORE,
        )
        if before is not None and before.get("content") != version.content:
            await self._save_version(path, before.get("content") or "", "restore", history_ttl)
        else:
            await self._history.delete_one({"path": path})
        return True

    async def _save_version(
        self, path: str, content: str, reason: str, history_ttl: timedelta
    ) -> None:
        """直前の版を 1 パス 1 件で残す（前の版は置き換える）。"""
        now = utc_now()
        await self._history.replace_one(
            {"path": path},
            {
                "path": path,
                "content": content,
                "reason": reason,
                "saved_at": now,
                "expire_at": now + history_ttl,
            },
            upsert=True,
        )
