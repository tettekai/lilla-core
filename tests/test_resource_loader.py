"""utils/resource_loader.py のテスト。"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_cfg() -> MagicMock:
    """load_text_resources が参照する AppConfig モック。"""
    cfg = MagicMock()
    cfg.env.config_root = Path("/fake/config")
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
def load_text_resources(with_mocked_modules):
    """patch.dict 有効後に load_text_resources をロードする。"""
    sys.modules.pop("lilla_core.utils.resource_loader", None)
    from lilla_core.utils.resource_loader import load_text_resources as loaded
    return loaded


class TestLoadTextResources:
    """load_text_resources() のテスト。"""

    def test_reads_single_file(self, load_text_resources, tmp_path: Path) -> None:
        """file: プレフィックスで単一ファイルを読み込む。"""
        f = tmp_path / "a.md"
        f.write_text("Hello", encoding="utf-8")
        assert load_text_resources(f"file:{f}", config_root=tmp_path) == "Hello"

    def test_string_equivalent_to_single_element_list(self, load_text_resources, tmp_path: Path) -> None:
        """文字列指定は1要素リスト指定と同じ挙動になる。"""
        f = tmp_path / "a.md"
        f.write_text("Hello", encoding="utf-8")
        as_str = load_text_resources(f"file:{f}", config_root=tmp_path)
        as_list = load_text_resources([f"file:{f}"], config_root=tmp_path)
        assert as_str == as_list == "Hello"

    def test_reads_dir_md_and_txt_in_name_order(self, load_text_resources, tmp_path: Path) -> None:
        """dir: プレフィックスで .md / .txt をファイル名昇順に結合する。"""
        d = tmp_path / "prompt"
        d.mkdir()
        (d / "02_second.txt").write_text("Second", encoding="utf-8")
        (d / "01_first.md").write_text("First", encoding="utf-8")
        (d / "ignore.py").write_text("nope", encoding="utf-8")
        assert load_text_resources(f"dir:{d}", config_root=tmp_path) == "First\nSecond"

    def test_dir_missing_returns_empty(self, load_text_resources, tmp_path: Path) -> None:
        """存在しないディレクトリは空文字列を返す。"""
        assert load_text_resources(f"dir:{tmp_path}/nope", config_root=tmp_path) == ""

    def test_missing_file_is_skipped(self, load_text_resources, tmp_path: Path) -> None:
        """存在しないファイルはスキップされる。"""
        f = tmp_path / "a.md"
        f.write_text("Hello", encoding="utf-8")
        missing = tmp_path / "missing.md"
        result = load_text_resources(
            [f"file:{f}", f"file:{missing}"], config_root=tmp_path
        )
        assert result == "Hello"

    def test_mixes_file_and_dir(self, load_text_resources, tmp_path: Path) -> None:
        """file: と dir: の混在指定を順に結合する。"""
        d = tmp_path / "notes"
        d.mkdir()
        (d / "01.md").write_text("Note", encoding="utf-8")
        extra = tmp_path / "extra.md"
        extra.write_text("Extra", encoding="utf-8")
        result = load_text_resources(
            [f"dir:{d}", f"file:{extra}"], config_root=tmp_path
        )
        assert result == "Note\nExtra"

    def test_expands_config_root_placeholder(self, load_text_resources, tmp_path: Path) -> None:
        """${config_root} が config_root 引数で展開される。"""
        f = tmp_path / "a.md"
        f.write_text("Hello", encoding="utf-8")
        result = load_text_resources(
            "file:${config_root}/a.md", config_root=tmp_path
        )
        assert result == "Hello"

    def test_uses_get_config_when_config_root_omitted(
        self, load_text_resources, with_mocked_modules, tmp_path: Path
    ) -> None:
        """config_root 未指定時は get_config().config_root が使われる。"""
        core_config = sys.modules["lilla_core.core.config"]

        f = tmp_path / "a.md"
        f.write_text("Hello", encoding="utf-8")
        fake_cfg = MagicMock()
        fake_cfg.env.config_root = tmp_path
        original = core_config.get_config
        core_config.get_config = lambda: fake_cfg
        try:
            assert load_text_resources("file:${config_root}/a.md") == "Hello"
        finally:
            core_config.get_config = original

    def test_no_extra_blank_line_with_trailing_newline(self, load_text_resources, tmp_path: Path) -> None:
        """ファイル末尾に改行があっても結合後に余分な空行が入らない。"""
        d = tmp_path / "prompt"
        d.mkdir()
        (d / "01_a.md").write_text("A\n", encoding="utf-8")
        (d / "02_b.md").write_text("B\n", encoding="utf-8")
        assert load_text_resources(f"dir:{d}", config_root=tmp_path) == "A\nB"

    def test_prefixless_spec_raises_value_error(self, load_text_resources, tmp_path: Path) -> None:
        """プレフィックスなしの spec は ValueError を送出する。"""
        with pytest.raises(ValueError):
            load_text_resources(f"{tmp_path}/a.md", config_root=tmp_path)

    def test_unknown_prefix_raises_value_error(self, load_text_resources, tmp_path: Path) -> None:
        """未知のプレフィックスは ValueError を送出する。"""
        with pytest.raises(ValueError):
            load_text_resources("url:https://example.com/x.md", config_root=tmp_path)
