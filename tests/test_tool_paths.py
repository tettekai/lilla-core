"""`lilla_core.loaders.tool_paths` のテスト（ツールルート解決とファイル検索）。"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from lilla_core.loaders import tool_paths


@pytest.fixture
def mock_cfg(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """`paths.tool_root` だけを持つ設定モックに差し替える。"""
    cfg = MagicMock()
    monkeypatch.setattr(tool_paths, "get_config", lambda: cfg)
    return cfg


class TestResolveToolRoots:
    def test_core_tool_root_comes_first(
        self, mock_cfg: MagicMock, make_extension, use_extensions
    ) -> None:
        """`paths.tool_root` が先頭で、拡張の追加ルートがロード順で続く。"""
        mock_cfg.paths.tool_root = Path("/core/tools")
        use_extensions(
            make_extension("a", tool_roots=[Path("/pack_a/tools")]),
            make_extension("b", tool_roots=[Path("/pack_b/tools")]),
        )

        assert tool_paths.resolve_tool_roots() == [
            Path("/core/tools"),
            Path("/pack_a/tools"),
            Path("/pack_b/tools"),
        ]

    def test_returns_only_core_root_without_extensions(
        self, mock_cfg: MagicMock, use_extensions
    ) -> None:
        """拡張が 0 個なら `paths.tool_root` だけを返す。"""
        mock_cfg.paths.tool_root = Path("/core/tools")
        use_extensions()

        assert tool_paths.resolve_tool_roots() == [Path("/core/tools")]

    def test_duplicate_roots_are_removed(
        self, mock_cfg: MagicMock, make_extension, use_extensions
    ) -> None:
        """同じディレクトリを指すルートは 1 つに畳む（自分自身との衝突を避ける）。"""
        mock_cfg.paths.tool_root = Path("/core/tools")
        use_extensions(make_extension("a", tool_roots=[Path("/core/tools/../tools")]))

        assert tool_paths.resolve_tool_roots() == [Path("/core/tools")]


class TestFindToolFile:
    def test_finds_file_in_a_single_root(self, tmp_path: Path) -> None:
        """ルート配下を再帰的に探してファイルを返す。"""
        target = tmp_path / "schedule" / "task_foo.py"
        target.parent.mkdir(parents=True)
        target.touch()

        assert tool_paths.find_tool_file("task_foo", [tmp_path]) == target

    def test_finds_file_in_an_extension_root(self, tmp_path: Path) -> None:
        """コアのルートに無くても、拡張の追加ルートから見つかる。"""
        core_root = tmp_path / "core"
        core_root.mkdir()
        pack_root = tmp_path / "pack"
        pack_root.mkdir()
        target = pack_root / "llm_extra.py"
        target.touch()

        assert tool_paths.find_tool_file("llm_extra", [core_root, pack_root]) == target

    def test_returns_none_when_not_found(self, tmp_path: Path) -> None:
        """どのルートにも無ければ None。"""
        assert tool_paths.find_tool_file("llm_missing", [tmp_path]) is None

    def test_missing_root_is_skipped(self, tmp_path: Path) -> None:
        """存在しないルートが混ざっていても例外にしない。"""
        target = tmp_path / "llm_one.py"
        target.touch()

        found = tool_paths.find_tool_file("llm_one", [tmp_path / "nope", tmp_path])

        assert found == target

    def test_same_name_in_multiple_roots_raises(self, tmp_path: Path) -> None:
        """複数のルートに同名ファイルがあると fail-fast（先頭マッチで採らない）。"""
        first = tmp_path / "a"
        second = tmp_path / "b"
        for root in (first, second):
            root.mkdir()
            (root / "llm_dup.py").touch()

        with pytest.raises(ValueError, match="multiple tool roots"):
            tool_paths.find_tool_file("llm_dup", [first, second])

    def test_same_name_inside_one_root_uses_first_match(self, tmp_path: Path) -> None:
        """1 つのルート内の重複は従来どおり先頭マッチを使う。"""
        for sub in ("one", "two"):
            (tmp_path / sub).mkdir()
            (tmp_path / sub / "llm_dup.py").touch()

        found = tool_paths.find_tool_file("llm_dup", [tmp_path])

        assert found is not None
        assert found.name == "llm_dup.py"


class TestResolveToolDirs:
    def test_appends_config_tools_dir_to_given_roots(self, tmp_path: Path) -> None:
        """渡した探索ルートの後ろに `config_root/tools` を足し、すべて解決済みで返す。"""
        root = tmp_path / "tools"
        root.mkdir()
        config_root = tmp_path / "config"

        assert tool_paths.resolve_tool_dirs([root], config_root) == [
            root.resolve(),
            (config_root / "tools").resolve(),
        ]

    def test_defaults_come_from_config_and_extensions(
        self, mock_cfg: MagicMock, make_extension, use_extensions, tmp_path: Path
    ) -> None:
        """省略時は `resolve_tool_roots()` と設定の `env.config_root` から導く。"""
        mock_cfg.paths.tool_root = tmp_path / "core"
        mock_cfg.env.config_root = tmp_path / "config"
        use_extensions(make_extension("a", tool_roots=[tmp_path / "pack"]))

        assert tool_paths.resolve_tool_dirs() == [
            (tmp_path / "core").resolve(),
            (tmp_path / "pack").resolve(),
            (tmp_path / "config" / "tools").resolve(),
        ]


class TestIsWithinToolDirs:
    def test_file_under_a_dir_is_accepted(self, tmp_path: Path) -> None:
        """いずれかのディレクトリの配下なら True。"""
        target = tmp_path / "tools" / "sub" / "llm_x.py"
        target.parent.mkdir(parents=True)
        target.touch()

        assert tool_paths.is_within_tool_dirs(target, [tmp_path / "tools"])

    def test_file_outside_is_rejected(self, tmp_path: Path) -> None:
        """どのディレクトリの配下でもなければ False。"""
        (tmp_path / "tools").mkdir()
        outside = tmp_path / "outside.py"
        outside.touch()

        assert not tool_paths.is_within_tool_dirs(outside, [tmp_path / "tools"])

    def test_parent_segments_are_resolved_before_checking(self, tmp_path: Path) -> None:
        """`..` を含むパスは解決してから比較する（ルートを抜ける経路を許さない）。"""
        (tmp_path / "tools").mkdir()
        outside = tmp_path / "outside.py"
        outside.touch()
        sneaky = tmp_path / "tools" / ".." / "outside.py"

        assert not tool_paths.is_within_tool_dirs(sneaky, [tmp_path / "tools"])

    def test_symlink_is_resolved_before_checking(self, tmp_path: Path) -> None:
        """ルート内のシンボリックリンクが外を指していれば False。"""
        root = tmp_path / "tools"
        root.mkdir()
        outside = tmp_path / "outside.py"
        outside.touch()
        link = root / "llm_x.py"
        link.symlink_to(outside)

        assert not tool_paths.is_within_tool_dirs(link, [root])

    def test_unresolved_dirs_are_resolved_too(self, tmp_path: Path) -> None:
        """ディレクトリ側に `..` があっても解決してから比較する。"""
        target = tmp_path / "tools" / "llm_x.py"
        target.parent.mkdir()
        target.touch()

        assert tool_paths.is_within_tool_dirs(target, [tmp_path / "other" / ".." / "tools"])
