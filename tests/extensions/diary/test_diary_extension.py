"""`diary` 拡張が申告する内容のテスト。

日記の設定（`extensions.diary`）・リポジトリ・ツール・同梱のツール YAML・会話向けの
説明（`default_prompt()`）を申告する。設定・登録の検証はコアの `use_extensions()` で
テスト内に閉じ込める。
"""
from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from lilla_core.testing import use_extensions, write_minimal_lilla_yaml

from lilla_core.extensions import diary

_DIARY_DIR = Path(diary.__file__).resolve().parent
_TOOL_NAMES = {"llm_diary_draft_get", "llm_diary_draft_set", "llm_diary_draft_faircopy_get"}
_TASK_NAMES = {"task_hearing_diary", "task_write_diary"}


@pytest.fixture
def diary_config_root(tmp_path: Path) -> Path:
    """`extensions.diary` 以外の拡張の節を持たない config_root を返す。"""
    write_minimal_lilla_yaml(tmp_path)
    return tmp_path


class TestDiaryExtensionDeclaration:
    """`diary` 拡張の申告内容。"""

    def test_name(self) -> None:
        """拡張名は `diary` で、`extension` インスタンスを 1 つ export する。"""
        from lilla_core.core.extension import Extension

        assert diary.extension.name == "diary"
        assert isinstance(diary.extension, Extension)

    def test_no_dependency_on_other_extensions(self) -> None:
        """認可も他拡張も必要としないため `requires` を持たない。"""
        assert diary.extension.requires == ()

    def test_declares_the_diary_section(self) -> None:
        """申告するモデルは `DiaryConfig` で、節名は `extensions.diary`。"""
        from lilla_core.core.config import extension_section_name

        assert diary.extension.config_model() is diary.DiaryConfig
        assert extension_section_name(diary.extension.name) == "diary"

    def test_declares_dashboard_settings(self) -> None:
        """テンプレ本文・日付の切り替わり時刻・清書の保存先は画面管理の設定（`DiarySettings`）で申告する。"""
        assert diary.extension.settings_model() is diary.DiarySettings
        assert set(diary.DiarySettings.model_fields) == {
            "template", "day_rollover", "faircopy_storage", "faircopy_path",
        }

    def test_declares_no_secrets(self) -> None:
        """秘匿情報は持たない。"""
        assert diary.extension.env_fields() == {}

    def test_declares_the_tool_root(self) -> None:
        """ツールの探索先は `tools/` で、3 つのツールがある。"""
        roots = diary.extension.tool_roots()

        assert roots == [_DIARY_DIR / "tools"]
        assert {p.stem for p in roots[0].glob("llm_*.py")} == _TOOL_NAMES

    def test_declares_bundled_tool_configs(self) -> None:
        """同梱のツール YAML のディレクトリに 3 つの LLM ツールと 2 つのタスクの YAML がある。"""
        roots = diary.extension.tool_config_roots()

        assert roots == [_DIARY_DIR / "tool_configs"]
        assert {p.stem for p in roots[0].glob("*.yaml")} == _TOOL_NAMES | _TASK_NAMES

    def test_startup_repos(self) -> None:
        """`diary_drafts` のリポジトリを起動時の初期化対象として申告する。"""
        from lilla_core.extensions.diary.repository import get_diary_draft_repo

        assert diary.extension.startup_repos() == [get_diary_draft_repo]


class TestNotesDirectories:
    """保存先 `notes` で書くトップレベルディレクトリの宣言（`notes_directories()`）。"""

    def test_declares_diary_with_ja_and_en_usage(self) -> None:
        """`diary` だけを、空でない ja / en の用途つきで宣言する。"""
        declared = diary.extension.notes_directories()

        assert set(declared) == {"diary"}
        assert set(declared["diary"]) == {"ja", "en"}
        assert all(isinstance(v, str) and v.strip() for v in declared["diary"].values())

    def test_faircopy_directory_is_declared(self) -> None:
        """清書を書くディレクトリ（`faircopy_path` の起点）を宣言している。"""
        assert diary.FAIRCOPY_DIRECTORY in diary.extension.notes_directories()

    @pytest.mark.parametrize("locale", ["ja", "en"])
    def test_usage_has_no_path_shape(self, locale: str) -> None:
        """用途にはパスやパスの形（`/`・プレースホルダ・拡張子）を書かない。"""
        usage = diary.extension.notes_directories()["diary"][locale]

        assert "/" not in usage
        assert "{" not in usage
        assert ".md" not in usage

    def test_returns_a_copy(self) -> None:
        """戻り値を書き換えても次の呼び出しには影響しない。"""
        diary.extension.notes_directories()["diary"]["ja"] = "x"

        assert diary.extension.notes_directories()["diary"] == diary.NOTES_DIRECTORY_USAGE

    def test_passes_core_validation(self, diary_config_root: Path) -> None:
        """コアの登録時の検証を通り、宣言の一覧に載る。"""
        from lilla_core.core.extension import get_notes_directories

        with use_extensions(diary.extension, config_root=diary_config_root):
            assert get_notes_directories() == {"diary": diary.NOTES_DIRECTORY_USAGE}

    def test_notes_can_write_under_diary_only_when_loaded(self, diary_config_root: Path) -> None:
        """notes と一緒にロードすると `diary/` が書ける場所に加わり、外すと消える。"""
        from lilla_core.extensions.notes import extension as notes_extension
        from lilla_core.extensions.notes.paths import writable_top_level_dirs

        with use_extensions(notes_extension, diary.extension, config_root=diary_config_root):
            with_diary = writable_top_level_dirs()
        with use_extensions(notes_extension, config_root=diary_config_root):
            without_diary = writable_top_level_dirs()

        assert with_diary["diary"] == diary.NOTES_DIRECTORY_USAGE
        assert "user" in with_diary
        assert "diary" not in without_diary
        assert "user" in without_diary

    def test_notes_accepts_default_faircopy_path(self, diary_config_root: Path) -> None:
        """diary をロードした構成では、清書の既定のパスを notes が書き込み可として受け付ける。"""
        from lilla_core.extensions.notes import extension as notes_extension
        from lilla_core.extensions.notes.errors import NotesPathError
        from lilla_core.extensions.notes.paths import check_writable_path

        from datetime import date

        from lilla_core.extensions.diary.service import render_faircopy_path

        path = render_faircopy_path(diary.DEFAULT_FAIRCOPY_PATH, date(2026, 11, 13))
        assert path == "diary/2026/11/2026-11-13.md"
        with use_extensions(notes_extension, diary.extension, config_root=diary_config_root):
            check_writable_path(path)
        with use_extensions(notes_extension, config_root=diary_config_root):
            with pytest.raises(NotesPathError):
                check_writable_path(path)


class TestDefaultPrompt:
    """会話向けの説明（`default_prompt()`）。"""

    def test_has_ja_and_en(self) -> None:
        """ja と en の両方を、空でない文字列で返す。"""
        prompts = diary.extension.default_prompt()

        assert set(prompts) == {"ja", "en"}
        assert all(isinstance(v, str) and v.strip() for v in prompts.values())

    @pytest.mark.parametrize("locale", ["ja", "en"])
    def test_explains_tool_usage(self, locale: str) -> None:
        """ツールの使い方（get してから set・moodscore を推測しない・日付の区切り）を含む。"""
        text = diary.extension.default_prompt()[locale]

        assert "llm_diary_draft_get" in text
        assert "llm_diary_draft_set" in text
        assert "moodscore" in text
        assert "04:00" in text

    @pytest.mark.parametrize("locale", ["ja", "en"])
    def test_refers_to_default_tool_names(self, locale: str) -> None:
        """ツール名は `config` 上の既定名（`llm_` 付き）で書く。"""
        text = diary.extension.default_prompt()[locale]

        assert re.search(r"(?<!llm_)diary_draft_(get|set)", text) is None

    @pytest.mark.parametrize("locale", ["ja", "en"])
    def test_holes_are_not_jinja_syntax(self, locale: str) -> None:
        """穴は `<<名前>>` で書き、Jinja2 の変数の記法は設定の参照にだけ使う。

        コアはこの説明を Jinja2 のテンプレートとして展開するため、穴を `{{` で書くと展開に失敗する。
        """
        text = diary.extension.default_prompt()[locale]

        assert "<<" in text
        assert re.findall(r"\{\{(.*?)\}\}", text) == [" settings.faircopy_path "]

    def test_passes_core_validation(self, diary_config_root: Path) -> None:
        """コアの登録時の検証（形・言語の選択）を通る。"""
        from lilla_core.core.extension import get_default_prompts

        with use_extensions(diary.extension, config_root=diary_config_root):
            assert get_default_prompts() == [("diary", diary.extension.default_prompt())]



def _render(locale: str, **values: object) -> str:
    """会話向けの説明を、コアと同じ Jinja2 の展開で `DiarySettings` の値を入れて返す。"""
    from lilla_core.extensions.diary import DiarySettings
    from lilla_core.services.prompt_template import render_prompt, settings_for_template

    settings = settings_for_template(DiarySettings(**values))
    return render_prompt(diary.extension.default_prompt()[locale], settings)


class TestDefaultPromptRendering:
    """設定から組み立てる清書の保存先の説明。"""

    @pytest.mark.parametrize("locale", ["ja", "en"])
    def test_default_settings_show_faircopy_path(self, locale: str) -> None:
        """既定の設定では、notes の `diary/` + 既定の `faircopy_path` と読み方を載せる。"""
        text = _render(locale)

        assert "diary/{year}/{month}/{date}.md" in text
        assert "llm_notes_read" in text
        assert "YYYY-MM-DD" in text
        assert text.count("llm_diary_draft_get") == 2

    @pytest.mark.parametrize("locale", ["ja", "en"])
    def test_custom_path_follows_settings(self, locale: str) -> None:
        """`faircopy_path` を変えると説明のパスも変わる（先頭の `diary/` も読み替えない）。"""
        assert "diary/journal/{date}.md" in _render(locale, faircopy_path="journal/{date}.md")
        assert "diary/diary/{date}.md" in _render(locale, faircopy_path="diary/{date}.md")

    @pytest.mark.parametrize("locale", ["ja", "en"])
    def test_faircopy_off_hides_location(self, locale: str) -> None:
        """清書がオフなら保存先の説明を出さず、下書きの説明だけを残す（末尾に空行も残さない）。"""
        text = _render(locale, faircopy_storage=[])

        assert "llm_notes_read" not in text
        assert "llm_diary_draft_set" in text
        assert text.count("llm_diary_draft_get") == 1
        assert text == text.rstrip()

    @pytest.mark.parametrize("locale", ["ja", "en"])
    @pytest.mark.parametrize("storage", [["notes"], []])
    def test_rendered_without_template_syntax(self, locale: str, storage: list[str]) -> None:
        """展開後の本文に Jinja2 の記法が残らない。"""
        text = _render(locale, faircopy_storage=storage)

        assert not any(token in text for token in ("{{", "{%", "%}", "}}"))

def _write_diary_yaml(config_root: Path, body: str) -> None:
    """最小構成の lilla.yaml に `extensions.diary` の節を書き足す。"""
    write_minimal_lilla_yaml(config_root)
    text = (config_root / "lilla.yaml").read_text(encoding="utf-8")
    (config_root / "lilla.yaml").write_text(
        text + "extensions:\n  diary:\n" + body, encoding="utf-8"
    )


class TestComposition:
    """`extensions.diary` の合成結果と、画面管理の設定の登録。"""

    def test_section_may_be_omitted(self, diary_config_root: Path) -> None:
        """節を省略しても起動でき、画面管理の設定のモデルが登録される。"""
        from lilla_core.core.extension import get_settings_models

        with use_extensions(diary.extension, config_root=diary_config_root) as cfg:
            assert cfg.extensions.diary.model_dump() == {}
            assert get_settings_models() == {"diary": diary.DiarySettings}

    @pytest.mark.parametrize(
        "body, key",
        [
            ("    template: file:/x/t.md\n", "template"),
            ("    day_rollover: 05:30\n", "day_rollover"),
        ],
    )
    def test_moved_items_in_yaml_fail_with_migration_target(
        self, tmp_path: Path, body: str, key: str
    ) -> None:
        """YAML に残った template / day_rollover は、移行先（画面）を示して起動時に落とす。"""
        from pydantic import ValidationError

        _write_diary_yaml(tmp_path, body)
        with pytest.raises(ValidationError, match=rf"dashboard.*extensions\.diary\.{key}"):
            with use_extensions(diary.extension, config_root=tmp_path):
                pass



class TestDashboardSave:
    """コアの汎用の保存（ダッシュボードの PUT と同じ検証）を通した日記の設定。"""

    @pytest.fixture
    def settings_repo(self, monkeypatch):
        """設定のリポジトリのモック。"""
        from unittest.mock import AsyncMock

        repo = MagicMock()
        repo.save = AsyncMock()
        monkeypatch.setattr(
            "lilla_core.repository.extension_settings_repository.get_extension_settings_repo",
            lambda: repo,
        )
        return repo

    async def test_saves_body_and_normalized_rollover(self, diary_config_root, settings_repo) -> None:
        """本文はそのまま、時刻は HH:MM に正規化して 1 回で保存する。"""
        from lilla_core.services.extension_settings import save_settings

        with use_extensions(diary.extension, config_root=diary_config_root):
            await save_settings("diary", {"template": "# <<date>>\n", "day_rollover": "5:0"})

        settings_repo.save.assert_awaited_once_with(
            "diary",
            {
                "template": "# <<date>>\n",
                "day_rollover": "05:00",
                "faircopy_storage": ["notes"],
                "faircopy_path": "{year}/{month}/{date}.md",
            },
        )

    async def test_explicit_unset(self, diary_config_root, settings_repo) -> None:
        """未設定（None）への解除は保存でき、空本文の保存とは区別される。"""
        from lilla_core.services.extension_settings import save_settings

        with use_extensions(diary.extension, config_root=diary_config_root):
            await save_settings("diary", {"template": None})

        assert settings_repo.save.await_args.args[1]["template"] is None

    @pytest.mark.parametrize(
        "values, field",
        [
            ({"template": ""}, "template"),
            ({"template": "  "}, "template"),
            ({"template": 3}, "template"),
            ({"template": "<< bad name >>"}, "template"),
            ({"day_rollover": "25:00"}, "day_rollover"),
            ({"day_rollover": 5}, "day_rollover"),
            ({"template": "ok", "day_rollover": "x"}, "day_rollover"),
            ({"template_path": "file:/x"}, "template_path"),
        ],
    )
    async def test_invalid_values_save_nothing(
        self, diary_config_root, settings_repo, values, field
    ) -> None:
        """不正な一式は項目ごとの理由つきで拒否し、一部だけ保存することはない。"""
        from lilla_core.services.extension_settings import SettingsValidationError, save_settings

        with use_extensions(diary.extension, config_root=diary_config_root):
            with pytest.raises(SettingsValidationError) as excinfo:
                await save_settings("diary", values)

        assert field in [e["field"] for e in excinfo.value.errors]
        settings_repo.save.assert_not_awaited()

    async def test_save_failure_propagates(self, diary_config_root, settings_repo) -> None:
        """DB への保存の失敗は成功扱いにしない。"""
        from lilla_core.services.extension_settings import save_settings

        settings_repo.save.side_effect = RuntimeError("db down")
        with use_extensions(diary.extension, config_root=diary_config_root):
            with pytest.raises(RuntimeError):
                await save_settings("diary", {"template": "x"})


class TestBundledToolConfigs:
    """同梱のツール YAML のロード。"""

    def _load(self, config_root: Path) -> dict:
        """`diary` 拡張を登録した状態で LLM ツールをロードする。"""
        from lilla_core.loaders.llm_tool_loader import load_llm_tools

        with use_extensions(diary.extension, config_root=config_root):
            return load_llm_tools(
                tool_roots=diary.extension.tool_roots(), config_root=config_root
            )

    def test_tools_load_without_user_yaml(self, diary_config_root: Path) -> None:
        """`${CONFIG_ROOT}/tools` に YAML が無くても 3 つのツールがロードされる。"""
        tools = self._load(diary_config_root)

        assert set(tools) == _TOOL_NAMES
        assert tools["llm_diary_draft_faircopy_get"]["supported_client_type"] == "task"
        assert tools["llm_diary_draft_get"]["supported_client_type"] == "all"
        assert tools["llm_diary_draft_set"]["supported_client_type"] == "all"

    def test_user_yaml_replaces_bundled_one(self, diary_config_root: Path) -> None:
        """同じ stem の YAML を `${CONFIG_ROOT}/tools` に置くと同梱分が置き換わる。"""
        tools_dir = diary_config_root / "tools"
        tools_dir.mkdir()
        (tools_dir / "llm_diary_draft_get.yaml").write_text(
            "type: llm_diary_draft_get\nsupported_client_type: task\n", encoding="utf-8"
        )
        (tools_dir / "llm_diary_draft_set.yaml").write_text(
            "type: llm_diary_draft_set\nenabled: false\n", encoding="utf-8"
        )

        tools = self._load(diary_config_root)

        assert tools["llm_diary_draft_get"]["supported_client_type"] == "task"
        assert "llm_diary_draft_set" not in tools


class TestSetup:
    """起動時の処理。"""

    def test_does_not_check_template_at_startup(self) -> None:
        """テンプレはファイルではなく画面の設定なので、起動時の読み取り検査は持たない。"""
        from lilla_core.core.extension import Extension

        assert type(diary.extension).setup is Extension.setup
