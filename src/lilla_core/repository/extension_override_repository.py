from __future__ import annotations

from functools import lru_cache

from pymongo import ASCENDING

from lilla_core.repository.mongo_client import create_mongo_client
from lilla_core.utils.datetime_utils import utc_now

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
    このリポジトリはドキュメントをそのまま返す。書き込みは指定したキーだけを
    1 回の `update_one` で更新し、他のキー（`prompt` を変えたときの `tasks` など）は残す。
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

    async def find_one(self, name: str) -> dict | None:
        """1 つの拡張の上書きドキュメント（`_id` を除く）を返す。無ければ `None`。

        Args:
            name: 対象の拡張名（`Extension.name`）。
        """
        return await self._collection.find_one({"extension": name}, {"_id": 0})

    async def update_overrides(
        self,
        name: str,
        set_fields: dict[str, object],
        unset_fields: list[str],
    ) -> None:
        """1 つの拡張の上書きのうち、指定したキーだけを 1 回の更新でまとめて書き換える。

        キーはドット区切りのパス（`prompt` / `tasks.<ツール名>.schedule` など）で渡す。
        `set_fields` が空でなければドキュメントが無いときに作り（upsert）、`updated_at`
        （UTC）も書く。`unset_fields` だけならドキュメントを作らない。どちらも空なら
        何もしない。値の検証は呼び出し側が済ませておくこと。

        Args:
            name: 対象の拡張名（`Extension.name`）。
            set_fields: 「パス -> 値」。
            unset_fields: 消すパス。

        Raises:
            ValueError: パスの要素が空・`$` 始まり・`set` と `unset` の両方に同じパスがある場合。
        """
        for path in [*set_fields, *unset_fields]:
            _validate_path(path)
        if set(set_fields) & set(unset_fields):
            raise ValueError("A path cannot be both set and unset")
        if not set_fields and not unset_fields:
            return
        update: dict = {}
        if set_fields:
            update["$set"] = {**set_fields, "updated_at": utc_now()}
        if unset_fields:
            update["$unset"] = {path: "" for path in unset_fields}
        await self._collection.update_one(
            {"extension": name}, update, upsert=bool(set_fields)
        )


def _validate_path(path: str) -> None:
    """更新に使うドット区切りのパスが安全な形かを確かめる。

    Raises:
        ValueError: 要素が空、または `$` で始まる要素がある場合。
    """
    if not isinstance(path, str) or not path:
        raise ValueError("Override path must be a non-empty string")
    for part in path.split("."):
        if not part or part.startswith("$"):
            raise ValueError(f"Invalid override path: {path!r}")
