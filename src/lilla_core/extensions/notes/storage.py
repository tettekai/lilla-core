"""ノートをコアの `FileStorage` として扱う実装。

日記の清書など、LLM を通さずにコードから保存先へ書く利用側が使う
（`get_file_storage("notes")`）。中身は `service.py` の関数を呼ぶだけで、
LLM ツール・ダッシュボードと同じ検証を通る。
"""
from __future__ import annotations

from lilla_core.core.file_storage import FileStorage, FileStorageEntry, FileWriteResult

from . import service


class NotesStorage(FileStorage):
    """Git リポジトリのノートを保存先として扱う `FileStorage`（コンストラクタでは接続しない）。

    論理パスは `/` 区切りの相対パス（例: `diary/2026-11-08.md`）。`size` は文字数。
    """

    async def read(self, path: str) -> str:
        """ノートの本文を返す。"""
        return (await service.read_note(path)).content

    async def write(self, path: str, content: str, mode: str = "overwrite") -> FileWriteResult:
        """ノートを書く（1 回の書き込みが 1 コミット。前の内容は Git の履歴に残る）。"""
        result = await service.write_note(path, content, mode)
        return FileWriteResult(path=result.path, created=result.created)

    async def list(self, path: str = "") -> list[FileStorageEntry]:
        """ディレクトリの直下のエントリを名前順に返す。"""
        return [
            FileStorageEntry(
                path=e.path,
                name=e.name,
                type=e.type,
                size=e.size,
                modified_time=e.modified_time,
            )
            for e in await service.list_entries(path)
        ]

    async def exists(self, path: str) -> bool:
        """ファイルかディレクトリがあれば True。"""
        return await service.exists(path)
