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

    def test_declares_bundled_tool_configs(self) -> None:
        """同梱のツール YAML のディレクトリに 4 つのツールの YAML がある。"""
        roots = notes.extension.tool_config_roots()

        assert roots == [_PACKAGE_DIR / "tool_configs"]
        assert {p.stem for p in roots[0].glob("*.yaml")} == _TOOL_STEMS

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

    def test_declares_user_dir(self) -> None:
        """notes 自身が宣言するトップレベルは `user/` だけ（`diary/` は持たない）。"""
        assert list(notes.extension.notes_directories()) == ["user"]

    def test_default_prompt_lists_writable_dirs(self, declare_notes_dirs) -> None:
        """デフォルトのプロンプト（ja / en）にルート直下へ書けないことと、書けるディレクトリが出る。"""
        declare_notes_dirs({})
        prompt = notes.extension.default_prompt()
        assert set(prompt) == {"ja", "en"}
        assert "ルート直下" in prompt["ja"] and "root" in prompt["en"]
        for text in prompt.values():
            assert "`user/`" in text and "diary" not in text

    def test_default_prompt_follows_declarations(self, declare_notes_dirs) -> None:
        """拡張が宣言したディレクトリと用途が ja / en でプロンプトに出る。"""
        declare_notes_dirs({"system": {"ja": "システム用", "en": "for the system"}})
        prompt = notes.extension.default_prompt()
        assert "- `user/`: " in prompt["ja"] and "- `system/`: システム用" in prompt["ja"]
        assert "- `system/`: for the system" in prompt["en"]

    def test_registered_default_prompt_includes_declarations(self, declare_notes_dirs) -> None:
        """登録時に集めたデフォルトのプロンプトにも、同じ登録で宣言したディレクトリが入る。"""
        from lilla_core.core.extension import get_default_prompts

        declare_notes_dirs({"system": {"ja": "システム用", "en": "for the system"}})
        prompts = dict(get_default_prompts())
        assert "- `system/`: システム用" in prompts["notes"]["ja"]

    def test_default_prompt_has_no_paths(self) -> None:
        """用途の説明には具体的なファイルのパスやパスの形を書かない。"""
        from lilla_core.extensions.notes import paths

        for usage in paths.CORE_WRITABLE_TOP_LEVEL_DIRS.values():
            for text in usage.values():
                assert "/" not in text and "{" not in text

    def test_no_startup_repos(self) -> None:
        """MongoDB のコレクションは使わない。"""
        assert notes.extension.startup_repos() == []

    async def test_setup_initializes_repository(self, tmp_path: Path, monkeypatch) -> None:
        """`setup()` が `repo_path` に bare リポジトリを作る。"""
        from lilla_core.extensions.notes import repository
        from lilla_core.extensions.notes.repository import NotesRepository

        repo = NotesRepository(tmp_path / "notes.git")
        monkeypatch.setattr(repository, "get_notes_repo", lambda: repo)
        await notes.extension.setup(None)
        assert (tmp_path / "notes.git" / "HEAD").is_file()

    async def test_setup_failure_does_not_stop_startup(self, tmp_path: Path, monkeypatch, caplog) -> None:
        """開けなくても例外を投げず、ERROR ログだけを出す。"""
        import logging

        from lilla_core.extensions.notes import repository
        from lilla_core.extensions.notes.repository import NotesRepository

        path = tmp_path / "notes.git"
        path.write_text("not a directory")
        monkeypatch.setattr(repository, "get_notes_repo", lambda: NotesRepository(path))
        with caplog.at_level(logging.ERROR):
            await notes.extension.setup(None)
        assert any("notes repository" in r.getMessage() for r in caplog.records)


class TestNotesConfig:
    """設定のモデル。"""

    def test_defaults(self) -> None:
        """全項目に既定値がある。"""
        config = notes.NotesConfig()
        assert config.max_content_chars == notes.DEFAULT_MAX_CONTENT_CHARS
        assert config.max_read_chars == notes.DEFAULT_MAX_READ_CHARS
        assert config.repo_path == notes.DEFAULT_REPO_PATH
        assert config.deletion_alert_lines == notes.DEFAULT_DELETION_ALERT_LINES

    @pytest.mark.parametrize(
        "values",
        [
            {"max_content_chars": 0},
            {"max_content_chars": notes.MAX_CONTENT_CHARS_LIMIT + 1},
            {"max_read_chars": 0},
            {"deletion_alert_lines": 0},
            {"repo_path": "relative/notes.git"},
            {"unknown": 1},
        ],
    )
    def test_rejects_invalid_values(self, values) -> None:
        """範囲外・未知の項目は落とす。"""
        with pytest.raises(ValidationError):
            notes.NotesConfig(**values)


    def test_rejects_removed_history_retention_days(self) -> None:
        """廃止した `history_retention_days` は移行先を示して落とす。"""
        with pytest.raises(ValidationError, match="history_retention_days"):
            notes.NotesConfig(history_retention_days=30)


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
        """利用者の YAML が無くても、同梱の YAML で 4 つのツールがロードされる。"""
        from lilla_core.loaders import llm_tool_loader
        from lilla_core.testing import use_extensions

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
            alert = catalog["notes"]["alert"]["large_deletion"]
            assert all(f"{{{key}}}" in alert for key in ("path", "removed", "added", "commit"))
            assert catalog["notes"]["alert"]["patch_truncated"]


class TestBundledToolConfigs:
    """同梱のツール YAML のロード（ホストの YAML による置き換え・`enabled: false`）。"""

    @pytest.fixture
    def config_root(self, tmp_path: Path) -> Path:
        """ホストの `${CONFIG_ROOT}` に見立てた空のディレクトリ（`tools/` つき）を返す。"""
        (tmp_path / "tools").mkdir()
        return tmp_path

    def _load(self, config_root: Path, use_extensions) -> dict[str, dict]:
        """notes だけを登録して LLM ツールをロードする。"""
        from lilla_core.loaders.llm_tool_loader import load_llm_tools

        use_extensions(notes.extension)
        return load_llm_tools(tool_roots=notes.extension.tool_roots(), config_root=config_root)

    def test_loads_all_tools_without_host_yaml(self, config_root: Path, use_extensions) -> None:
        """ホストに YAML が無くても 4 つのツールがロードされる。"""
        tools = self._load(config_root, use_extensions)

        assert set(tools) == _TOOL_STEMS

    def test_override_yaml_is_merged(self, config_root: Path, use_extensions) -> None:
        """`extensions/notes/tools/` の差分が同梱分に重なる。"""
        override_dir = config_root / "extensions" / "notes" / "tools"
        override_dir.mkdir(parents=True)
        (override_dir / "llm_notes_read.yaml").write_text(
            "description: host description\n", encoding="utf-8"
        )

        tools = self._load(config_root, use_extensions)

        assert tools["llm_notes_read"]["tool_config"]["description"] == "host description"
        assert tools["llm_notes_read"]["schema"]["function"]["description"] == "host description"

    def test_override_yaml_can_disable_one_tool(self, config_root: Path, use_extensions) -> None:
        """差分の `enabled: false` で 1 つだけ止められる。"""
        override_dir = config_root / "extensions" / "notes" / "tools"
        override_dir.mkdir(parents=True)
        (override_dir / "llm_notes_write.yaml").write_text("enabled: false\n", encoding="utf-8")

        tools = self._load(config_root, use_extensions)

        assert set(tools) == _TOOL_STEMS - {"llm_notes_write"}

    def test_host_yaml_with_bundled_name_fails_fast(
        self, config_root: Path, use_extensions
    ) -> None:
        """ホストの `tools/` に同梱と同じ stem があれば、置き換えずに落とす。"""
        (config_root / "tools" / "llm_notes_read.yaml").write_text(
            "type: llm_notes_read\n", encoding="utf-8"
        )

        with pytest.raises(ValueError, match="same name as a tool bundled by extension 'notes'"):
            self._load(config_root, use_extensions)
