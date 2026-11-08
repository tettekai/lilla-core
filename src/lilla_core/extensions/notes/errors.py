"""ノートの操作の例外。

どれもコアの `FileStorage` の例外を継承し、`FileStorage` の利用側から種類が分かるようにする。
メッセージは LLM・画面へそのまま返してよい文言にする。
"""
from __future__ import annotations

from lilla_core.core.file_storage import (
    FileStorageError,
    FileStorageExistsError,
    FileStorageNotFoundError,
    FileStoragePathError,
)


class NotesError(FileStorageError):
    """ノートの操作の失敗。"""


class NotesPathError(NotesError, FileStoragePathError):
    """パスの形式が正しくない、またはファイルとディレクトリが食い違う。"""


class NotesNotFoundError(NotesError, FileStorageNotFoundError):
    """パスが存在しない。"""


class NotesExistsError(NotesError, FileStorageExistsError):
    """`mode="create"` で、ファイルが既にある。"""


class NotesTooLargeError(NotesError):
    """書いた後の本文が上限を超える。"""
