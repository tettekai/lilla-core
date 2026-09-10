"""src/utils/path_utils.py のテスト。"""
from __future__ import annotations

from pathlib import Path

import pytest

from lilla_core.utils.path_utils import resolve_within_root, validate_relative_path


class TestValidateRelativePath:
    """validate_relative_path のテスト。"""

    @pytest.mark.parametrize(
        "path",
        [
            "note.md",
            "lilla/diary/2026-04-09.md",
            "フォルダ/ノート.md",
            "a/./b.md",
            "..hidden/note.md",
            "note..md",
        ],
    )
    def test_valid_relative_paths_are_accepted(self, path: str) -> None:
        """Vault 内を指す通常の相対パスは通ること。"""
        validate_relative_path(path)

    def test_empty_path_is_rejected(self) -> None:
        """空文字は拒否されること。"""
        with pytest.raises(ValueError, match="empty"):
            validate_relative_path("")

    @pytest.mark.parametrize("path", ["/etc/passwd", "/note.md"])
    def test_absolute_path_is_rejected(self, path: str) -> None:
        """絶対パスは拒否されること。"""
        with pytest.raises(ValueError, match="Absolute paths"):
            validate_relative_path(path)

    @pytest.mark.parametrize(
        "path",
        ["../secret.md", "a/../../b.md", "..", "a/..", "..\\secret.md"],
    )
    def test_parent_directory_segment_is_rejected(self, path: str) -> None:
        """.. セグメントを含むパスは拒否されること。"""
        with pytest.raises(ValueError, match="parent directory"):
            validate_relative_path(path)

    def test_backslash_absolute_path_is_rejected(self) -> None:
        """区切りがバックスラッシュの絶対パスも拒否されること。"""
        with pytest.raises(ValueError, match="Absolute paths"):
            validate_relative_path("\\etc\\passwd")


class TestResolveWithinRoot:
    """resolve_within_root のテスト。"""

    def test_returns_resolved_path_under_root(self, tmp_path: Path) -> None:
        """root 配下のパスは解決済み絶対パスで返ること。"""
        (tmp_path / "sub").mkdir()
        target = tmp_path / "sub" / "note.md"
        target.write_text("内容", encoding="utf-8")

        assert resolve_within_root(tmp_path, "sub/note.md") == target.resolve()

    def test_nonexistent_path_under_root_is_allowed(self, tmp_path: Path) -> None:
        """存在しないファイルでも root 配下なら解決できること（存在判定は呼び出し側の責務）。"""
        resolved = resolve_within_root(tmp_path, "まだ無い.md")

        assert resolved == (tmp_path / "まだ無い.md").resolve()

    def test_parent_directory_segment_is_rejected(self, tmp_path: Path) -> None:
        """.. を含むパスは拒否されること。"""
        with pytest.raises(ValueError, match="parent directory"):
            resolve_within_root(tmp_path, "../outside.md")

    def test_absolute_path_is_rejected(self, tmp_path: Path) -> None:
        """絶対パスは拒否されること。"""
        with pytest.raises(ValueError, match="Absolute paths"):
            resolve_within_root(tmp_path, "/etc/passwd")

    def test_symlink_escaping_root_is_rejected(self, tmp_path: Path) -> None:
        """root 外へ向かうシンボリックリンク経由のパスは拒否されること。"""
        root = tmp_path / "vault"
        root.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.md").write_text("秘密", encoding="utf-8")
        (root / "link").symlink_to(outside)

        with pytest.raises(ValueError, match="outside the root directory"):
            resolve_within_root(root, "link/secret.md")
