"""`lilla_core.extensions.notes` の `notes` 拡張のテスト。"""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from lilla_core.extensions import notes

_PACKAGE_DIR = Path(notes.__file__).resolve().parent
_TOOL_STEMS = {"llm_notes_list", "llm_notes_read", "llm_notes_write", "llm_notes_search"}


class TestNotesExtensionDeclaration:
    """拡張の申告。"""

    def test_name_and_section(self) -> None:
        """名前は `notes` で、`extensions.notes` の設定を申告する。"""
        assert notes.extension.name == "notes"
        assert notes.extension.config_model() is notes.NotesConfig

    def test_tool_and_locale_dirs(self) -> None:
        """ツールと文言カタログの場所を返す。"""
        assert notes.extension.tool_roots() == [_PACKAGE_DIR / "tools"]
        assert notes.extension.locale_dirs() == [_PACKAGE_DIR / "locales"]

    def test_file_storage(self) -> None:
        """保存先 `notes` を申告する。"""
        from lilla_core.core.file_storage import FileStorage

        storages = notes.extension.file_storages()
        assert list(storages) == ["notes"]
        assert isinstance(storages["notes"], FileStorage)

    def test_dashboard(self) -> None:
        """常用ナビの「ノート」タブと page.js、`/api/notes` 配下のルートを申告する。"""
        page = notes.extension.dashboard_page()
        assert (page.label, page.group) == ("ノート", "main")
        assert (notes.extension.dashboard_static_dir() / "page.js").is_file()
        routes = notes.extension.dashboard_routes()
        assert routes and all(r.path.startswith("/api/notes/") for r in routes)

    def test_startup_repos(self) -> None:
        """リポジトリのファクトリを返す。"""
        from lilla_core.extensions.notes.repository import get_notes_repo

        assert notes.extension.startup_repos() == [get_notes_repo]


class TestNotesConfig:
    """設定のモデル。"""

    def test_defaults(self) -> None:
        """全項目に既定値がある。"""
        config = notes.NotesConfig()
        assert config.max_content_chars == notes.DEFAULT_MAX_CONTENT_CHARS
        assert config.max_read_chars == notes.DEFAULT_MAX_READ_CHARS
        assert config.history_retention_days == notes.DEFAULT_HISTORY_RETENTION_DAYS

    @pytest.mark.parametrize(
        "values",
        [
            {"max_content_chars": 0},
            {"max_content_chars": notes.MAX_CONTENT_CHARS_LIMIT + 1},
            {"max_read_chars": 0},
            {"history_retention_days": 0},
            {"unknown": 1},
        ],
    )
    def test_rejects_invalid_values(self, values) -> None:
        """範囲外・未知の項目は落とす。"""
        with pytest.raises(ValidationError):
            notes.NotesConfig(**values)


class TestComposition:
    """コアへの登録。"""

    @pytest.fixture
    def config_root(self, tmp_path: Path) -> Path:
        """`extensions.notes` だけを持つ最小構成の `lilla.yaml` を書いたディレクトリ。"""
        from lilla_core.testing import write_minimal_lilla_yaml

        write_minimal_lilla_yaml(
            tmp_path, extra={"extensions": {"notes": {"max_read_chars": 500}}}
        )
        return tmp_path

    def test_registered(self, config_root: Path) -> None:
        """設定の合成・保存先・ダッシュボードのページが登録される。"""
        from lilla_core.core.config import get_section
        from lilla_core.core.extension import get_dashboard_pages, get_file_storage
        from lilla_core.testing import use_extensions

        with use_extensions(notes.extension, config_root=config_root) as cfg:
            assert get_section("notes", notes.NotesConfig, cfg).max_read_chars == 500
            assert get_file_storage("notes") is not None
            assert [p.name for p in get_dashboard_pages()] == ["notes"]

    def test_tools_are_loaded(self, config_root: Path) -> None:
        """利用者の YAML に `type: <ファイル名>` と書けば 4 つのツールがロードされる。"""
        from lilla_core.loaders import llm_tool_loader
        from lilla_core.testing import use_extensions

        tools_dir = config_root / "tools"
        tools_dir.mkdir()
        for stem in _TOOL_STEMS:
            (tools_dir / f"{stem}.yaml").write_text(f"type: {stem}\n")

        with use_extensions(notes.extension, config_root=config_root):
            tools = llm_tool_loader.load_llm_tools(config_root=config_root)

        assert set(tools) == _TOOL_STEMS

    def test_locale_catalog_is_valid(self, config_root: Path) -> None:
        """文言カタログが登録時の検証を通り、日英で引ける。"""
        from lilla_core.testing import use_extensions
        from lilla_core.ui import messages

        with use_extensions(notes.extension, config_root=config_root):
            catalogs = {locale: messages._load_catalog(locale) for locale in ("ja", "en")}

        for catalog in catalogs.values():
            assert "{tool}" in catalog["notes"]["tool"]["unexpected_error"]
