"""Google Drive のテキストファイルを新規作成・上書きする LLM ツール。"""
from __future__ import annotations

import logging

import aiohttp

from lilla_core.tool_support.tool_result import tool_error, tool_success

logger = logging.getLogger(__name__)

SCHEMA = {
    "type": "function",
    "function": {
        "name": "google_drive_write_text",
        "description": (
            "Google Drive のテキストファイルを新規作成、または既存のテキストファイルの内容を"
            "丸ごと上書きします。書き込みが許可されたフォルダ配下だけが対象です。"
            "file_id を指定すると上書き、指定しなければ name と parent_folder_id で新規作成します。"
            "上書きの前には llm_google_drive_get_text で現在の内容を確認してください。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "書き込む内容（ファイル全体）",
                },
                "file_id": {
                    "type": "string",
                    "description": "上書きするファイルの ID。新規作成のときは指定しない",
                },
                "name": {
                    "type": "string",
                    "description": "新規作成するファイルの名前（拡張子込み。例: memo.md）。新規作成のときは必須",
                },
                "parent_folder_id": {
                    "type": "string",
                    "description": "新規作成先のフォルダ ID。新規作成のときは必須",
                },
            },
            "required": ["content"],
        },
    },
}

#: `write_scope: file` で、この拡張が作っていないファイルを上書きしようとしたときの文言。
_FILE_SCOPE_MESSAGE = (
    "このファイルは上書きできません（write_scope が file のため、"
    "Lilla が作成したファイルしか上書きできません）。"
)
#: `write_scope: file` で、新規作成を API に拒否されたときの文言（`drive.file` では、この拡張が
#: 作っていないフォルダへの作成が拒否されることがある）。
_FILE_SCOPE_CREATE_MESSAGE = (
    "このフォルダにはファイルを作成できませんでした（write_scope が file のため、"
    "Lilla が作成していないフォルダには作成できない可能性があります）。"
)
_READ_ONLY_MESSAGE = "このファイルがあるフォルダには書き込みが許可されていません。"
_READ_ONLY_FOLDER_MESSAGE = "このフォルダには書き込みが許可されていません。"
_FOLDER_NOT_FOUND_MESSAGE = "フォルダが見つからないか、アクセスが許可されていません。"


async def execute(input: dict, context: dict) -> dict:
    """Google Drive のテキストファイルを新規作成、または上書きする。

    上書きは `text/*` の既存ファイルに限り、MIME タイプ・名前・置き場所は変えない。
    新規作成は `parent_folder_id` のフォルダ直下に、名前から決めた `text/*` の
    MIME タイプで作る。どちらも書き込み可能フォルダの配下でなければ拒否する。

    Parameters
    ----------
    input : dict
        content: 書き込む内容（必須）
        file_id: 上書きするファイルの ID（任意。指定時は name / parent_folder_id を受け付けない）
        name: 新規作成するファイルの名前（新規作成時は必須）
        parent_folder_id: 新規作成先のフォルダ ID（新規作成時は必須）
    context : dict
        使わない（ホワイトリストは `extensions.google_drive` から読む）。

    Returns
    -------
    dict
        標準形式の結果辞書。`data` は書き込んだファイルの情報と `created`（新規作成なら True）。
    """
    from lilla_core.extensions.google_drive.access import Access, is_valid_drive_id
    from lilla_core.extensions.google_drive.client import FOLDER_MIME_TYPE
    from lilla_core.extensions.google_drive.tool_support import (
        NOT_FOUND_MESSAGE,
        build_resolver,
        guess_text_mime,
        is_visible_text_file,
        run_drive_operation,
        summarize_file,
    )

    tool_name = SCHEMA["function"]["name"]
    content = input.get("content")
    if not isinstance(content, str):
        return tool_error(tool_name, None, "content を文字列で指定してください。")
    file_id = input.get("file_id")
    name = input.get("name")
    parent_folder_id = input.get("parent_folder_id")

    if file_id is not None:
        if not is_valid_drive_id(file_id):
            return tool_error(tool_name, None, "file_id の形式が正しくありません。")
        if name is not None or parent_folder_id is not None:
            return tool_error(
                tool_name,
                None,
                "上書き（file_id 指定）では name と parent_folder_id を指定しないでください。",
            )
    else:
        if not isinstance(name, str) or not name.strip():
            return tool_error(tool_name, None, "新規作成では name を指定してください。")
        if parent_folder_id is None:
            return tool_error(tool_name, None, "新規作成では parent_folder_id を指定してください。")
        if not is_valid_drive_id(parent_folder_id):
            return tool_error(tool_name, None, "parent_folder_id の形式が正しくありません。")

    async def overwrite(client, config) -> dict:
        """既存のテキストファイルを上書きする。"""
        resolver = build_resolver(client, config)
        if resolver.is_empty:
            return tool_error(tool_name, None, NOT_FOUND_MESSAGE)
        metadata = await client.get_metadata(file_id)
        if not is_visible_text_file(metadata):
            return tool_error(tool_name, None, NOT_FOUND_MESSAGE)
        access = await resolver.resolve(file_id, metadata.get("parents"))
        if access is Access.NONE:
            return tool_error(tool_name, None, NOT_FOUND_MESSAGE)
        if access is not Access.WRITE:
            return tool_error(tool_name, None, _READ_ONLY_MESSAGE)

        try:
            updated = await client.update_text_file(file_id, content, metadata["mimeType"])
        except aiohttp.ClientResponseError as e:
            # drive.file では、この拡張が作っていないファイルの更新を API が拒否する。
            if config.write_scope == "file" and e.status in (403, 404):
                return tool_error(tool_name, None, _FILE_SCOPE_MESSAGE)
            raise

        data = summarize_file({**metadata, **updated}, writable=True)
        data["created"] = False
        memory_entry = (
            f"Google Drive のテキストファイル「{data['name']}」を上書きしました（{len(content)}文字）。"
        )
        return tool_success(tool_name, memory_entry, data)

    async def create(client, config) -> dict:
        """書き込み可能フォルダの直下にテキストファイルを新規作成する。"""
        resolver = build_resolver(client, config)
        if resolver.is_empty:
            return tool_error(tool_name, None, _FOLDER_NOT_FOUND_MESSAGE)
        try:
            folder = await client.get_metadata(parent_folder_id)
        except aiohttp.ClientResponseError as e:
            if e.status == 404:
                return tool_error(tool_name, None, _FOLDER_NOT_FOUND_MESSAGE)
            raise
        if folder.get("trashed") or folder.get("mimeType") != FOLDER_MIME_TYPE:
            return tool_error(tool_name, None, _FOLDER_NOT_FOUND_MESSAGE)
        access = await resolver.resolve(parent_folder_id, folder.get("parents"))
        if access is Access.NONE:
            return tool_error(tool_name, None, _FOLDER_NOT_FOUND_MESSAGE)
        if access is not Access.WRITE:
            return tool_error(tool_name, None, _READ_ONLY_FOLDER_MESSAGE)

        file_name = name.strip()
        try:
            created = await client.create_text_file(
                file_name, parent_folder_id, content, guess_text_mime(file_name)
            )
        except aiohttp.ClientResponseError as e:
            # drive.file では、この拡張が作っていない親フォルダが見えず拒否されることがある。
            if config.write_scope == "file" and e.status in (403, 404):
                return tool_error(tool_name, None, _FILE_SCOPE_CREATE_MESSAGE)
            raise
        data = summarize_file(created, writable=True)
        data["created"] = True
        memory_entry = (
            f"Google Drive にテキストファイル「{file_name}」を作成しました（{len(content)}文字）。"
        )
        return tool_success(tool_name, memory_entry, data)

    return await run_drive_operation(tool_name, overwrite if file_id is not None else create)
