from __future__ import annotations

from functools import lru_cache

from lilla_core.repository.motor_client import create_motor_client


@lru_cache(maxsize=1)
def get_credentials_repo() -> "CredentialsRepository":
    """CredentialsRepository のシングルトンインスタンスを返す。

    初回呼び出し時に設定を読み込んでインスタンスを生成し、以降は同じインスタンスを返す。

    Returns
    -------
    CredentialsRepository
        CredentialsRepository のインスタンス
    """
    from lilla_core.core.config import get_config
    config = get_config()
    return CredentialsRepository(config.env.mongodb_uri, config.mongodb.db_name)


class CredentialsRepository:
    """MongoDB credentials コレクションのリポジトリ。

    type ごとに1件の認証情報を管理する。
    """

    def __init__(self, mongo_uri: str, db_name: str) -> None:
        client = create_motor_client(mongo_uri)
        self._collection = client[db_name]["credentials"]

    async def get_by_type(self, credential_type: str) -> dict | None:
        """指定された type の認証情報を取得します。

        Parameters
        ----------
        credential_type : str
            認証情報の種別（例: "google_oauth"）

        Returns
        -------
        dict | None
            認証情報ドキュメント。存在しない場合は None。
        """
        doc = await self._collection.find_one({"type": credential_type}, {"_id": 0})
        return doc

    async def upsert(self, credential_type: str, data: dict) -> None:
        """指定された type の認証情報を作成または更新します。

        既存レコードがある場合は data のフィールドを上書きし、
        ない場合は新規作成します。

        Parameters
        ----------
        credential_type : str
            認証情報の種別（例: "google_oauth"）
        data : dict
            保存するデータ（type フィールドは自動付与）
        """
        await self._collection.update_one(
            {"type": credential_type},
            {"$set": {**data, "type": credential_type}},
            upsert=True,
        )
