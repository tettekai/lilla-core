"""ダッシュボード管理者パスワードのリポジトリ。

``admin_credentials`` コレクションに bcrypt ハッシュを **1 レコードだけ** 保持する。
パスワードの変更・リセット UI は用意していないため、パスワードを忘れた場合は
MongoDB 上のレコードを手動削除すると ``/setup`` が再度有効になる。
"""
from __future__ import annotations

from functools import lru_cache

from lilla_core.repository.motor_client import create_motor_client

from lilla_core.utils.datetime_utils import utc_now

# 単一レコードであることを DB レベルで保証するための固定 _id
CREDENTIAL_ID = "admin"


@lru_cache(maxsize=1)
def get_admin_credential_repo() -> "AdminCredentialRepository":
    """AdminCredentialRepository のシングルトンインスタンスを返す。

    初回呼び出し時に設定を読み込んでインスタンスを生成し、以降は同じインスタンスを返す。

    Returns
    -------
    AdminCredentialRepository
        AdminCredentialRepository のインスタンス
    """
    from lilla_core.core.config import get_config
    config = get_config()
    return AdminCredentialRepository(config.env.mongodb_uri, config.mongodb.db_name)


class AdminCredentialRepository:
    """ダッシュボード管理者パスワード（bcrypt ハッシュ）のリポジトリ。

    ドキュメント構造:

    - ``_id``: 固定値 ``"admin"``（レコードを 1 件に制限するため）
    - ``password_hash``: bcrypt ハッシュ（bytes）
    - ``created_at``: 登録日時（UTC）
    """

    def __init__(self, mongo_uri: str, db_name: str) -> None:
        """AdminCredentialRepository を初期化する。

        Args:
            mongo_uri: MongoDB の接続 URI。
            db_name: 使用するデータベース名。
        """
        client = create_motor_client(mongo_uri)
        self._collection = client[db_name]["admin_credentials"]

    async def init_collection(self) -> None:
        """コレクションの初期化を行う（起動時の一元初期化用の薄いオーケストレーション層）。"""
        await self.ensure_indexes()

    async def ensure_indexes(self) -> None:
        """インデックスを作成する。

        ``_id`` を固定値にしてレコードを 1 件に制限しているため、追加の
        インデックスは不要。インターフェースを他リポジトリと揃えるために定義する。
        """
        return None

    async def exists(self) -> bool:
        """パスワードが登録済みかを返す。

        Returns:
            登録済みなら True、未登録なら False。
        """
        doc = await self._collection.find_one({}, {"_id": 1})
        return doc is not None

    async def get_password_hash(self) -> bytes | None:
        """登録済みの bcrypt ハッシュを返す。

        Returns:
            bcrypt ハッシュ（bytes）。未登録の場合は None。
        """
        doc = await self._collection.find_one({}, {"password_hash": 1})
        if not doc:
            return None
        password_hash = doc.get("password_hash")
        if password_hash is None:
            return None
        return bytes(password_hash)

    async def save_password_hash(self, password_hash: bytes) -> bool:
        """bcrypt ハッシュを登録する。

        既にレコードが存在する場合は上書きせず False を返す（再設定の禁止を
        DB レベルでも担保するため）。

        Args:
            password_hash: 保存する bcrypt ハッシュ。

        Returns:
            登録できた場合は True、既に登録済みで何もしなかった場合は False。
        """
        result = await self._collection.update_one(
            {"_id": CREDENTIAL_ID},
            {
                "$setOnInsert": {
                    "password_hash": password_hash,
                    "created_at": utc_now(),
                }
            },
            upsert=True,
        )
        return result.upserted_id is not None
