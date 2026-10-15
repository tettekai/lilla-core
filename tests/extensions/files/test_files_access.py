"""`lilla_core.extensions.files.access`（パスの解決とアクセス判定）のテスト。"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from lilla_core.extensions.files.access import (
    INVALID_PATH_MESSAGE,
    NOT_FOUND_MESSAGE,
    OUTSIDE_MESSAGE,
    Access,
    FileAccessError,
    Root,
    access_for,
    child_logical,
    is_within,
    resolve_target,
    split_logical_path,
)


@pytest.fixture
def roots(tmp_path: Path) -> list[Root]:
    """`ro`（読み取り専用）・`rw`（書き込み可能）・`rw/locked`（入れ子の読み取り専用）。"""
    (tmp_path / "ro").mkdir()
    (tmp_path / "rw" / "locked").mkdir(parents=True)
    (tmp_path / "outside").mkdir()
    return [
        Root("ro", str(tmp_path / "ro"), False),
        Root("locked", str(tmp_path / "rw" / "locked"), False),
        Root("rw", str(tmp_path / "rw"), True),
    ]


class TestSplitLogicalPath:
    """文字列の段階での検査。"""

    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            (None, []),
            ("", []),
            (".", []),
            ("ro", ["ro"]),
            ("ro/a/b.txt", ["ro", "a", "b.txt"]),
            ("ro\\a\\b.txt", ["ro", "a", "b.txt"]),
            ("ro//./a/", ["ro", "a"]),
        ],
    )
    def test_splits(self, path, expected) -> None:
        """区切りは `/` と `\\` で、空の要素と `.` は読み飛ばす。"""
        assert split_logical_path(path) == expected

    @pytest.mark.parametrize(
        "path", ["/etc/passwd", "\\\\server\\share", "C:\\Windows", "c:/x", "ro/../x", "..", "a\0b", 1]
    )
    def test_rejects(self, path) -> None:
        """絶対パス・UNC・ドライブ・`..`・NUL・文字列以外は拒否する。"""
        with pytest.raises(FileAccessError, match=INVALID_PATH_MESSAGE):
            split_logical_path(path)


class TestResolveTarget:
    """実体パスでの判定。"""

    def test_unknown_label(self, roots) -> None:
        """名前の分からないディレクトリは見つからない扱い。"""
        with pytest.raises(FileAccessError, match=NOT_FOUND_MESSAGE):
            resolve_target(roots, ["outside", "x"])

    def test_access_by_deepest_root(self, roots, tmp_path) -> None:
        """許す操作は、実体パスを含む最も深いディレクトリで決まる。"""
        assert resolve_target(roots, ["rw", "a.txt"]).access is Access.WRITE
        assert resolve_target(roots, ["rw", "locked", "a.txt"]).access is Access.READ
        assert resolve_target(roots, ["locked", "a.txt"]).access is Access.READ
        assert resolve_target(roots, ["ro", "a.txt"]).access is Access.READ

    def test_logical_path_is_normalized(self, roots) -> None:
        """返す論理パスは `/` 区切り。"""
        target = resolve_target(roots, split_logical_path("rw\\a\\b.txt"))

        assert target.logical == "rw/a/b.txt"
        assert target.root.label == "rw"

    def test_symlink_to_outside(self, roots, tmp_path) -> None:
        """外を指すシンボリックリンクは拒否する。"""
        os.symlink(tmp_path / "outside", tmp_path / "ro" / "link")

        with pytest.raises(FileAccessError, match=OUTSIDE_MESSAGE):
            resolve_target(roots, ["ro", "link", "x.txt"])

    def test_symlink_into_other_root(self, roots, tmp_path) -> None:
        """リンクで別のホワイトリストのディレクトリへ出るのも拒否する（先頭の名前の配下に限る）。"""
        os.symlink(tmp_path / "rw", tmp_path / "ro" / "to-rw")

        with pytest.raises(FileAccessError, match=OUTSIDE_MESSAGE):
            resolve_target(roots, ["ro", "to-rw", "x.txt"])

    def test_symlinked_root(self, tmp_path) -> None:
        """ホワイトリストのディレクトリ自体がリンクでも、その実体の配下は使える。"""
        (tmp_path / "real").mkdir()
        os.symlink(tmp_path / "real", tmp_path / "alias")
        roots = [Root("alias", str(tmp_path / "alias"), True)]

        target = resolve_target(roots, ["alias", "x.txt"])

        assert target.real == os.path.realpath(tmp_path / "real" / "x.txt")
        assert target.access is Access.WRITE

    def test_empty_segments(self, roots) -> None:
        """要素が空なら見つからない扱い（一覧は呼び出し側が扱う）。"""
        with pytest.raises(FileAccessError):
            resolve_target(roots, [])


class TestHelpers:
    """小さな関数。"""

    def test_is_within(self, tmp_path) -> None:
        """自身と配下は True、兄弟や接頭辞だけ同じパスは False。"""
        base = str(tmp_path / "a")
        assert is_within(base, base)
        assert is_within(base, str(tmp_path / "a" / "b"))
        assert not is_within(base, str(tmp_path / "ab"))
        assert not is_within(base, str(tmp_path))

    def test_access_for_same_path_prefers_read(self, tmp_path) -> None:
        """同じ実体パスが両方にあれば読み取り専用に倒す。"""
        roots = [Root("a", str(tmp_path), True), Root("b", str(tmp_path), False)]

        assert access_for(roots, str(tmp_path / "x")) is Access.READ

    def test_access_for_outside(self, roots, tmp_path) -> None:
        """どこにも含まれなければ NONE。"""
        assert access_for(roots, str(tmp_path / "outside")) is Access.NONE

    def test_child_logical(self) -> None:
        """親が空なら子だけ。"""
        assert child_logical("", "a") == "a"
        assert child_logical("a", "b", "c") == "a/b/c"
