"""ホワイトリストのディレクトリへのパスの解決とアクセス判定。

LLM とやりとりするパスは「ディレクトリの名前/その中の相対パス」の形（例:
`notes/2026/todo.md`）で、先頭の要素で `extensions.files` のどのディレクトリかを決める。
空文字（または `.`）はホワイトリストのディレクトリの一覧そのものを指す。

判定は実体パス（シンボリックリンクを辿った後の `os.path.realpath`）で行う。

- 文字列の段階で絶対パス・`..`・NUL を拒否する
- 実体パスが、先頭の要素で選んだディレクトリの実体パスの配下に無ければ拒否する
  （配下のシンボリックリンクで外へ出る経路を塞ぐ）
- 許す操作は、実体パスを含むホワイトリストのディレクトリのうち最も深いもので決める
  （書き込み可能ディレクトリの中に読み取り専用ディレクトリを置けば、その配下は
  読み取り専用になる。google-drive の「最も近いフォルダで判定」と同じ考え方）

ファイルシステムを確かめてから操作するまでの間に差し替えられる競合（TOCTOU）は
防がない。ホワイトリストのディレクトリへ書き込める者は LLM の外にいる前提とする。
"""
from __future__ import annotations

import ntpath
import os
from dataclasses import dataclass
from enum import IntEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lilla_core.extensions.files import FilesConfig

#: ホワイトリスト外・名前の分からないディレクトリに返す文言。
NOT_FOUND_MESSAGE = "そのパスは見つからないか、アクセスが許可されていません。"
#: `..`・絶対パス・リンクなどでホワイトリストの外を指したときの文言。
OUTSIDE_MESSAGE = "ホワイトリストのディレクトリの外へはアクセスできません。"
#: パスの形式が正しくないときの文言。
INVALID_PATH_MESSAGE = (
    "path は「ディレクトリの名前/相対パス」の形の文字列で指定してください"
    "（絶対パスや .. は使えません）。"
)


class FileAccessError(Exception):
    """ツールの入力やアクセス判定の失敗（LLM へそのまま返してよい文言を持つ）。"""


class Access(IntEnum):
    """パスに許す操作（大きいほど強い）。"""

    NONE = 0
    READ = 1
    WRITE = 2


@dataclass(frozen=True)
class Root:
    """ホワイトリストのディレクトリ 1 つ。"""

    #: LLM から指すときの名前（パスの先頭の要素）。
    label: str
    #: 設定に書かれたパス。
    path: str
    #: 書き込みを許すか。
    writable: bool

    def real_path(self) -> str:
        """実体パスを返す（マウントやリンクの張り替えに追従するよう、毎回解決する）。"""
        return os.path.realpath(self.path)


@dataclass(frozen=True)
class Target:
    """解決済みのパス。"""

    #: 先頭の要素で選んだディレクトリ。
    root: Root
    #: LLM へ返す形に正規化したパス（`label/相対パス`）。
    logical: str
    #: 実体パス。
    real: str
    #: 許す操作（`READ` か `WRITE`）。
    access: Access


def build_roots(config: FilesConfig) -> list[Root]:
    """設定からホワイトリストのディレクトリの一覧を作る（読み取り専用 → 書き込み可能の順）。"""
    roots = [Root(d.label, d.path, False) for d in config.read_only_dirs]
    roots += [Root(d.label, d.path, True) for d in config.writable_dirs]
    return roots


def split_logical_path(path: Any) -> list[str]:
    """LLM から受け取ったパスを要素のリストに分ける（空なら一覧そのものを指す）。

    区切りは `/` と `\\` のどちらでもよい。空の要素と `.` は読み飛ばす。

    Raises:
        FileAccessError: 文字列でない・NUL を含む・絶対パス・`..` を含む場合。
    """
    if path is None:
        return []
    if not isinstance(path, str) or "\0" in path:
        raise FileAccessError(INVALID_PATH_MESSAGE)
    normalized = path.strip().replace("\\", "/")
    if normalized.startswith("/") or ntpath.splitdrive(normalized)[0]:
        raise FileAccessError(INVALID_PATH_MESSAGE)
    segments = [s for s in normalized.split("/") if s not in ("", ".")]
    if any(s == ".." for s in segments):
        raise FileAccessError(INVALID_PATH_MESSAGE)
    return segments


def is_within(base_real: str, real: str) -> bool:
    """実体パス `real` が実体パス `base_real` 自身かその配下なら True を返す。"""
    base = os.path.normcase(base_real)
    target = os.path.normcase(real)
    try:
        return os.path.commonpath([base, target]) == base
    except ValueError:
        # Windows で別ドライブ同士を比べた場合など。
        return False


def access_for(roots: list[Root], real: str) -> Access:
    """実体パスに許す操作を、それを含む最も深いホワイトリストのディレクトリで決める。

    同じ深さ（同じ実体パス）のディレクトリが読み取り専用と書き込み可能の両方にあれば
    安全側（読み取り専用）に倒す。
    """
    best_depth = -1
    best = Access.NONE
    for root in roots:
        root_real = root.real_path()
        if not is_within(root_real, real):
            continue
        depth = len(os.path.normcase(root_real))
        access = Access.WRITE if root.writable else Access.READ
        if depth > best_depth:
            best_depth, best = depth, access
        elif depth == best_depth:
            best = min(best, access)
    return best


def resolve_target(roots: list[Root], segments: list[str]) -> Target:
    """要素のリスト（先頭がディレクトリの名前）を実体パスへ解決し、操作の可否を決める。

    Raises:
        FileAccessError: 名前の分からないディレクトリ、またはホワイトリストの外を指す場合。
    """
    if not segments:
        raise FileAccessError(NOT_FOUND_MESSAGE)
    root = next((r for r in roots if r.label == segments[0]), None)
    if root is None:
        raise FileAccessError(NOT_FOUND_MESSAGE)
    root_real = root.real_path()
    real = os.path.realpath(os.path.join(root_real, *segments[1:]))
    if not is_within(root_real, real):
        raise FileAccessError(OUTSIDE_MESSAGE)
    access = access_for(roots, real)
    if access is Access.NONE:
        # 選んだディレクトリの配下にあれば必ずどれかに当たる。念のため拒否側に倒す。
        raise FileAccessError(OUTSIDE_MESSAGE)
    return Target(root, "/".join(segments), real, access)


def child_logical(parent: str, *names: str) -> str:
    """親の論理パスの下に要素を足した論理パスを返す。"""
    return "/".join([parent, *names]) if parent else "/".join(names)
