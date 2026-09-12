"""script_loader.py のテスト。
実際の .py ファイルを tmp_path に生成して importlib 経由でロードする動作を検証する。
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_cfg() -> MagicMock:
    """script_loader が参照する AppConfig モック。"""
    cfg = MagicMock()
    cfg.paths.allowed_tool_paths_list = []  # ALLOWED_PATHS の初期値は空（autouse で上書き）
    return cfg


@pytest.fixture
def with_mocked_modules(mock_cfg: MagicMock):
    """依存モジュールを patch.dict で差し替える。"""
    with patch.dict(
        sys.modules,
        {"lilla_core.core.config": MagicMock(get_config=lambda: mock_cfg)},
    ):
        yield


@pytest.fixture
def script_loader(with_mocked_modules):
    """patch.dict 有効後に script_loader をロードする。"""
    sys.modules.pop("lilla_core.loaders.script_loader", None)
    import lilla_core.loaders.script_loader as loaded
    yield loaded
    sys.modules.pop("lilla_core.loaders.script_loader", None)


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------


def write_script(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# autouse フィクスチャ: tmp_path をホワイトリストに追加
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def allow_tmp_path(script_loader, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(script_loader, "_get_allowed_paths", lambda: [tmp_path.resolve()])


# ---------------------------------------------------------------------------
# TestLoadScriptFunction
# ---------------------------------------------------------------------------


class TestLoadScriptFunction:
    def test_returns_function(self, script_loader, tmp_path: Path) -> None:
        """正常系: 関数が返る"""
        script = write_script(
            tmp_path,
            "my_tool.py",
            "def greet():\n    return 'hello'\n",
        )
        result = script_loader.load_script_function(script, "greet")
        assert result is not None

    def test_function_is_callable(self, script_loader, tmp_path: Path) -> None:
        """返った関数が実行できる"""
        script = write_script(
            tmp_path,
            "my_tool.py",
            "def greet():\n    return 'hello'\n",
        )
        func = script_loader.load_script_function(script, "greet")
        assert callable(func)
        assert func() == "hello"

    def test_path_outside_allowed_returns_none(self, script_loader, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """ホワイトリスト外のパス → None"""
        other_dir = tmp_path / "other"
        other_dir.mkdir()
        script = write_script(other_dir, "tool.py", "def run(): pass\n")
        # ホワイトリストを tmp_path/allowed のみに限定
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        monkeypatch.setattr(script_loader, "_get_allowed_paths", lambda: [allowed.resolve()])
        assert script_loader.load_script_function(script, "run") is None

    def test_nonexistent_file_returns_none(self, script_loader, tmp_path: Path) -> None:
        """存在しないファイル → None"""
        script = tmp_path / "ghost.py"
        assert script_loader.load_script_function(script, "run") is None

    def test_non_py_file_returns_none(self, script_loader, tmp_path: Path) -> None:
        """.py 以外の拡張子 → None"""
        script = write_script(tmp_path, "tool.txt", "def run(): pass\n")
        assert script_loader.load_script_function(script, "run") is None

    def test_function_not_found_returns_none(self, script_loader, tmp_path: Path) -> None:
        """指定関数名が存在しない → None"""
        script = write_script(tmp_path, "tool.py", "def greet(): pass\n")
        assert script_loader.load_script_function(script, "no_such_func") is None

    def test_non_callable_attr_returns_none(self, script_loader, tmp_path: Path) -> None:
        """関数でなく変数を指定した場合 → None"""
        script = write_script(tmp_path, "tool.py", "MY_VAR = 42\n")
        assert script_loader.load_script_function(script, "MY_VAR") is None


# ---------------------------------------------------------------------------
# TestLoadScriptClass
# ---------------------------------------------------------------------------


class TestLoadScriptClass:
    def test_returns_class_by_name(self, script_loader, tmp_path: Path) -> None:
        """class_name 指定で正しいクラスが返る"""
        script = write_script(
            tmp_path,
            "tool.py",
            "class MyTool:\n    def execute(self): return 'ok'\n",
        )
        cls = script_loader.load_script_class(script, class_name="MyTool")
        assert cls is not None

    def test_class_is_instantiatable(self, script_loader, tmp_path: Path) -> None:
        """返ったクラスがインスタンス化できる"""
        script = write_script(
            tmp_path,
            "tool.py",
            "class MyTool:\n    def execute(self): return 'ok'\n",
        )
        cls = script_loader.load_script_class(script, class_name="MyTool")
        instance = cls()
        assert instance.execute() == "ok"

    def test_auto_detect_class(self, script_loader, tmp_path: Path) -> None:
        """class_name=None で execute メソッドを持つクラスを自動検出する"""
        script = write_script(
            tmp_path,
            "tool.py",
            "class AutoTool:\n    def execute(self): return 'auto'\n",
        )
        cls = script_loader.load_script_class(script)
        assert cls is not None
        assert cls().execute() == "auto"

    def test_class_without_execute_returns_none(self, script_loader, tmp_path: Path) -> None:
        """execute メソッドなしのクラスのみの場合 → None"""
        script = write_script(
            tmp_path,
            "tool.py",
            "class NoExec:\n    def run(self): pass\n",
        )
        assert script_loader.load_script_class(script) is None

    def test_path_outside_allowed_returns_none(self, script_loader, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """ホワイトリスト外のパス → None"""
        other_dir = tmp_path / "other"
        other_dir.mkdir()
        script = write_script(
            other_dir,
            "tool.py",
            "class T:\n    def execute(self): pass\n",
        )
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        monkeypatch.setattr(script_loader, "_get_allowed_paths", lambda: [allowed.resolve()])
        assert script_loader.load_script_class(script) is None

    def test_nonexistent_file_returns_none(self, script_loader, tmp_path: Path) -> None:
        """存在しないファイル → None"""
        script = tmp_path / "ghost.py"
        assert script_loader.load_script_class(script) is None

    def test_class_name_not_found_returns_none(self, script_loader, tmp_path: Path) -> None:
        """指定クラス名が存在しない → None"""
        script = write_script(
            tmp_path,
            "tool.py",
            "class MyTool:\n    def execute(self): pass\n",
        )
        assert script_loader.load_script_class(script, class_name="NoSuchClass") is None

    def test_attr_is_not_type_returns_none(self, script_loader, tmp_path: Path) -> None:
        """type でない属性を class_name に指定 → None"""
        script = write_script(
            tmp_path,
            "tool.py",
            "MY_CLASS = 'not_a_class'\n",
        )
        assert script_loader.load_script_class(script, class_name="MY_CLASS") is None
