from __future__ import annotations

from functools import lru_cache

from pymongo import ASCENDING

from lilla_core.repository.mongo_client import create_mongo_client
from lilla_core.utils.datetime_utils import utc_now

#: 画面管理の拡張設定（設定の本体）を保持するコレクション名。既定への上書きを持つ
#: `extension_overrides` とは分けておく。
COLLECTION_NAME = "extension_settings"


@lru_cache(maxsize=1)
def get_extension_settings_repo() -> "ExtensionSettingsRepository":
    """ExtensionSettingsRepository のシングルトンインスタンスを返す。

    初回呼び出し時に設定を読み込んでインスタンスを生成し、以降は同じインスタンスを返す。

    Returns
    -------
    ExtensionSettingsRepository
        ExtensionSettingsRepository のインスタンス
    """
    from lilla_core.core.config import get_config
    config = get_config()
    return ExtensionSettingsRepository(config.env.mongodb_uri, config.mongodb.db_name)


class ExtensionSettingsRepository:
    """画面管理の拡張設定（`extension_settings`）を読み書きするリポジトリ。

    1 拡張につき 1 ドキュメントで、`extension` に `Extension.name`（ユニーク）、
    `values` に検証済みの設定一式、`updated_at` に保存時刻（UTC）を持つ。値の検証は
    呼び出し側（`services/extension_settings.py`）が行い、このリポジトリは一式を
    そのまま置き換えて保存する（項目ごとの部分更新はしない）。
    """

    def __init__(self, mongo_uri: str, db_name: str) -> None:
        """ExtensionSettingsRepository を初期化する。

        Args:
            mongo_uri: MongoDB の接続 URI。
            db_name: 使用するデータベース名。
        """
        client = create_mongo_client(mongo_uri)
        self._collection = client[db_name][COLLECTION_NAME]

    async def init_collection(self) -> None:
        """コレクションの初期化を行う（起動時の一元初期化用の薄いオーケストレーション層）。"""
        await self.ensure_indexes()

    async def ensure_indexes(self) -> None:
        """インデックスを作成する。

        - `extension` のユニークインデックス（1 拡張 1 ドキュメント）
        """
        await self._collection.create_index([("extension", ASCENDING)], unique=True)

    async def find_one(self, name: str) -> dict | None:
        """1 つの拡張の設定ドキュメント（`_id` を除く）を返す。保存していなければ `None`。

        Args:
            name: 拡張名（`Extension.name`）。
        """
        return await self._collection.find_one({"extension": name}, {"_id": 0})

    async def save(self, name: str, values: dict) -> None:
        """1 つの拡張の設定一式を 1 回の書き込みで置き換えて保存する（無ければ作る）。

        Args:
            name: 拡張名（`Extension.name`）。
            values: 検証済みの設定一式（JSON に変換できる値だけ）。
        """
        await self._collection.replace_one(
            {"extension": name},
            {"extension": name, "values": values, "updated_at": utc_now()},
            upsert=True,
        )
