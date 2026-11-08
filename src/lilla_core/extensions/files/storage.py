"""`files` 拡張の読み書きの本体と、コアの `FileStorage` の実装。

LLM ツール（`tools/`）と `FilesStorage` が同じ関数を通るようにし、パスの検証・
ホワイトリストの判定・テキストの判定を二重に持たない。ここの関数は同期で、
呼び出し側がスレッドで実行する（ツールは `tool_support.run_file_operation`、
`FilesStorage` は `asyncio.to_thread`）。
"""
from __future__ import annotations

import asyncio
import fnmatch
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from lilla_core.core.file_storage import (
    FileStorage,
    FileStorageEntry,
    FileStorageNotFoundError,
    FileStoragePermissionError,
    FileWriteResult,
    check_write_mode,
)

from .access import (
    Access,
    FileAccessError,
    FileAlreadyExistsError,
    FileNotAllowedError,
    FileNotTextAccessError,
    FilePathError,
    FileWriteDeniedError,
    Root,
    Target,
    access_for,
    child_logical,
    is_within,
    resolve_target,
    split_logical_path,
)
from .tool_support import (
    FILE_NOT_FOUND_MESSAGE,
    MAX_READ_FILE_BYTES,
    NOT_TEXT_MESSAGE,
    PERMISSION_DENIED_MESSAGE,
    FileTooLargeError,
    NotTextError,
    ensure_text_head,
    load_roots,
    read_text_file,
)

#: `mode` ごとに `open()` に渡すモード。
OPEN_MODES = {"create": "x", "overwrite": "w", "append": "a"}

#: ディレクトリへ書こうとしたときの文言。
WRITE_TO_DIR_MESSAGE = "ディレクトリには書き込めません。ファイルのパスを指定してください。"
#: 書き込みが許可されていない場所への文言。
WRITE_DENIED_MESSAGE = "このディレクトリには書き込みが許可されていません。"
#: `create` で既にあるときの文言。
ALREADY_EXISTS_MESSAGE = "ファイルが既にあります（上書きするなら mode に overwrite を指定してください）。"
#: 親のパスがファイルのときの文言。
PARENT_NOT_DIR_MESSAGE = "親のパスがディレクトリではありません。"
#: ディレクトリを読もうとしたときの文言。
READ_DIR_MESSAGE = "ディレクトリは読めません（file_list を使ってください）。"
#: ディレクトリでないものを一覧しようとしたときの文言。
NOT_A_DIR_MESSAGE = "ディレクトリではありません。"
#: ホワイトリストが空のときの文言。
NO_ROOTS_MESSAGE = "アクセスが許可されたディレクトリがありません。"


@dataclass(frozen=True)
class DirEntry:
    """ディレクトリの直下のエントリ 1 つ（ツールと `FilesStorage` が共有する形）。"""

    path: str
    name: str
    is_dir: bool
    size: int | None
    mtime: float
    writable: bool


def write_text(roots: list[Root], path: Any, content: str, mode: str) -> tuple[Target, bool]:
    """テキストファイルを UTF-8（BOM 無し・改行変換なし）で書く。

    Args:
        roots: ホワイトリストのディレクトリ。
        path: 論理パス（「ディレクトリの名前/相対パス」）。
        content: 書く内容。
        mode: `create` / `overwrite` / `append`。

    Returns:
        解決したパスと、書く前からファイルがあったか。

    Raises:
        ValueError: `mode` が正しくない場合。
        FilePathError: パスの形式が正しくない・ディレクトリを指す・親がファイルの場合。
        FileNotAllowedError: ホワイトリストの外を指す場合。
        FileWriteDeniedError: 書き込みが許可されていない場合。
        FileAlreadyExistsError: `create` で既にある場合。
        FileNotTextAccessError: `append` で既存ファイルが UTF-8 のテキストでない場合。
    """
    check_write_mode(mode)
    segments = split_logical_path(path)
    target = resolve_target(roots, segments)
    if len(segments) < 2 or os.path.isdir(target.real):
        raise FilePathError(WRITE_TO_DIR_MESSAGE)
    if target.access is not Access.WRITE:
        raise FileWriteDeniedError(WRITE_DENIED_MESSAGE)

    existed = os.path.exists(target.real)
    if mode == "create" and existed:
        raise FileAlreadyExistsError(ALREADY_EXISTS_MESSAGE)
    if mode == "append" and existed:
        # Shift_JIS などへ UTF-8 で追記すると文字コードが混ざって読めなくなるため断る。
        try:
            ensure_text_head(target.real)
        except NotTextError:
            raise FileNotTextAccessError(NOT_TEXT_MESSAGE)
    parent = os.path.dirname(target.real)
    if os.path.exists(parent) and not os.path.isdir(parent):
        raise FilePathError(PARENT_NOT_DIR_MESSAGE)
    os.makedirs(parent, exist_ok=True)
    try:
        with open(target.real, OPEN_MODES[mode], encoding="utf-8", newline="") as f:
            f.write(content)
    except FileExistsError:
        raise FileAlreadyExistsError(ALREADY_EXISTS_MESSAGE)
    return target, existed


def read_text(roots: list[Root], path: Any) -> tuple[Target, str]:
    """テキストファイルを UTF-8 で読む（先頭の BOM は取り除く）。

    Raises:
        FilePathError: パスの形式が正しくない・ディレクトリを指す場合。
        FileNotAllowedError: ホワイトリストの外を指す場合。
        FileNotFoundError: ファイルが無い場合。
        NotTextError: テキストとして読めない場合。
        FileTooLargeError: `MAX_READ_FILE_BYTES` を超える場合。
    """
    target = resolve_target(roots, split_logical_path(path))
    if os.path.isdir(target.real):
        raise FilePathError(READ_DIR_MESSAGE)
    return target, read_text_file(target.real, MAX_READ_FILE_BYTES)


def list_dir(
    roots: list[Root],
    target: Target,
    pattern: str | None = None,
    limit: int | None = None,
) -> tuple[list[DirEntry], bool]:
    """ディレクトリの直下のエントリを名前順に返す（再帰しない）。

    ホワイトリストのディレクトリの外を指すエントリ（リンク）と、壊れたリンクは出さない。

    Args:
        roots: ホワイトリストのディレクトリ。
        target: 一覧するディレクトリ。
        pattern: 名前で絞り込むグロブ（省略時は絞り込まない）。
        limit: 返す件数の上限（省略時は上限なし）。

    Returns:
        エントリのリストと、上限で切り詰めたか。

    Raises:
        FilePathError: ディレクトリでない場合。
        FileNotFoundError: 存在しない場合。
    """
    if not os.path.isdir(target.real):
        if os.path.exists(target.real):
            raise FilePathError(NOT_A_DIR_MESSAGE)
        raise FileNotFoundError(target.real)

    root_real = target.root.real_path()
    entries: list[DirEntry] = []
    with os.scandir(target.real) as it:
        items = sorted(it, key=lambda e: e.name)
    for item in items:
        if pattern is not None and not fnmatch.fnmatch(item.name, pattern):
            continue
        real = os.path.realpath(item.path)
        if not is_within(root_real, real):
            continue
        if limit is not None and len(entries) >= limit:
            return entries, True
        try:
            is_dir = item.is_dir()
            stat = item.stat()
        except OSError:
            # 壊れたリンクなど。一覧から外す。
            continue
        entries.append(
            DirEntry(
                path=child_logical(target.logical, item.name),
                name=item.name,
                is_dir=is_dir,
                size=None if is_dir else stat.st_size,
                mtime=stat.st_mtime,
                writable=access_for(roots, real) is Access.WRITE,
            )
        )
    return entries, False


def _to_storage_entry(entry: DirEntry) -> FileStorageEntry:
    """`DirEntry` をコアの `FileStorageEntry` にする。"""
    return FileStorageEntry(
        path=entry.path,
        name=entry.name,
        type="dir" if entry.is_dir else "file",
        size=entry.size,
        modified_time=datetime.fromtimestamp(entry.mtime, tz=timezone.utc),
    )


async def _run(func, *args):
    """同期の操作をスレッドで実行し、OS の例外を `FileStorage` の例外へ変換する。"""
    try:
        return await asyncio.to_thread(func, *args)
    except FileAccessError:
        raise
    except FileNotFoundError as e:
        raise FileStorageNotFoundError(FILE_NOT_FOUND_MESSAGE) from e
    except PermissionError as e:
        raise FileStoragePermissionError(PERMISSION_DENIED_MESSAGE) from e


class FilesStorage(FileStorage):
    """`extensions.files` のホワイトリストのディレクトリを保存先として扱う `FileStorage`。

    論理パスは LLM ツールと同じ「ディレクトリの名前/相対パス」。設定は呼び出しのたびに
    読む（コンストラクタでは何も読まない）。
    """

    @property
    def read_only(self) -> bool:
        """書き込み可能なディレクトリが 1 つも無ければ True。"""
        try:
            _, roots = load_roots()
        except Exception:
            return True
        return not any(r.writable for r in roots)

    async def read(self, path: str) -> str:
        """テキストファイルの内容を返す。"""

        def op() -> str:
            _, roots = load_roots()
            try:
                return read_text(roots, path)[1]
            except (NotTextError, FileTooLargeError):
                raise FileNotTextAccessError(NOT_TEXT_MESSAGE)

        return await _run(op)

    async def write(self, path: str, content: str, mode: str = "overwrite") -> FileWriteResult:
        """テキストファイルを書く。"""
        check_write_mode(mode)

        def op() -> FileWriteResult:
            _, roots = load_roots()
            target, existed = write_text(roots, path, content, mode)
            return FileWriteResult(path=target.logical, created=not existed)

        return await _run(op)

    async def list(self, path: str = "") -> list[FileStorageEntry]:
        """ディレクトリの直下のエントリを名前順に返す（空文字はホワイトリストの一覧）。"""

        def op() -> list[FileStorageEntry]:
            _, roots = load_roots()
            segments = split_logical_path(path)
            if not segments:
                return [
                    FileStorageEntry(path=r.label, name=r.label, type="dir")
                    for r in sorted(roots, key=lambda r: r.label)
                ]
            entries, _ = list_dir(roots, resolve_target(roots, segments))
            return [_to_storage_entry(e) for e in entries]

        return await _run(op)

    async def exists(self, path: str) -> bool:
        """ファイルかディレクトリがあれば True（ホワイトリストの外は False）。"""

        def op() -> bool:
            _, roots = load_roots()
            segments = split_logical_path(path)
            if not segments:
                return True
            try:
                target = resolve_target(roots, segments)
            except FileNotAllowedError:
                return False
            return os.path.exists(target.real)

        return await _run(op)
