"""長期記憶の夜間タスク（`memory.tools.task_memory_llm`）のテスト。"""
from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from lilla_core.core.file_storage import FileStorageExistsError
from lilla_core.extensions.memory import CORE_PATH, PERSONAL_README_PATH, MemorySettings
from lilla_core.extensions.memory.service import RememberEntry
from lilla_core.extensions.memory.tools import task_memory_llm
from lilla_core.extensions.memory.tools.task_memory_llm import (
    COMPACTION_TASK,
    NOTES_TOOLS,
    REVIEW_TASK,
    MemoryLlmTask,
)

_TZ = ZoneInfo("Asia/Tokyo")
_NOW = datetime(2026, 9, 28, 4, 30, tzinfo=_TZ)

_LLM_TOOLS = {stem: {"schema": {}, "supported_client_type": "all"} for stem in NOTES_TOOLS}


@pytest.fixture(autouse=True)
def loaded_llm_tools():
    """タスクの初期化が参照するロード済み LLM ツールを差し替える。"""
    with patch("lilla_core.loaders.llm_tool_loader.get_llm_tools", return_value=_LLM_TOOLS):
        yield


@pytest.fixture
def cfg():
    """タスクが読む設定（`llm.default` / `ui` / `discord` / `tools.sets`）を差し替える。"""
    value = SimpleNamespace(
        llm=SimpleNamespace(default="default-llm"),
        ui=SimpleNamespace(locale="en", timezone="Asia/Tokyo"),
        discord=SimpleNamespace(my_user_id="42"),
        tools=SimpleNamespace(sets={"main": []}),
        env=SimpleNamespace(config_root="/nonexistent"),
    )
    with patch("lilla_core.core.config.get_config", return_value=value), patch(
        "lilla_core.loaders.llm_tool_loader.get_config", return_value=value
    ):
        yield value


@pytest.fixture
def storage(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """保存先 `notes` のモック（`exists` / `write`）。"""
    mock = MagicMock()
    mock.exists = AsyncMock(return_value=True)
    mock.write = AsyncMock()
    monkeypatch.setattr(task_memory_llm, "_notes_storage", lambda: mock)
    return mock


@pytest.fixture
def entries(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """remember の読み取りと設定の読み取りを差し替え、読み取りのモックを返す。"""
    from lilla_core.extensions.memory import service

    mock = AsyncMock(return_value=[RememberEntry("青が好き", "t1")])
    monkeypatch.setattr(service, "get_entries", mock)
    monkeypatch.setattr(
        service, "load_memory_settings", AsyncMock(return_value=MemorySettings())
    )
    monkeypatch.setattr(service, "local_timezone", lambda: _TZ)
    return mock


def _task(name: str, **config) -> MemoryLlmTask:
    """YAML の `type` だけ（と上書き）でタスクを組み立てる。"""
    return MemoryLlmTask({"type": "task_memory_llm", **config}, name)


class TestDefaults:
    """YAML で省略した項目の既定値。"""

    def test_review_defaults(self, cfg) -> None:
        """毎日の整理の既定値。"""
        task = _task(REVIEW_TASK)
        assert task.schedule == "30 4 * * *"
        assert task._llm_provider == "default-llm"
        assert task._resolve_target() == "dm:42"
        assert task._allowed_tool_names == NOTES_TOOLS
        assert task._max_tool_call_iterations == 20
        assert task._prompt_sources.endswith("task_memory_review.en.md")

    def test_compaction_defaults(self, cfg) -> None:
        """週次の整理の既定値。"""
        assert _task(COMPACTION_TASK).schedule == "30 3 * * mon"

    def test_yaml_values_win(self, cfg) -> None:
        """YAML に書いた値はそのまま使う。"""
        task = _task(REVIEW_TASK, schedule="0 5 * * *", llm_provider="night", available_tools=[])
        assert task.schedule == "0 5 * * *"
        assert task._llm_provider == "night"
        assert task._allowed_tool_names == []

    def test_unknown_stem(self, cfg) -> None:
        """長期記憶のタスク以外の stem は落とす。"""
        with pytest.raises(ValueError, match="not a memory task"):
            _task("task_other")

    def test_prompt_falls_back_to_ja(self, cfg) -> None:
        """同梱の無い言語なら ja を使う。"""
        cfg.ui.locale = "fr"
        assert _task(REVIEW_TASK)._prompt_sources.endswith("task_memory_review.ja.md")


class TestReviewVariables:
    """毎日の整理の変数。"""

    async def test_target_date_and_entries(self, cfg, storage, entries) -> None:
        """直前に締まった日の記録を、タグで囲んで渡す。"""
        variables = await _task(REVIEW_TASK)._template_variables({"now": _NOW})

        assert variables["target_date"] == "2026-09-27"
        assert variables["remember_entries"].startswith("<remember_entries>")
        assert "青が好き" in variables["remember_entries"]
        entries.assert_awaited_once_with(date(2026, 9, 27))

    async def test_keys_match_declared_names(self, cfg, storage, entries) -> None:
        """実行時に渡す変数のキーが、画面のプレビュー・保存時の検査に使う宣言と一致する。"""
        task = _task(REVIEW_TASK)
        variables = await task._template_variables({"now": _NOW})

        assert sorted(variables) == sorted(task.prompt_variable_names())

    async def test_no_entries_skips(self, cfg, storage, entries) -> None:
        """記録が無い日は None（LLM を呼ばない）で、ひな形も書かない。"""
        entries.return_value = []
        assert await _task(REVIEW_TASK)._template_variables({"now": _NOW}) is None
        storage.write.assert_not_awaited()

    async def test_creates_personal_readme_once(self, cfg, storage, entries) -> None:
        """README が無ければ同梱のひな形を create で書く。"""
        storage.exists.return_value = False
        await _task(REVIEW_TASK)._template_variables({"now": _NOW})

        path, text = storage.write.await_args.args
        assert path == PERSONAL_README_PATH
        assert text.startswith("# Index of memory/personal")
        assert storage.write.await_args.kwargs == {"mode": "create"}

    async def test_existing_readme_is_kept(self, cfg, storage, entries) -> None:
        """README があれば書かない。同時に作られていた場合も失敗にしない。"""
        await _task(REVIEW_TASK)._template_variables({"now": _NOW})
        storage.write.assert_not_awaited()

        storage.exists.return_value = False
        storage.write.side_effect = FileStorageExistsError("exists")
        assert await _task(REVIEW_TASK)._template_variables({"now": _NOW}) is not None


class TestCompactionVariables:
    """週次の整理の変数。"""

    async def test_journal_path(self, cfg, storage, monkeypatch) -> None:
        """実行した月のアーカイブのパスを渡す。"""
        from lilla_core.extensions.memory import service

        monkeypatch.setattr(service, "local_timezone", lambda: _TZ)
        variables = await _task(COMPACTION_TASK)._template_variables({"now": _NOW})
        assert variables == {"journal_path": "memory/journal/202609.md"}
        storage.exists.assert_awaited_once_with(CORE_PATH)

    async def test_keys_match_declared_names(self, cfg, storage, monkeypatch) -> None:
        """実行時に渡す変数のキーが、画面のプレビュー・保存時の検査に使う宣言と一致する。"""
        from lilla_core.extensions.memory import service

        monkeypatch.setattr(service, "local_timezone", lambda: _TZ)
        task = _task(COMPACTION_TASK)
        variables = await task._template_variables({"now": _NOW})

        assert sorted(variables) == sorted(task.prompt_variable_names())

    async def test_no_core_skips(self, cfg, storage) -> None:
        """core.md がまだ無ければ None。"""
        storage.exists.return_value = False
        assert await _task(COMPACTION_TASK)._template_variables({"now": _NOW}) is None


class TestBundledPrompts:
    """同梱のプロンプト。"""

    @pytest.mark.parametrize("stem", [REVIEW_TASK, COMPACTION_TASK])
    @pytest.mark.parametrize("lang", ["ja", "en"])
    async def test_prompts_render_with_the_variables(self, stem, lang) -> None:
        """同梱のプロンプトは、タスクが渡す変数で展開できる。"""
        from lilla_core.services.prompt_template import render_template

        text = task_memory_llm.select_default_prompt_path(stem, lang).read_text(encoding="utf-8")
        variables = (
            {"target_date": "2026-09-27", "remember_entries": "<remember_entries>\n</remember_entries>"}
            if stem == REVIEW_TASK else {"journal_path": "memory/journal/202609.md"}
        )
        rendered = await render_template(text, variables, now=_NOW)
        assert "NO_NOTIFICATION" in rendered
