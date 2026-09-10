"""ダッシュボードのログインセッションのリポジトリ。

``admin_sessions`` コレクションにセッション ID の SHA-256 ハッシュと有効期限を
保持する。生のセッション ID は Cookie にしか存在せず、DB には保存しない
（DB が漏れてもセッションを乗っ取れないようにするため）。

有効期限の扱いは 2 段構えになっている。

- **認証判定**: ``find_valid`` が ``expires_at > 現在時刻`` を毎回明示的に比較する
  （アプリケーションロジックが本丸）。
- **データ掃除**: ``expires_at`` の TTL インデックス（``expireAfterSeconds=0``）で
  期限切れレコードを MongoDB 側が自動削除する。TTL の削除には最大 1 分程度の
  遅延があるため、掃除専用と位置づける。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from functools import lru_cache

from pymongo import ASCENDING

from lilla_core.repository.motor_client import create_motor_client
from lilla_core.utils.datetime_utils import utc_now


@lru_cache(maxsize=1)
def get_admin_session_repo() -> "AdminSessionRepository":
    """AdminSessionRepository のシングルトンインスタンスを返す。

    初回呼び出し時に設定を読み込んでインスタンスを生成し、以降は同じインスタンスを返す。

    Returns
    -------
    AdminSessionRepository
        AdminSessionRepository のインスタンス
    """
    from lilla_core.core.config import get_config
    config = get_config()
    return AdminSessionRepository(config.env.mongodb_uri, config.mongodb.db_name)


class AdminSessionRepository:
    """ダッシュボードのログインセッションのリポジトリ。

    ドキュメント構造:

    - ``_id``: セッション ID の SHA-256 ハッシュ（16 進文字列）
    - ``session_id_hash``: 同上（検索・可読性のため明示フィールドとしても持つ）
    - ``expires_at``: 有効期限（UTC）
    - ``created_at``: 発行日時（UTC）
    """

    SESSION_TTL_DAYS = 30

    def __init__(self, mongo_uri: str, db_name: str) -> None:
        """AdminSessionRepository を初期化する。

        Args:
            mongo_uri: MongoDB の接続 URI。
            db_name: 使用するデータベース名。
        """
        client = create_motor_client(mongo_uri)
        self._collection = client[db_name]["admin_sessions"]

    async def init_collection(self) -> None:
        """コレクションの初期化を行う（起動時の一元初期化用の薄いオーケストレーション層）。"""
        await self.ensure_indexes()

    async def ensure_indexes(self) -> None:
        """TTL インデックスを作成する。

        ``expires_at`` に ``expireAfterSeconds=0`` の TTL インデックスを張ることで、
        レコードごとに有効期限を持たせる。ログアウト時は ``delete`` で即座に消す
        ため、この TTL は「ログアウトせず放置されたセッション」の掃除用途。
        """
        await self._collection.create_index(
            [("expires_at", ASCENDING)],
            expireAfterSeconds=0,
        )

    def default_expires_at(self) -> datetime:
        """現在時刻から ``SESSION_TTL_DAYS`` 後の有効期限を返す。

        Returns:
            有効期限（timezone-aware な UTC datetime）。
        """
        return utc_now() + timedelta(days=self.SESSION_TTL_DAYS)

    async def create(self, session_id_hash: str, expires_at: datetime) -> None:
        """セッションを保存する。

        Args:
            session_id_hash: セッション ID の SHA-256 ハッシュ（16 進文字列）。
            expires_at: 有効期限（timezone-aware な UTC datetime）。
        """
        await self._collection.insert_one(
            {
                "_id": session_id_hash,
                "session_id_hash": session_id_hash,
                "expires_at": expires_at,
                "created_at": utc_now(),
            }
        )

    async def find_valid(self, session_id_hash: str) -> dict | None:
        """有効期限内のセッションを取得する。

        TTL による物理削除には遅延があるため、``expires_at`` を明示的に比較して
        期限切れレコードを除外する。

        Args:
            session_id_hash: セッション ID の SHA-256 ハッシュ（16 進文字列）。

        Returns:
            有効なセッションのドキュメント。存在しない・期限切れの場合は None。
        """
        return await self._collection.find_one(
            {"_id": session_id_hash, "expires_at": {"$gt": utc_now()}}
        )

    async def delete(self, session_id_hash: str) -> bool:
        """セッションを削除する（ログアウト）。

        Args:
            session_id_hash: セッション ID の SHA-256 ハッシュ（16 進文字列）。

        Returns:
            削除できた場合は True、対象が存在しなかった場合は False。
        """
        result = await self._collection.delete_one({"_id": session_id_hash})
        return result.deleted_count > 0
