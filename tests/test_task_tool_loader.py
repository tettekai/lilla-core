"""task_tool_loader.py のテスト。

難易度別に対象を選定:
- _get_trigger_from_filename : 純粋関数、全パターン検証
- _build_tool_entry          : 純粋関数、MagicMock を instance に渡して検証
- _load_tool_configs         : YAML ファイル I/O、tmp_path で実ファイル生成
- _resolve_tool_class        : load_script_class をモック化して検証
- load_all_tools             : 上記の組み合わせ（統合テスト）

対象外:
- load_script_class の実ファイルロード経路（script_loader テストで検証済み）
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_cfg() -> MagicMock:
    """task_tool_loader が参照する AppConfig モック。"""
    cfg = MagicMock()
    cfg.paths.tool_root = Path("/fake/tools")
    cfg.env.config_root = Path("/fake/config")
    return cfg


@pytest.fixture
def with_mocked_modules(mock_cfg: MagicMock):
    """依存モジュールを patch.dict で差し替える。"""
    with patch.dict(
        sys.modules,
        {
            "lilla_core.core.config": MagicMock(get_config=lambda: mock_cfg),
            "lilla_core.loaders.script_loader": MagicMock(load_script_class=MagicMock(return_value=None)),
        },
    ):
        yield


@pytest.fixture
def task_tool_loader(with_mocked_modules):
    """patch.dict 有効後に task_tool_loader をロードする。"""
    sys.modules.pop("lilla_core.loaders.task_tool_loader", None)
    import lilla_core.loaders.task_tool_loader as loaded
    yield loaded
    sys.modules.pop("lilla_core.loaders.task_tool_loader", None)


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------


def _make_tool_class(**attrs):
    """テスト用の最小ツールクラスを動的生成する。"""

    def __init__(self, config, name=""):
        pass

    return type("FakeTool", (), {"execute": lambda self: None, "__init__": __init__, **attrs})


def _write_yaml(directory: Path, name: str, content: str) -> Path:
    p = directory / name
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# TestGetTriggerFromFilename
# ---------------------------------------------------------------------------


class TestGetTriggerFromFilename:
    @pytest.mark.parametrize(
        "filename, expected",
        [
            ("task_send_message", "task"),
            ("task_", "task"),
            ("llm_obsidian_write", "llm"),
            ("llm_", "llm"),
            ("system_startup", "system"),
            ("system_", "system"),
            ("message_reply", "other"),
            ("other_thing", "other"),
            ("unknown", "other"),
            ("", "other"),
        ],
    )
    def test_trigger_from_prefix(self, task_tool_loader, filename: str, expected: str) -> None:
        assert task_tool_loader._get_trigger_from_filename(filename) == expected


# ---------------------------------------------------------------------------
# TestBuildToolEntry
# ---------------------------------------------------------------------------


class TestBuildToolEntry:
    def test_returns_all_keys(self, task_tool_loader) -> None:
        instance = MagicMock()
        entry = task_tool_loader._build_tool_entry(instance, "task_foo", {})
        assert set(entry.keys()) == {"instance", "description", "category", "trigger", "scheduled"}

    def test_instance_is_stored(self, task_tool_loader) -> None:
        instance = MagicMock()
        entry = task_tool_loader._build_tool_entry(instance, "task_foo", {})
        assert entry["instance"] is instance

    def test_description_from_instance(self, task_tool_loader) -> None:
        instance = MagicMock(description="テスト説明")
        entry = task_tool_loader._build_tool_entry(instance, "task_foo", {})
        assert entry["description"] == "テスト説明"

    def test_category_from_instance(self, task_tool_loader) -> None:
        instance = MagicMock(category="schedule")
        entry = task_tool_loader._build_tool_entry(instance, "task_foo", {})
        assert entry["category"] == "schedule"

    def test_trigger_derived_from_tool_type(self, task_tool_loader) -> None:
        instance = MagicMock()
        entry = task_tool_loader._build_tool_entry(instance, "task_foo", {})
        assert entry["trigger"] == "task"

    def test_default_description_when_missing(self, task_tool_loader) -> None:
        """description 属性がない instance → 空文字列がデフォルト"""
        instance = MagicMock(spec=[])  # 属性アクセスで AttributeError になるスペック
        entry = task_tool_loader._build_tool_entry(instance, "task_foo", {})
        assert entry["description"] == ""

    def test_default_category_when_missing(self, task_tool_loader) -> None:
        """category 属性がない instance → 'unknown' がデフォルト"""
        instance = MagicMock(spec=[])
        entry = task_tool_loader._build_tool_entry(instance, "task_foo", {})
        assert entry["category"] == "unknown"


# ---------------------------------------------------------------------------
# TestLoadToolConfigs
# ---------------------------------------------------------------------------


class TestLoadToolConfigsBundled:
    """拡張が同梱した `task_*.yaml` と `enabled: false` の扱い。"""

    def test_bundled_yaml_is_loaded_and_user_yaml_overrides(
        self, task_tool_loader, tmp_path: Path, make_extension, use_extensions
    ) -> None:
        """同梱 YAML が集められ、同名の利用者 YAML があればそちらが勝つ。"""
        configs = tmp_path / "pack"
        configs.mkdir()
        (configs / "task_a.yaml").write_text("type: task_a\nfrom: pack\n")
        (configs / "task_b.yaml").write_text("type: task_b\nfrom: pack\n")
        (tmp_path / "tools").mkdir()
        (tmp_path / "tools" / "task_b.yaml").write_text("type: task_b\nfrom: user\n")
        use_extensions(make_extension("pack", tool_config_roots=[configs]))

        result = dict(task_tool_loader._load_tool_configs(tmp_path))

        assert result[str(configs / "task_a.yaml")]["from"] == "pack"
        assert result[str(tmp_path / "tools" / "task_b.yaml")]["from"] == "user"
        assert str(configs / "task_b.yaml") not in result

    def test_disabled_yaml_is_excluded(
        self, task_tool_loader, tmp_path: Path, use_extensions
    ) -> None:
        """`enabled: false` の YAML は結果に含めない。"""
        use_extensions()
        (tmp_path / "tools").mkdir()
        (tmp_path / "tools" / "task_off.yaml").write_text("type: task_off\nenabled: false\n")
        (tmp_path / "tools" / "task_on.yaml").write_text("type: task_on\n")

        paths = [p for p, _ in task_tool_loader._load_tool_configs(tmp_path)]

        assert paths == [str(tmp_path / "tools" / "task_on.yaml")]


class TestLoadToolConfigs:
    def test_returns_empty_when_tools_dir_missing(self, task_tool_loader, tmp_path: Path) -> None:
        """tools/ サブディレクトリが存在しない → 空リスト"""
        result = task_tool_loader._load_tool_configs(tmp_path)
        assert result == []

    def test_returns_empty_when_no_yaml_files(self, task_tool_loader, tmp_path: Path) -> None:
        """tools/ ディレクトリはあるが YAML がない → 空リスト"""
        (tmp_path / "tools").mkdir()
        result = task_tool_loader._load_tool_configs(tmp_path)
        assert result == []

    def test_returns_parsed_config(self, task_tool_loader, tmp_path: Path) -> None:
        """task_*.yaml を正しくパースして返す"""
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        _write_yaml(tools_dir, "task_my_tool.yaml", "type: task_foo\nname: Foo Tool\n")

        result = task_tool_loader._load_tool_configs(tmp_path)

        assert len(result) == 1
        path, config = result[0]
        assert config["type"] == "task_foo"
        assert config["name"] == "Foo Tool"

    def test_path_is_included_in_result(self, task_tool_loader, tmp_path: Path) -> None:
        """返り値の 1 要素目がファイルパス文字列"""
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        yaml_file = _write_yaml(tools_dir, "task_my_tool.yaml", "type: task_foo\n")

        path, _ = task_tool_loader._load_tool_configs(tmp_path)[0]
        assert path == str(yaml_file)

    def test_returns_multiple_configs(self, task_tool_loader, tmp_path: Path) -> None:
        """複数 task_*.yaml ファイルをすべて返す"""
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        _write_yaml(tools_dir, "task_a.yaml", "type: task_a\n")
        _write_yaml(tools_dir, "task_b.yaml", "type: task_b\n")

        result = task_tool_loader._load_tool_configs(tmp_path)
        assert len(result) == 2

    def test_ignores_non_yaml_files(self, task_tool_loader, tmp_path: Path) -> None:
        """非 YAML ファイルは無視される"""
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        (tools_dir / "readme.txt").write_text("type: task_foo\n")

        result = task_tool_loader._load_tool_configs(tmp_path)
        assert result == []

    def test_ignores_non_task_yaml_files(self, task_tool_loader, tmp_path: Path) -> None:
        """task_ プレフィックスのない YAML ファイルは無視される"""
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        _write_yaml(tools_dir, "my_tool.yaml", "type: task_foo\n")
        _write_yaml(tools_dir, "llm_tool.yaml", "type: llm_foo\n")

        result = task_tool_loader._load_tool_configs(tmp_path)
        assert result == []


# ---------------------------------------------------------------------------
# TestResolveToolClass
# ---------------------------------------------------------------------------


class TestResolveToolClass:
    def test_returns_class_on_successful_load(
        self, task_tool_loader, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """rglob でファイルが見つかり load_script_class がクラスを返す → そのクラスを返す"""
        FakeClass = _make_tool_class()
        # rglob が返すファイルを用意する
        py_file = tmp_path / "schedule" / "task_foo.py"
        py_file.parent.mkdir(parents=True)
        py_file.touch()
        monkeypatch.setattr(task_tool_loader, "load_script_class", lambda path, **kw: FakeClass)

        result = task_tool_loader._resolve_tool_class("task_foo", [tmp_path], {})
        assert result is FakeClass

    def test_import_path_uses_importlib_not_file_search(
        self, task_tool_loader, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`.` を含む type は import パスとして解決し、ファイル探索も load_script_class も使わない。"""
        FakeClass = _make_tool_class()
        fake_module = MagicMock()
        monkeypatch.setattr(task_tool_loader, "import_tool_module", lambda t: fake_module)
        monkeypatch.setattr(task_tool_loader, "find_tool_class", lambda m: FakeClass if m is fake_module else None)
        monkeypatch.setattr(
            task_tool_loader, "load_script_class", lambda *a, **kw: pytest.fail("file loader must not be used")
        )

        result = task_tool_loader._resolve_tool_class("pkg.tools.task_foo", [tmp_path], {})

        assert result is FakeClass

    def test_import_path_failure_returns_none(
        self, task_tool_loader, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """import に失敗したら None（そのツールだけスキップ）。"""
        monkeypatch.setattr(task_tool_loader, "import_tool_module", lambda t: None)

        assert task_tool_loader._resolve_tool_class("pkg.tools.task_foo", [tmp_path], {}) is None

    def test_returns_none_when_load_fails(
        self, task_tool_loader, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ファイルが存在しても load_script_class が None を返す → None"""
        py_file = tmp_path / "task_foo.py"
        py_file.touch()
        monkeypatch.setattr(task_tool_loader, "load_script_class", lambda path, **kw: None)

        result = task_tool_loader._resolve_tool_class("task_foo", [tmp_path], {})
        assert result is None

    def test_returns_none_when_no_file_found(
        self, task_tool_loader, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """rglob でファイルが見つからない → None（load_script_class は呼ばれない）"""
        called = []
        monkeypatch.setattr(task_tool_loader, "load_script_class", lambda path, **kw: called.append(path) or None)

        result = task_tool_loader._resolve_tool_class("task_foo", [tmp_path], {})
        assert result is None
        assert called == []

    def test_finds_file_via_rglob(
        self, task_tool_loader, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """rglob でサブディレクトリ内のファイルを検索して load_script_class に渡す"""
        captured = []
        py_file = tmp_path / "schedule" / "task_foo.py"
        py_file.parent.mkdir(parents=True)
        py_file.touch()
        monkeypatch.setattr(task_tool_loader, "load_script_class", lambda path, **kw: captured.append(path) or None)

        task_tool_loader._resolve_tool_class("task_foo", [tmp_path], {})
        assert captured[0] == py_file

    def test_caches_loaded_class(
        self, task_tool_loader, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """同じ tool_type を 2 回呼んでも load_script_class は 1 度しか呼ばれない"""
        call_count = 0
        py_file = tmp_path / "task_foo.py"
        py_file.touch()

        def fake_load(path, **kw):
            nonlocal call_count
            call_count += 1
            return _make_tool_class()

        monkeypatch.setattr(task_tool_loader, "load_script_class", fake_load)

        class_map: dict = {}
        task_tool_loader._resolve_tool_class("task_foo", [tmp_path], class_map)
        task_tool_loader._resolve_tool_class("task_foo", [tmp_path], class_map)
        assert call_count == 1

    def test_returns_cached_class(
        self, task_tool_loader, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """class_map に既にある tool_type は load_script_class を呼ばずキャッシュを返す"""
        FakeClass = _make_tool_class()
        class_map = {"task_foo": FakeClass}
        # load_script_class が呼ばれたら失敗させる
        monkeypatch.setattr(task_tool_loader, "load_script_class", lambda path, **kw: None)

        result = task_tool_loader._resolve_tool_class("task_foo", [tmp_path], class_map)
        assert result is FakeClass


# ---------------------------------------------------------------------------
# TestLoadAllTools
# ---------------------------------------------------------------------------


class TestLoadAllTools:
    @pytest.fixture()
    def config_root(self, tmp_path: Path) -> Path:
        cr = tmp_path / "config_root"
        (cr / "tools").mkdir(parents=True)
        return cr

    @pytest.fixture()
    def tool_root(self, tmp_path: Path) -> Path:
        return tmp_path / "tool_root"

    def test_returns_empty_when_no_yaml(
        self, task_tool_loader, tool_root: Path, config_root: Path
    ) -> None:
        result = task_tool_loader.load_all_tools(
            tool_roots=[tool_root], config_root=config_root, class_map={}
        )
        assert result == {}

    def test_skips_yaml_without_type(
        self,
        task_tool_loader,
        tool_root: Path,
        config_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """type フィールドなし YAML → スキップ（空 dict）"""
        _write_yaml(config_root / "tools", "task_no_type.yaml", "name: No Type\n")
        monkeypatch.setattr(task_tool_loader, "load_script_class", lambda path, **kw: None)

        result = task_tool_loader.load_all_tools(
            tool_roots=[tool_root], config_root=config_root, class_map={}
        )
        assert result == {}

    def test_skips_when_class_not_found(
        self,
        task_tool_loader,
        tool_root: Path,
        config_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """対応する .py が見つからない → スキップ"""
        _write_yaml(config_root / "tools", "task_tool.yaml", "type: task_foo\n")
        monkeypatch.setattr(task_tool_loader, "load_script_class", lambda path, **kw: None)

        result = task_tool_loader.load_all_tools(
            tool_roots=[tool_root], config_root=config_root, class_map={}
        )
        assert result == {}

    def test_loads_tool_by_yaml_filename(
        self,
        task_tool_loader,
        tool_root: Path,
        config_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """正常系: YAML ファイル名（拡張子なし）がキーになる。name フィールドは無視される"""
        _write_yaml(
            config_root / "tools", "task_tool.yaml", "type: task_foo\nname: My Tool\n"
        )
        py_file = tool_root / "task_foo.py"
        tool_root.mkdir(parents=True)
        py_file.touch()
        monkeypatch.setattr(task_tool_loader, "load_script_class", lambda path, **kw: _make_tool_class())

        result = task_tool_loader.load_all_tools(
            tool_roots=[tool_root], config_root=config_root, class_map={}
        )
        assert "task_tool" in result

    def test_uses_yaml_filename_as_name(
        self,
        task_tool_loader,
        tool_root: Path,
        config_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """name フィールドなしでも YAML ファイル名がキーになる"""
        _write_yaml(config_root / "tools", "task_foo.yaml", "type: task_foo\n")
        py_file = tool_root / "task_foo.py"
        tool_root.mkdir(parents=True)
        py_file.touch()
        monkeypatch.setattr(task_tool_loader, "load_script_class", lambda path, **kw: _make_tool_class())

        result = task_tool_loader.load_all_tools(
            tool_roots=[tool_root], config_root=config_root, class_map={}
        )
        assert "task_foo" in result

    def test_tool_entry_has_required_keys(
        self,
        task_tool_loader,
        tool_root: Path,
        config_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """返された各エントリに instance / description / category / trigger が含まれる"""
        _write_yaml(
            config_root / "tools", "task_tool.yaml", "type: task_foo\n"
        )
        py_file = tool_root / "task_foo.py"
        tool_root.mkdir(parents=True)
        py_file.touch()
        monkeypatch.setattr(task_tool_loader, "load_script_class", lambda path, **kw: _make_tool_class())

        result = task_tool_loader.load_all_tools(
            tool_roots=[tool_root], config_root=config_root, class_map={}
        )
        entry = result["task_tool"]
        assert set(entry.keys()) == {"instance", "description", "category", "trigger", "scheduled"}

    def test_does_not_mutate_global_tool_class_map(
        self,
        task_tool_loader,
        tool_root: Path,
        config_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """独自 class_map を渡せばグローバル tool_class_map は変化しない"""
        _write_yaml(config_root / "tools", "task_tool.yaml", "type: task_foo\n")
        py_file = tool_root / "task_foo.py"
        tool_root.mkdir(parents=True)
        py_file.touch()
        monkeypatch.setattr(task_tool_loader, "load_script_class", lambda path, **kw: _make_tool_class())

        before = dict(task_tool_loader.tool_class_map)
        task_tool_loader.load_all_tools(
            tool_roots=[tool_root], config_root=config_root, class_map={}
        )
        assert task_tool_loader.tool_class_map == before
