from __future__ import annotations

import logging
from datetime import timedelta
from functools import lru_cache

from pymongo import ASCENDING

from lilla_core.repository.motor_client import create_motor_client
from lilla_core.utils.datetime_utils import utc_now

logger = logging.getLogger(__name__)

STATUS_PENDING = "pending"
STATUS_COMPLETED = "completed"


@lru_cache(maxsize=1)
def get_pending_tool_calls_repo() -> "PendingToolCallsRepository":
    """PendingToolCallsRepository のシングルトンインスタンスを返す。

    初回呼び出し時に設定を読み込んでインスタンスを生成し、以降は同じインスタンスを返す。

    Returns
    -------
    PendingToolCallsRepository
        PendingToolCallsRepository のインスタンス
    """
    from lilla_core.core.config import get_config
    config = get_config()
    return PendingToolCallsRepository(config.env.mongodb_uri, config.mongodb.db_name)


class PendingToolCallsRepository:
    """外部エージェントへの非同期依頼（結果待ち）を保持するリポジトリ。

    ドキュメント構造:

    - ``_id``: correlation_id（UUID 文字列）
    - ``created_at`` / ``expire_at``: 作成日時と有効期限
    - ``client_type``: 依頼元クライアント種別を表す任意の文字列（例: ``"discord"``）
    - ``discord_channel_id``: Discord の場合は依頼元チャンネル ID、それ以外は None
    - ``context``: 結果配送時に使う情報（``original_request`` / ``purpose``）
    - ``status``: ``"pending"`` または ``"completed"``
    """

    DEFAULT_EXPIRE_HOURS = 24

    def __init__(self, mongo_uri: str, db_name: str) -> None:
        """PendingToolCallsRepository を初期化する。

        Args:
            mongo_uri: MongoDB の接続 URI。
            db_name: 使用するデータベース名。
        """
        client = create_motor_client(mongo_uri)
        self._collection = client[db_name]["pending_tool_calls"]

    async def init_collection(self) -> None:
        """コレクションの初期化を行う（起動時の一元初期化用の薄いオーケストレーション層）。"""
        await self.ensure_indexes()

    async def ensure_indexes(self) -> None:
        """TTL インデックスを作成する。

        ``expire_at`` に ``expireAfterSeconds=0`` の TTL インデックスを張ることで、
        レコードごとに有効期限を変えられるようにする。
        """
        await self._collection.create_index(
            [("expire_at", ASCENDING)],
            expireAfterSeconds=0,
        )

    async def save(
        self,
        correlation_id: str,
        client_type: str,
        discord_channel_id: int | None,
        context: dict,
        expire_hours: int | None = None,
    ) -> None:
        """結果待ちの依頼を pending 状態で保存する。

        Args:
            correlation_id: 依頼を一意に識別する UUID 文字列（``_id`` になる）。
            client_type: 依頼元クライアント種別を表す任意の文字列（例: ``"discord"``）。
            discord_channel_id: 依頼元の Discord チャンネル ID。Discord 以外は None。
            context: 結果配送時に使う情報（``original_request`` / ``purpose``）。
            expire_hours: 有効期限（時間）。None の場合は ``DEFAULT_EXPIRE_HOURS``。
        """
        now = utc_now()
        hours = expire_hours or self.DEFAULT_EXPIRE_HOURS
        await self._collection.insert_one(
            {
                "_id": correlation_id,
                "created_at": now,
                "expire_at": now + timedelta(hours=hours),
                "client_type": client_type,
                "discord_channel_id": discord_channel_id,
                "context": context,
                "status": STATUS_PENDING,
            }
        )

    async def exists_pending(self, correlation_id: str) -> bool:
        """有効期限内かつ pending のレコードが存在するかを返す。

        外部エージェントからのメッセージを ``!toolresult`` コマンドに変換してよいかの
        判定に使う。TTL による削除には遅延があるため、``expire_at`` を明示的に比較する。

        Args:
            correlation_id: 判定する correlation_id。

        Returns:
            存在すれば True、存在しない・処理済み・期限切れの場合は False。
        """
        doc = await self._collection.find_one(
            {
                "_id": correlation_id,
                "status": STATUS_PENDING,
                "expire_at": {"$gt": utc_now()},
            },
            {"_id": 1},
        )
        return doc is not None

    async def complete(self, correlation_id: str) -> dict | None:
        """pending のレコードを原子的に completed へ更新し、更新前のドキュメントを返す。

        承認の二重押下などで結果が二重配送されないよう、状態遷移と取得を 1 回の操作で行う。

        Args:
            correlation_id: 完了させる correlation_id。

        Returns:
            更新前のドキュメント。既に completed・存在しない・期限切れの場合は None。
        """
        now = utc_now()
        return await self._collection.find_one_and_update(
            {
                "_id": correlation_id,
                "status": STATUS_PENDING,
                "expire_at": {"$gt": now},
            },
            {"$set": {"status": STATUS_COMPLETED, "completed_at": now}},
        )
