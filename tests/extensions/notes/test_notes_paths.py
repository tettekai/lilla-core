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


class TestCheckWritablePath:
    """書き込めるパスの検査。"""

    @pytest.fixture(autouse=True)
    def _no_declarations(self, declare_notes_dirs) -> None:
        """ほかの拡張が何も宣言していない状態から始める。"""
        declare_notes_dirs({})

    @pytest.mark.parametrize("path", ["user/a.md", "user/x/y.md"])
    def test_accepts_user(self, path: str) -> None:
        """notes 自身の `user/` の配下は通す。"""
        paths.check_writable_path(path)

    @pytest.mark.parametrize("path", ["a.md", "diary/a.md", "user", "other/a.md", "USER/a.md"])
    def test_rejects_others(self, path: str) -> None:
        """ルート直下のファイル・宣言の無いトップレベル（`diary/` を含む）は `FileStoragePathError`。"""
        with pytest.raises(FileStoragePathError):
            paths.check_writable_path(path)

    def test_accepts_declared_dirs(self, declare_notes_dirs) -> None:
        """拡張が宣言したトップレベルの配下は通し、ディレクトリそのものは通さない。"""
        declare_notes_dirs({"diary": {"ja": "日記用", "en": "for the diary"}})
        paths.check_writable_path("diary/2026/a.md")
        with pytest.raises(FileStoragePathError):
            paths.check_writable_path("diary")

    def test_message_lists_allowed_dirs(self, declare_notes_dirs) -> None:
        """文言は許可リスト（`user/` → 宣言のロード順）から組み立て、例は `user/` を使う。"""
        declare_notes_dirs({"system": {"ja": "x", "en": "x"}, "diary": {"ja": "y", "en": "y"}})
        with pytest.raises(NotesPathError, match="user/、system/、diary/（例: user/memo.md）"):
            paths.check_writable_path("a.md")

    def test_writable_dirs_without_registration(self, monkeypatch) -> None:
        """notes が登録されていなくても `user/` は書ける。"""
        from lilla_core.core import extension as ext_module

        monkeypatch.setattr(ext_module, "get_notes_directories", lambda: {})
        assert list(paths.writable_top_level_dirs()) == ["user"]


class TestDirectoryNameRule:
    """コアの宣言の検査（`_is_valid_notes_directory_name`）が `normalize_path` の 1 要素の規則と一致する。"""

    @pytest.mark.parametrize(
        "name",
        [
            "diary", "日記", "a b", "x.y", "a..b", "a" * 255, "", ".", "..", "a\\b",
            "a\0b", "a\nb", "a\x7fb", "a\x85b", " a", "a ", "a" * 256,
            unicodedata.normalize("NFD", "が"),
        ],
    )
    def test_matches_normalize_path(self, name: str) -> None:
        """名前 1 つを `normalize_path` に通した結果（変わらずに通るか）と判定が一致する。"""
        from lilla_core.core.extension import _is_valid_notes_directory_name

        try:
            expected = paths.normalize_path(name) == name
        except NotesPathError:
            expected = False
        assert _is_valid_notes_directory_name(name) is expected

    @pytest.mark.parametrize("name", ["a/b", "/a", "a/", None, 1])
    def test_rejects_multi_segment_and_non_str(self, name) -> None:
        """`/` を含む名前（2 要素以上）と文字列以外は通さない。"""
        from lilla_core.core.extension import _is_valid_notes_directory_name

        assert _is_valid_notes_directory_name(name) is False
