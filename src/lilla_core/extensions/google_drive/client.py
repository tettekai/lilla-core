"""Google Drive / Google Sheets API クライアント。

`google-drive` 拡張の中身で、OAuth2 のトークン処理は `google-oauth` 拡張が
提供する `GoogleOAuthClient` に委譲する。この拡張が持つのは Drive 固有の
`CREDENTIAL_TYPE` / スコープと、Drive Files API・Sheets Values API の呼び出しだけ。
ホワイトリストの判定はここでは行わない（`access.py` の担当）。
"""
from __future__ import annotations

import json
import secrets
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Self
from urllib.parse import quote

from lilla_core.core.exceptions import ReauthenticationRequiredError
from lilla_core.core.http_util import send_http_request
from lilla_core.extensions.google_oauth.client import GoogleOAuthClient

if TYPE_CHECKING:
    from lilla_core.extensions.google_drive import GoogleDriveConfig


SCOPE_DRIVE_READONLY = "https://www.googleapis.com/auth/drive.readonly"
SCOPE_DRIVE_FILE = "https://www.googleapis.com/auth/drive.file"
SCOPE_DRIVE = "https://www.googleapis.com/auth/drive"
SCOPE_SPREADSHEETS_READONLY = "https://www.googleapis.com/auth/spreadsheets.readonly"

FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
SPREADSHEET_MIME_TYPE = "application/vnd.google-apps.spreadsheet"

_DRIVE_FILES_URL = "https://www.googleapis.com/drive/v3/files"
_DRIVE_UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"
_SHEETS_VALUES_URL = "https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{range}"

#: ファイルのメタデータとして取り出す項目（検索・取得・書き込みで共通）。
FILE_FIELDS = "id,name,mimeType,parents,trashed,modifiedTime,size"


def scopes_for(config: GoogleDriveConfig) -> list[str]:
    """設定から、認可フローで要求する OAuth スコープを決める。

    読み取りは常に `drive.readonly` と `spreadsheets.readonly`。書き込み可能フォルダが
    あるときだけ書き込みのスコープを足し、`write_scope` が `file` なら `drive.file`、
    `full` なら `drive`（`drive.readonly` を含むため置き換える）にする。

    Args:
        config: `extensions.google_drive` セクション。

    Returns:
        要求するスコープの URL のリスト。
    """
    if not config.writable_folders:
        return [SCOPE_DRIVE_READONLY, SCOPE_SPREADSHEETS_READONLY]
    if config.write_scope == "full":
        return [SCOPE_DRIVE, SCOPE_SPREADSHEETS_READONLY]
    return [SCOPE_DRIVE_READONLY, SCOPE_DRIVE_FILE, SCOPE_SPREADSHEETS_READONLY]


@lru_cache(maxsize=1)
def get_google_drive_client() -> "GoogleDriveClient":
    """GoogleDriveClient のシングルトンインスタンスを返す。

    初回呼び出し時に設定を読み込んでインスタンスを生成し、以降は同じインスタンスを返す。

    Returns:
        GoogleDriveClient のインスタンス。
    """
    return GoogleDriveClient.from_config()


class GoogleDriveClient(GoogleOAuthClient):
    """Google Drive / Google Sheets API クライアント。

    Drive と Sheets で認可を 1 つにまとめる（`CREDENTIAL_TYPE` は 1 つ）。
    スコープは設定（`write_scope` と書き込み可能フォルダの有無）で変わるため、
    `from_config()` がインスタンスの `SCOPES` を上書きする。
    """

    CREDENTIAL_TYPE = "google_drive"
    SCOPES = [SCOPE_DRIVE_READONLY, SCOPE_SPREADSHEETS_READONLY]

    @classmethod
    def from_config(cls) -> Self:
        """アプリ設定からクライアントを生成し、設定に応じたスコープを持たせる。

        Returns:
            GoogleDriveClient のインスタンス。
        """
        from lilla_core.core.config import get_section
        from lilla_core.extensions.google_drive import GoogleDriveConfig

        client = super().from_config()
        client.SCOPES = scopes_for(get_section("google-drive", GoogleDriveConfig))
        return client

    async def _get_access_token(self) -> str:
        """保存済みのトークンが今のスコープを満たすか確かめてから、アクセストークンを返す。

        設定を変えて必要なスコープが増えた場合（書き込み可能フォルダを足した・
        `write_scope` を `full` にした）、古いトークンのままでは API 側で拒否される。
        認可時に許可されたスコープ（`credentials` の `scopes`）が足りなければ
        再認証を求め、新しいスコープで認可し直してもらう。

        Raises:
            ReauthenticationRequiredError: スコープが足りない、またはトークンの
                取得・更新に失敗した場合。
        """
        creds = await self._repo.get_by_type(self.CREDENTIAL_TYPE)
        granted = (creds or {}).get("scopes")
        if granted is not None and not set(self.SCOPES) <= set(granted):
            raise ReauthenticationRequiredError(
                "Granted scopes do not cover the required scopes. Re-authentication is required."
            )
        return await super()._get_access_token()

    async def get_metadata(self, file_id: str) -> dict[str, Any]:
        """ファイル（フォルダ・スプレッドシートを含む）のメタデータを取得する。

        Args:
            file_id: Drive のファイル ID（呼び出し側で形式を検証済みであること）。

        Returns:
            `FILE_FIELDS` の項目を持つ dict。

        Raises:
            aiohttp.ClientResponseError: API がエラーを返した場合（存在しなければ 404）。
            ReauthenticationRequiredError: 再認証が必要な場合。
        """
        return await self._request_json(
            f"{_DRIVE_FILES_URL}/{file_id}",
            params={"fields": FILE_FIELDS, "supportsAllDrives": "true"},
        )

    async def search_files(
        self, q: str, *, page_size: int, page_token: str | None = None
    ) -> dict[str, Any]:
        """Drive の検索クエリでファイルを 1 ページ分検索する（共有ドライブも含む）。

        Args:
            q: Drive API の検索クエリ（`fullText contains '...'` など。エスケープ済みであること）。
            page_size: 1 ページの件数。
            page_token: 続きのページのトークン（最初のページなら None）。

        Returns:
            `files`（`FILE_FIELDS` の項目を持つ dict のリスト）と、続きがあれば
            `nextPageToken` を持つ dict。
        """
        params = {
            "q": q,
            "fields": f"nextPageToken,files({FILE_FIELDS})",
            "pageSize": str(page_size),
            "corpora": "allDrives",
            "includeItemsFromAllDrives": "true",
            "supportsAllDrives": "true",
        }
        if page_token:
            params["pageToken"] = page_token
        return await self._request_json(_DRIVE_FILES_URL, params=params)

    async def download_text(self, file_id: str) -> str:
        """ファイルの中身をテキストとして取得する（`alt=media`）。

        Args:
            file_id: Drive のファイル ID。

        Returns:
            ファイルの中身。
        """
        headers = await self._auth_headers()
        return await send_http_request(
            f"{_DRIVE_FILES_URL}/{file_id}",
            method="GET",
            params={"alt": "media", "supportsAllDrives": "true"},
            headers=headers,
        )

    async def create_text_file(
        self, name: str, parent_id: str, content: str, mime_type: str
    ) -> dict[str, Any]:
        """指定フォルダの直下にテキストファイルを新規作成する（multipart アップロード）。

        メタデータと本文を 1 回のリクエストで送るため、途中で失敗しても空のファイルは残らない。

        Args:
            name: ファイル名。
            parent_id: 作成先フォルダの ID。
            content: 本文（UTF-8 で送る）。
            mime_type: ファイルの MIME タイプ（`text/*`）。

        Returns:
            作成したファイルの `FILE_FIELDS` の項目を持つ dict。
        """
        metadata = json.dumps(
            {"name": name, "parents": [parent_id], "mimeType": mime_type}, ensure_ascii=False
        )
        boundary = _new_boundary(content)
        body = (
            f"--{boundary}\r\n"
            "Content-Type: application/json; charset=UTF-8\r\n\r\n"
            f"{metadata}\r\n"
            f"--{boundary}\r\n"
            f"Content-Type: {mime_type}; charset=UTF-8\r\n\r\n"
            f"{content}\r\n"
            f"--{boundary}--\r\n"
        ).encode("utf-8")
        headers = await self._auth_headers()
        headers["Content-Type"] = f"multipart/related; boundary={boundary}"
        response_text = await send_http_request(
            _DRIVE_UPLOAD_URL,
            method="POST",
            params={"uploadType": "multipart", "supportsAllDrives": "true", "fields": FILE_FIELDS},
            headers=headers,
            body=body,
        )
        return json.loads(response_text)

    async def update_text_file(
        self, file_id: str, content: str, mime_type: str
    ) -> dict[str, Any]:
        """既存ファイルの本文を丸ごと置き換える（`uploadType=media`。名前・置き場所は変えない）。

        Args:
            file_id: 上書きするファイルの ID。
            content: 新しい本文（UTF-8 で送る）。
            mime_type: 送る本文の MIME タイプ（既存ファイルのものをそのまま渡す）。

        Returns:
            更新したファイルの `FILE_FIELDS` の項目を持つ dict。
        """
        headers = await self._auth_headers()
        headers["Content-Type"] = f"{mime_type}; charset=UTF-8"
        response_text = await send_http_request(
            f"{_DRIVE_UPLOAD_URL}/{file_id}",
            method="PATCH",
            params={"uploadType": "media", "supportsAllDrives": "true", "fields": FILE_FIELDS},
            headers=headers,
            body=content.encode("utf-8"),
        )
        return json.loads(response_text)

    async def get_sheet_values(self, spreadsheet_id: str, range_: str) -> dict[str, Any]:
        """スプレッドシートの指定範囲の値（表示形式の文字列）を取得する。

        Args:
            spreadsheet_id: スプレッドシートの ID。
            range_: A1 記法の範囲（`シート1!A1:C10` など）。URL のパスに入れるため
                ここでエンコードする。

        Returns:
            Sheets API の `ValueRange`（`range` / `majorDimension` / `values`）。
        """
        url = _SHEETS_VALUES_URL.format(
            spreadsheet_id=spreadsheet_id, range=quote(range_, safe="")
        )
        return await self._request_json(url)


def _new_boundary(content: str) -> str:
    """本文に現れない multipart の境界文字列を作る。"""
    while True:
        boundary = f"lilla-{secrets.token_hex(16)}"
        if boundary not in content:
            return boundary
