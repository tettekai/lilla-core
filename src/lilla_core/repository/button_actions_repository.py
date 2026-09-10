from __future__ import annotations

from functools import lru_cache

from lilla_core.repository.motor_client import create_motor_client
from pymongo import ASCENDING

from lilla_core.utils.datetime_utils import utc_now


@lru_cache(maxsize=1)
def get_button_actions_repo() -> "ButtonActionsRepository":
    """ButtonActionsRepository のシングルトンインスタンスを返す。

    初回呼び出し時に設定を読み込んでインスタンスを生成し、以降は同じインスタンスを返す。

    Returns
    -------
    ButtonActionsRepository
        ButtonActionsRepository のインスタンス
    """
    from lilla_core.core.config import get_config
    config = get_config()
    return ButtonActionsRepository(config.env.mongodb_uri, config.mongodb.db_name)


class ButtonActionsRepository:
    """Discord ボタン押下で実行する保留中アクションのリポジトリ。"""

    TTL_SECONDS = 7 * 24 * 60 * 60  # 7 日間

    def __init__(self, mongo_uri: str, db_name: str) -> None:
        """ButtonActionsRepository を初期化する。

        Args:
            mongo_uri: MongoDB の接続 URI。
            db_name: 使用するデータベース名。
        """
        client = create_motor_client(mongo_uri)
        self._collection = client[db_name]["button_actions"]

    async def init_collection(self) -> None:
        """コレクションの初期化を行う（起動時の一元初期化用の薄いオーケストレーション層）。"""
        await self.ensure_indexes()

    async def ensure_indexes(self) -> None:
        """TTL インデックスを作成する。

        created_at フィールドに対して 7 日間の TTL インデックスを作成する。
        """
        await self._collection.create_index(
            [("created_at", ASCENDING)],
            expireAfterSeconds=self.TTL_SECONDS,
        )

    async def save(self, id: str, tool: str, params: dict, title: str) -> None:
        """保留中アクションを保存する。

        Args:
            id: アクションを一意に識別する UUID 文字列。
            tool: 押下時に呼び出すツール名（例: "llm_send_notification"）。
            params: ツールに渡す引数の dict。
            title: ユーザー向け表示用のタイトル。
        """
        await self._collection.insert_one(
            {
                "_id": id,
                "tool": tool,
                "params": params,
                "title": title,
                "created_at": utc_now(),
            }
        )

    async def find_one_and_delete(self, id: str) -> dict | None:
        """id でドキュメントを原子的に取得＆削除する。

        ボタンの二重押下時に同じアクションを 2 回実行しないよう、取得と削除を 1 回の
        操作で行う。

        Args:
            id: アクションを一意に識別する UUID 文字列。

        Returns:
            ドキュメントの dict。存在しない場合は None。
        """
        return await self._collection.find_one_and_delete({"_id": id})
