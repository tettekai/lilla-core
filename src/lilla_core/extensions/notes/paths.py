"""ノートの論理パスの規則。

パスは `/` 区切りの相対パス（例: `diary/2026-11-08.md`）。保存前に Unicode を NFC へ
正規化する（macOS 由来の NFD で同じ名前が 2 つできるのを防ぐ）。日本語の名前は許す。

拒否するもの: 文字列以外・空（ファイルを指す場合）・先頭や末尾の `/`・空の要素
（`a//b`）・`.` / `..`・`\\`・NUL などの制御文字・長すぎるパスや要素。前後の空白は
取り除かず、空白だけの要素や前後に空白のある要素も拒否する（見分けにくい同名を防ぐ）。
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

from .errors import NotesPathError

#: パス全体の最大の文字数。
MAX_PATH_CHARS = 512
#: 要素 1 つの最大の文字数。
MAX_SEGMENT_CHARS = 255

#: パスの形式が正しくないときの文言（LLM・画面へそのまま返す）。
INVALID_PATH_MESSAGE = (
    "path は `/` 区切りの相対パスで指定してください（例: diary/2026-11-08.md）。"
    "先頭・末尾の /、空の要素、. や ..、\\、制御文字は使えません。"
)

# C0 / C1 制御文字と DEL。
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def normalize_path(path: Any, *, allow_empty: bool = False) -> str:
    """論理パスを検証し、NFC へ正規化して返す。

    Args:
        path: 検証するパス。
        allow_empty: 空文字（最上位）を許すか（一覧・検索の起点に使う）。`None` も空として扱う。

    Returns:
        正規化したパス（空を許した場合は空文字もありうる）。

    Raises:
        NotesPathError: 規則に合わない場合。
    """
    if path is None and allow_empty:
        return ""
    if not isinstance(path, str):
        raise NotesPathError(INVALID_PATH_MESSAGE)
    normalized = unicodedata.normalize("NFC", path)
    if normalized == "":
        if allow_empty:
            return ""
        raise NotesPathError(INVALID_PATH_MESSAGE)
    if (
        len(normalized) > MAX_PATH_CHARS
        or "\\" in normalized
        or _CONTROL_RE.search(normalized)
        or normalized.startswith("/")
        or normalized.endswith("/")
    ):
        raise NotesPathError(INVALID_PATH_MESSAGE)
    for segment in normalized.split("/"):
        if (
            segment in ("", ".", "..")
            or len(segment) > MAX_SEGMENT_CHARS
            or segment != segment.strip()
        ):
            raise NotesPathError(INVALID_PATH_MESSAGE)
    return normalized


def child_path(parent: str, name: str) -> str:
    """親のパスの下に名前を足したパスを返す（親が空なら名前だけ）。"""
    return f"{parent}/{name}" if parent else name
