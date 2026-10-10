"""共有 HTTP サーバーの Bearer 認証に使うクライアントトークンのリポジトリ。

``client_tokens`` コレクションにトークンの SHA-256 ハッシュだけを保持する。
平文トークンは発行時（ホストが用意する発行スクリプトなどの実行時）にしか存在せず、
DB には保存しない（DB が漏れてもトークンを復元できないようにするため）。

有効期限は設けない（無期限トークン）。漏洩時は同じ ``label`` で再発行し、
古いレコードを置き換えることで実質的に失効させる運用とする（``replace``）。
インデックスは共有 HTTP サーバーの起動時（``handlers/http_server.py``）に作る。
"""
from __future__ import annotations

import hashlib
from functools import lru_cache

from pymongo import ASCENDING

from lilla_core.repository.mongo_client import create_mongo_client
from lilla_core.utils.datetime_utils import utc_now

#: 発行対象名を指定しないときの既定値（特定のクライアント名は入れない）
DEFAULT_LABEL = "default"


def hash_client_token(token: str) -> str:
    """平文トークンを DB 保存・照合用の SHA-256 ハッシュへ変換する。

    発行側と認証側で同じ計算式を共有するため、保存形式を知っているこのモジュールに置く。

    Args:
        token: 平文トークン。

    Returns:
        SHA-256 ハッシュの 16 進文字列。
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def verify_client_token(token: str) -> bool:
    """受け取った平文トークンが発行済みかを検証する。

    平文トークンは DB に保存していないため、SHA-256 ハッシュ化してから
    ``client_tokens`` を引く。共有 HTTP サーバーの Bearer 認証
    （``handlers/http_server.py``）のほか、拡張が独自方式で保護するルート
    （WebSocket の接続後認証など）からも使えるよう、リポジトリ層に置く。

    Args:
        token: クライアントから受け取った平文トークン。

    Returns:
        一致するレコードがあれば True。空文字の場合は DB を引かずに False。
    """
    if not token:
        return False

    doc = await get_client_token_repo().find_by_token_hash(hash_client_token(token))
    return doc is not None


@lru_cache(maxsize=1)
def get_client_token_repo() -> "ClientTokenRepository":
    """ClientTokenRepository のシングルトンインスタンスを返す。

    初回呼び出し時に設定を読み込んでインスタンスを生成し、以降は同じインスタンスを返す。

    Returns
    -------
    ClientTokenRepository
        ClientTokenRepository のインスタンス
    """
    from lilla_core.core.config import get_config
    config = get_config()
    return ClientTokenRepository(config.env.mongodb_uri, config.mongodb.db_name)


class ClientTokenRepository:
    """クライアント用 Bearer トークン（SHA-256 ハッシュ）のリポジトリ。

    ドキュメント構造:

    - ``_id``: トークンの SHA-256 ハッシュ（16 進文字列）
    - ``token_hash``: 同上（検索・可読性のため明示フィールドとしても持つ）
    - ``label``: 発行対象の識別名（既定は ``DEFAULT_LABEL``）
    - ``created_at``: 発行日時（UTC）
    """

    def __init__(self, mongo_uri: str, db_name: str) -> None:
        """ClientTokenRepository を初期化する。

        Args:
            mongo_uri: MongoDB の接続 URI。
            db_name: 使用するデータベース名。
        """
        client = create_mongo_client(mongo_uri)
        self._collection = client[db_name]["client_tokens"]

    async def init_collection(self) -> None:
        """コレクションの初期化を行う（起動時の一元初期化用の薄いオーケストレーション層）。"""
        await self.ensure_indexes()

    async def ensure_indexes(self) -> None:
        """インデックスを作成する。

        認証のたびに ``token_hash`` で検索するためインデックスを張る。``label`` は
        発行スクリプトが「同じ発行対象の既存トークン」を探すときに使う。
        有効期限を持たないため TTL インデックスは作らない。
        """
        await self._collection.create_index([("token_hash", ASCENDING)], unique=True)
        await self._collection.create_index([("label", ASCENDING)])

    async def find_by_token_hash(self, token_hash: str) -> dict | None:
        """トークンハッシュに一致するレコードを返す。

        Args:
            token_hash: トークンの SHA-256 ハッシュ（16 進文字列）。

        Returns:
            一致したドキュメント。存在しない場合は None。
        """
        return await self._collection.find_one({"token_hash": token_hash})

    async def exists_by_label(self, label: str) -> bool:
        """指定した発行対象のトークンが既に存在するかを返す。

        Args:
            label: 発行対象の識別名。

        Returns:
            1 件以上存在すれば True。
        """
        doc = await self._collection.find_one({"label": label}, {"_id": 1})
        return doc is not None

    async def delete_by_label(self, label: str) -> int:
        """指定した発行対象のトークンをすべて削除する（再発行時の置き換え用）。

        Args:
            label: 発行対象の識別名。

        Returns:
            削除した件数。
        """
        result = await self._collection.delete_many({"label": label})
        return result.deleted_count

    async def create(self, token_hash: str, label: str) -> None:
        """トークンハッシュを保存する。

        Args:
            token_hash: トークンの SHA-256 ハッシュ（16 進文字列）。
            label: 発行対象の識別名。
        """
        await self._collection.insert_one(
            {
                "_id": token_hash,
                "token_hash": token_hash,
                "label": label,
                "created_at": utc_now(),
            }
        )

    async def replace(self, token_hash: str, label: str) -> None:
        """同じ発行対象の既存トークンを消してから、新しいトークンハッシュを保存する。

        1 label = 1 token の運用で、再発行＝古いトークンの失効になる。

        Args:
            token_hash: 新しいトークンの SHA-256 ハッシュ（16 進文字列）。
            label: 発行対象の識別名。
        """
        await self.delete_by_label(label)
        await self.create(token_hash, label)
