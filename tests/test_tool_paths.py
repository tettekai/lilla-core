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
        """省略時は `resolve_tool_roots()` と設定の `env.config_root` から導く。

        拡張が同梱した YAML のディレクトリ（`type: self` の `.py` が置かれうる）も含める。
        """
        mock_cfg.paths.tool_root = tmp_path / "core"
        mock_cfg.env.config_root = tmp_path / "config"
        use_extensions(make_extension(
            "a", tool_roots=[tmp_path / "pack"], tool_config_roots=[tmp_path / "pack_configs"]
        ))

        assert tool_paths.resolve_tool_dirs() == [
            (tmp_path / "core").resolve(),
            (tmp_path / "pack").resolve(),
            (tmp_path / "pack_configs").resolve(),
            (tmp_path / "config" / "tools").resolve(),
        ]


class TestResolveToolConfigFiles:
    """拡張が同梱した既定 YAML と `config_root/tools` の合成規則。"""

    @staticmethod
    def _touch(directory: Path, *names: str) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        for name in names:
            (directory / name).write_text("type: x\n")

    def test_user_dir_only_without_extensions(self, tmp_path: Path, use_extensions) -> None:
        """拡張が 0 個なら `config_root/tools` だけを stem 順で返す（従来どおり）。"""
        use_extensions()
        self._touch(tmp_path / "tools", "llm_b.yaml", "llm_a.yaml", "task_x.yaml", "note.txt")

        assert tool_paths.resolve_tool_config_files("llm_", tmp_path) == [
            tmp_path / "tools" / "llm_a.yaml",
            tmp_path / "tools" / "llm_b.yaml",
        ]

    def test_bundled_yaml_is_included(
        self, tmp_path: Path, make_extension, use_extensions
    ) -> None:
        """拡張が同梱した YAML は、利用者側に無くても集められる。"""
        pack = tmp_path / "pack" / "tool_configs"
        self._touch(pack, "llm_pack.yaml")
        use_extensions(make_extension("pack", tool_config_roots=[pack]))

        assert tool_paths.resolve_tool_config_files("llm_", tmp_path) == [pack / "llm_pack.yaml"]

    def test_user_yaml_overrides_bundled_one(
        self, tmp_path: Path, make_extension, use_extensions
    ) -> None:
        """同じ stem は `config_root/tools` 側が丸ごと勝つ。"""
        pack = tmp_path / "pack" / "tool_configs"
        self._touch(pack, "llm_pack.yaml", "llm_other.yaml")
        self._touch(tmp_path / "tools", "llm_pack.yaml")
        use_extensions(make_extension("pack", tool_config_roots=[pack]))

        assert tool_paths.resolve_tool_config_files("llm_", tmp_path) == [
            pack / "llm_other.yaml",
            tmp_path / "tools" / "llm_pack.yaml",
        ]

    def test_same_stem_in_two_extensions_fails_fast(
        self, tmp_path: Path, make_extension, use_extensions
    ) -> None:
        """拡張どうしで同じ stem を同梱していたら、両方の拡張名を含めて落とす。"""
        a = tmp_path / "a"
        b = tmp_path / "b"
        self._touch(a, "llm_dup.yaml")
        self._touch(b, "llm_dup.yaml")
        use_extensions(
            make_extension("pack-a", tool_config_roots=[a]),
            make_extension("pack-b", tool_config_roots=[b]),
        )

        with pytest.raises(ValueError, match="bundled by both extensions 'pack-a' and 'pack-b'"):
            tool_paths.resolve_tool_config_files("llm_", tmp_path)

    def test_missing_directories_are_skipped(
        self, tmp_path: Path, make_extension, use_extensions
    ) -> None:
        """存在しないディレクトリは拡張側・利用者側とも読み飛ばす。"""
        use_extensions(make_extension("pack", tool_config_roots=[tmp_path / "nope"]))

        assert tool_paths.resolve_tool_config_files("llm_", tmp_path / "no_config") == []

    def test_prefix_filters_kind(self, tmp_path: Path, make_extension, use_extensions) -> None:
        """`llm_` と `task_` は別々に集める。"""
        pack = tmp_path / "pack"
        self._touch(pack, "llm_x.yaml", "task_y.yaml")
        use_extensions(make_extension("pack", tool_config_roots=[pack]))

        assert tool_paths.resolve_tool_config_files("task_", tmp_path) == [pack / "task_y.yaml"]


class TestIsToolEnabled:
    @pytest.mark.parametrize("config", [{}, {"enabled": True}, {"enabled": "no"}, {"enabled": 0}])
    def test_enabled_unless_explicit_false(self, config: dict) -> None:
        """`enabled: false` と明示したときだけ無効（それ以外の値では止めない）。"""
        assert tool_paths.is_tool_enabled(config)

    def test_explicit_false_disables(self) -> None:
        assert not tool_paths.is_tool_enabled({"enabled": False})


class TestIsImportPath:
    @pytest.mark.parametrize("value", ["pkg.tools.llm_x", "a.b", "lilla_core.builtin_tools.x"])
    def test_dotted_is_import_path(self, value: str) -> None:
        """`.` を含めば import パス。"""
        assert tool_paths.is_import_path(value)

    @pytest.mark.parametrize("value", ["llm_x", "self", "task_foo"])
    def test_plain_stem_is_not(self, value: str) -> None:
        """`.` を含まなければファイル名 stem。"""
        assert not tool_paths.is_import_path(value)


class TestImportToolModule:
    def test_imports_installed_module(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """import できるモジュールはそのまま返す（`sys.modules` にも通常どおり載る）。"""
        import sys
        import uuid

        pkg = f"pkg_{uuid.uuid4().hex}"
        (tmp_path / pkg / "tools").mkdir(parents=True)
        (tmp_path / pkg / "__init__.py").write_text("")
        (tmp_path / pkg / "tools" / "__init__.py").write_text("")
        (tmp_path / pkg / "tools" / "llm_x.py").write_text("MARK = 1\n")
        monkeypatch.syspath_prepend(str(tmp_path))

        module = tool_paths.import_tool_module(f"{pkg}.tools.llm_x")

        assert module is not None and module.MARK == 1
        assert f"{pkg}.tools.llm_x" in sys.modules

    def test_missing_module_returns_none_with_warning(self, caplog) -> None:
        """モジュールが無ければ WARNING を出して None（起動は止めない）。"""
        with caplog.at_level("WARNING"):
            assert tool_paths.import_tool_module("no_such_pkg_xyz.tools.llm_x") is None
        assert "Failed to import tool module 'no_such_pkg_xyz.tools.llm_x'" in caplog.text

    def test_import_error_inside_module_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
    ) -> None:
        """モジュール本体が import 中に例外を出しても None にして先へ進む。"""
        import uuid

        name = f"broken_{uuid.uuid4().hex}"
        (tmp_path / name).mkdir()
        (tmp_path / name / "__init__.py").write_text("")
        (tmp_path / name / "llm_x.py").write_text("raise RuntimeError('boom')\n")
        monkeypatch.syspath_prepend(str(tmp_path))

        with caplog.at_level("WARNING"):
            assert tool_paths.import_tool_module(f"{name}.llm_x") is None
        assert "boom" in caplog.text


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
