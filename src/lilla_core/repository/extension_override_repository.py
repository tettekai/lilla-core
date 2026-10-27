from __future__ import annotations

from functools import lru_cache

from pymongo import ASCENDING

from lilla_core.repository.mongo_client import create_mongo_client

#: 拡張ごとの上書きを保持するコレクション名。特定のリポジトリや拡張パックに
#: 依存しない中立な名前にしておき、拡張の置き場所が変わってもデータ移行なしで読めるようにする。
COLLECTION_NAME = "extension_overrides"


@lru_cache(maxsize=1)
def get_extension_override_repo() -> "ExtensionOverrideRepository":
    """ExtensionOverrideRepository のシングルトンインスタンスを返す。

    初回呼び出し時に設定を読み込んでインスタンスを生成し、以降は同じインスタンスを返す。

    Returns
    -------
    ExtensionOverrideRepository
        ExtensionOverrideRepository のインスタンス
    """
    from lilla_core.core.config import get_config
    config = get_config()
    return ExtensionOverrideRepository(config.env.mongodb_uri, config.mongodb.db_name)


class ExtensionOverrideRepository:
    """拡張ごとの上書き（`extension_overrides`）を読むリポジトリ。

    1 拡張につき 1 ドキュメントで、`extension` に `Extension.name` を持つ（ユニーク）。
    ドキュメントは上書きしたときだけ存在し、トップレベルのキー（`prompt`、将来の
    `tasks` / `settings`）の一部だけを持ってよい。キーの解釈は呼び出し側が行い、
    このリポジトリはドキュメントをそのまま返す。今は読み込みだけを提供する。
    """

    def __init__(self, mongo_uri: str, db_name: str) -> None:
        """ExtensionOverrideRepository を初期化する。

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

    async def find_by_extensions(self, names: list[str]) -> dict[str, dict]:
        """指定した拡張名の上書きドキュメントを「拡張名 -> ドキュメント」で返す。

        上書きの無い拡張はキーごと含めない。`names` が空なら DB を読まない。

        Args:
            names: 対象の拡張名（`Extension.name`）。

        Returns:
            拡張名から上書きドキュメント（`_id` を除く）への dict。
        """
        if not names:
            return {}
        cursor = self._collection.find({"extension": {"$in": list(names)}}, {"_id": 0})
        docs = await cursor.to_list(length=None)
        return {
            doc["extension"]: doc
            for doc in docs
            if isinstance(doc.get("extension"), str)
        }
