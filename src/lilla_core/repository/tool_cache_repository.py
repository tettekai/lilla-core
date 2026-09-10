from __future__ import annotations

from datetime import timedelta
from functools import lru_cache

from lilla_core.repository.motor_client import create_motor_client
from pymongo import ASCENDING

from lilla_core.utils.datetime_utils import utc_now


@lru_cache(maxsize=1)
def get_tool_cache_repo() -> "ToolCacheRepository":
    """ToolCacheRepository のシングルトンインスタンスを返す。

    初回呼び出し時に設定を読み込んでインスタンスを生成し、以降は同じインスタンスを返す。

    Returns
    -------
    ToolCacheRepository
        ToolCacheRepository のインスタンス
    """
    from lilla_core.core.config import get_config
    config = get_config()
    return ToolCacheRepository(config.env.mongodb_uri, config.mongodb.db_name)


class ToolCacheRepository:
    """ツール実行結果を TTL 付きでキャッシュするリポジトリ。"""

    def __init__(self, mongo_uri: str, db_name: str) -> None:
        """ToolCacheRepository を初期化する。

        Args:
            mongo_uri: MongoDB の接続 URI。
            db_name: 使用するデータベース名。
        """
        client = create_motor_client(mongo_uri)
        self._collection = client[db_name]["tool_cache"]

    async def init_collection(self) -> None:
        """コレクションの初期化を行う（起動時の一元初期化用の薄いオーケストレーション層）。"""
        await self.ensure_indexes()

    async def ensure_indexes(self) -> None:
        """インデックスを作成する。

        - (tool_name, args_key) のユニーク複合インデックス（upsert 用）
        - expires_at に TTL インデックス（expireAfterSeconds=0）
        """
        await self._collection.create_index(
            [("tool_name", ASCENDING), ("args_key", ASCENDING)],
            unique=True,
        )
        await self._collection.create_index(
            [("expires_at", ASCENDING)],
            expireAfterSeconds=0,
        )

    async def set(
        self, tool_name: str, args_key: str, data: str, expiration_minutes: int
    ) -> None:
        """ツール実行結果をキャッシュに保存する（upsert）。

        Args:
            tool_name: ツール名（YAML stem）。
            args_key: 引数を json.dumps(sort_keys=True) した文字列。
            data: ツール実行結果の文字列データ。
            expiration_minutes: キャッシュ有効期間（分）。
        """
        now = utc_now()
        expires_at = now + timedelta(minutes=expiration_minutes)
        await self._collection.update_one(
            {"tool_name": tool_name, "args_key": args_key},
            {
                "$set": {
                    "tool_name": tool_name,
                    "args_key": args_key,
                    "data": data,
                    "cached_at": now,
                    "expires_at": expires_at,
                }
            },
            upsert=True,
        )

    async def get_all_valid(self) -> list[dict]:
        """有効期限内のキャッシュレコードをすべて返す。

        TTL インデックスによる削除には遅延があるため、現在時刻と expires_at を
        明示的に比較して、まだ有効なドキュメントのみを返す。

        Returns:
            tool_name / args_key / data を含む dict のリスト。
        """
        now = utc_now()
        cursor = self._collection.find(
            {"expires_at": {"$gt": now}},
            {"_id": 0, "tool_name": 1, "args_key": 1, "data": 1},
        )
        return await cursor.to_list(length=None)
