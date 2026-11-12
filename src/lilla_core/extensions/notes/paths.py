"""ノートの論理パスの規則。

パスは `/` 区切りの相対パス（例: `diary/2026-11-08.md`）。保存前に Unicode を NFC へ
正規化する（macOS 由来の NFD で同じ名前が 2 つできるのを防ぐ）。日本語の名前は許す。

拒否するもの: 文字列以外・空（ファイルを指す場合）・先頭や末尾の `/`・空の要素
（`a//b`）・`.` / `..`・`\\`・NUL などの制御文字・長すぎるパスや要素。前後の空白は
取り除かず、空白だけの要素や前後に空白のある要素も拒否する（見分けにくい同名を防ぐ）。

書き込み（作成・上書き・追記・復元）はさらに `check_writable_path` を通し、ルート直下の
ファイルと、`WRITABLE_TOP_LEVEL_DIRS` に無いトップレベルディレクトリへの書き込みを拒否する
（あとからシステム用のトップレベル名を予約できるようにするため）。読み取り・一覧・検索・
削除にはこの規則をかけない（既存のルート直下のファイルも読めて、ダッシュボードから消せる）。
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

#: 書き込める（ファイル・ディレクトリを作れる）トップレベルディレクトリの名前と、その用途。
#: 許可リストはここ 1 か所だけに持ち、パスの検証（`check_writable_path`）と拡張の
#: デフォルトのプロンプト（`NotesExtension.default_prompt()`）はここを参照する。
#: 予約名を足すときはこの辞書に名前を足す（用途は `ja` / `en` の両方を書く）。
WRITABLE_TOP_LEVEL_DIRS: dict[str, dict[str, str]] = {
    "diary": {
        "ja": "日記用。清書は diary/{日付}.md に置く",
        "en": "for the diary; the clean copy goes to diary/{date}.md",
    },
    "user": {
        "ja": "利用者が自由に置くメモ用。用途もパスの切り方も自由",
        "en": "free-form notes for the user; any purpose and layout",
    },
}


def writable_top_level_message() -> str:
    """書ける場所の外へ書こうとしたときの文言（LLM・画面へそのまま返す）を返す。"""
    names = "、".join(f"{name}/" for name in WRITABLE_TOP_LEVEL_DIRS)
    return (
        "ルート直下や、許可されていないトップレベルディレクトリには書けません。"
        f"書けるのは次のディレクトリの配下だけです: {names}（例: diary/2026-11-08.md）。"
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


def check_writable_path(path: str) -> None:
    """正規化済みのパスへ書き込めるか（許可したトップレベルディレクトリの配下か）を確かめる。

    LLM ツール・ダッシュボード・保存先 `notes` のどこからの書き込みも `service` を通るため、
    この検査は入り口ごとではなくここ 1 か所で行う。

    Args:
        path: `normalize_path` で正規化したファイルのパス。

    Raises:
        NotesPathError: ルート直下のファイル、または許可リストに無いトップレベルの場合。
    """
    top, sep, _ = path.partition("/")
    if not sep or top not in WRITABLE_TOP_LEVEL_DIRS:
        raise NotesPathError(writable_top_level_message())


def child_path(parent: str, name: str) -> str:
    """親のパスの下に名前を足したパスを返す（親が空なら名前だけ）。"""
    return f"{parent}/{name}" if parent else name
