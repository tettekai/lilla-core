"""`!mongodata` と同じ形で MongoDB へ書き込むヘルパー（opt-in）。

task ツール（`type: self` を含む）が外部から取得したデータを保存するときに、
`create_mongo_client` や `get_config` を直接触らずに済むようにする。`!mongodata`
コマンドも同じ関数で書き込むため、コマンド経由とツール経由で保存される形はそろう。

書き込めるのは `commands.mongodata.allowed_collections` に並べたコレクションだけ
（`!mongodata` と同じホワイトリスト）。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class MongoDataError(ValueError):
    """`save_document` に渡した内容が書き込みの条件を満たさないことを表す基底例外。"""


class CollectionNotAllowedError(MongoDataError):
    """コレクション名が空、または `allowed_collections` に無いことを表す。"""

    def __init__(self, collection: str) -> None:
        """対象のコレクション名を受け取る。"""
        super().__init__(f"collection is not allowed: {collection!r}")
        self.collection = collection


class KeyFieldMissingError(MongoDataError):
    """`key` に指定したフィールドが payload に無い（値が None）ことを表す。"""

    def __init__(self, key: str) -> None:
        """対象のキー名を受け取る。"""
        super().__init__(f"key field is missing in payload: {key!r}")
        self.key = key


async def save_document(collection: str, payload: dict, key: str | None = None) -> None:
    """`!mongodata` と同じ形でドキュメントを 1 件書き込む。

    `key` を渡すと `{key: payload[key]}` を条件に `$set` で upsert し、省略すると
    insert する。payload の `_id` は取り除いてから書く（呼び出し元の dict は変えない）。

    Args:
        collection: 書き込み先のコレクション名。`commands.mongodata.allowed_collections`
            に含まれている必要がある。
        payload: 書き込むドキュメント。
        key: upsert のキーにするフィールド名。省略時は insert。

    Raises:
        CollectionNotAllowedError: コレクション名が空、または許可されていないとき。
        TypeError: payload が dict でないとき。
        KeyFieldMissingError: `key` のフィールドが payload に無いとき。
        Exception: MongoDB の操作に失敗したとき（そのまま伝える）。
    """
    from lilla_core.core.config import get_config
    from lilla_core.repository.mongo_client import create_mongo_client

    config = get_config()
    name = (collection or "").strip()
    if not name or name not in config.commands.mongodata.allowed_collections:
        raise CollectionNotAllowedError(name)
    if not isinstance(payload, dict):
        raise TypeError("payload must be a dict")

    document = {k: v for k, v in payload.items() if k != "_id"}
    col = create_mongo_client(config.env.mongodb_uri)[config.mongodb.db_name][name]

    if key:
        value = document.get(key)
        if value is None:
            raise KeyFieldMissingError(key)
        await col.update_one({key: value}, {"$set": document}, upsert=True)
        logger.info("mongodata: upsert completed collection=%s %s=%s", name, key, value)
    else:
        await col.insert_one(document)
        logger.info("mongodata: insert completed collection=%s", name)
