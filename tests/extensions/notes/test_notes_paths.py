"""`lilla_core.extensions.notes.paths` のテスト。"""
from __future__ import annotations

import unicodedata

import pytest

from lilla_core.core.file_storage import FileStoragePathError
from lilla_core.extensions.notes import paths
from lilla_core.extensions.notes.errors import NotesPathError


class TestNormalizePath:
    """`normalize_path()` の規則。"""

    @pytest.mark.parametrize(
        "path",
        ["diary/2026-11-08.md", "a", "日記/十一月.md", "a b/c d.txt", "x.y/z..md"],
    )
    def test_accepts_valid_paths(self, path: str) -> None:
        """`/` 区切りの相対パスは通る（日本語・空白を含む名前も可）。"""
        assert paths.normalize_path(path) == path

    @pytest.mark.parametrize(
        "path",
        [
            "", "/a", "a/", "a//b", "./a", "a/./b", "../a", "a/../b", "a\\b",
            "a\0b", "a\nb", "a\x7fb", " a", "a/ b", "a /b", "a" * 513, "b/" + "a" * 256,
            None, 1, ["a"],
        ],
    )
    def test_rejects_invalid_paths(self, path) -> None:
        """規則に合わないパスは `NotesPathError`（`FileStoragePathError` の派生）。"""
        with pytest.raises(NotesPathError) as exc:
            paths.normalize_path(path)
        assert isinstance(exc.value, FileStoragePathError)

    def test_normalizes_to_nfc(self) -> None:
        """NFD で来た名前は NFC にそろえる。"""
        nfd = unicodedata.normalize("NFD", "がぎぐ.md")
        assert nfd != "がぎぐ.md"
        assert paths.normalize_path(nfd) == "がぎぐ.md"

    def test_allow_empty(self) -> None:
        """`allow_empty` なら空文字・None は最上位（空文字）。"""
        assert paths.normalize_path("", allow_empty=True) == ""
        assert paths.normalize_path(None, allow_empty=True) == ""


class TestHelpers:
    """補助関数。"""

    def test_child_path(self) -> None:
        """親が空なら名前だけ。"""
        assert paths.child_path("", "a") == "a"
        assert paths.child_path("a", "b") == "a/b"

    def test_prefix_regex_escapes(self) -> None:
        """正規表現の記号はエスケープする。"""
        import re

        pattern = re.compile(paths.prefix_regex("a.b(c)"))
        assert pattern.match("a.b(c)/x")
        assert not pattern.match("aXb(c)/x")
        assert not pattern.match("a.b(c)")
        assert paths.prefix_regex("") == "^"
