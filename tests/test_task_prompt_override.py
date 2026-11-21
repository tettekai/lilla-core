"""拡張が同梱した LLM の定期タスクのプロンプトをダッシュボードから上書きする機能のテスト（#383）。

- `services/extension_overrides.py` の組み立てと検証
- `handlers/dashboard_extensions.py` の保存 API（試し展開・スケジューラへの反映の有無）
- `handlers/dashboard_server.py` のプレビュー API（`target.type: task`）
- `builtin_tools/task_scheduled_llm.py` の実行時の合成
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from lilla_core.builtin_tools.task_scheduled_llm import ScheduledLlmTask
from lilla_core.handlers import dashboard_extensions, task_handler
from lilla_core.services import extension_overrides as svc
from lilla_core.services.extension_overrides import OverrideValidationError

DEFAULT_PROMPT = "既定 {{ target_date }}"


class _LlmTask:
    """`ScheduledLlmTask` と同じ目印・フックを持つテスト用のタスク。"""

    prompt_override_supported = True

    def __init__(self, schedule: str, variables: list[str], default: str = DEFAULT_PROMPT,
                 sources: str | None = None) -> None:
        self.schedule = schedule
        self._variables = variables
        self._default = default
        self._prompt_sources = sources

    def prompt_variable_names(self) -> list[str]:
        return list(self._variables)

    async def load_default_prompt(self) -> str:
        return self._default


def _entry(instance, extension: str = "memory", file_overrides=frozenset()) -> dict:
    return {
        "instance": instance,
        "scheduled": True,
        "extension": extension,
        "file_overrides": frozenset(file_overrides),
    }


@pytest.fixture
def tools() -> dict:
    """LLM のタスク 1 つと、LLM を使わないタスク 1 つ。"""
    return {
        "task_review": _entry(_LlmTask(
            "30 4 * * *", ["target_date", "remember_entries"],
            sources="file:/pkg/task_prompts/task_review.en.md",
        )),
        "task_plain": _entry(SimpleNamespace(schedule="0 3 * * *")),
    }


@pytest.fixture(autouse=True)
def registered(make_extension, use_extensions, monkeypatch, tmp_path):
    """`memory` を登録し、ロケールを固定する。"""
    use_extensions(make_extension("memory"))
    cfg = SimpleNamespace(
        ui=SimpleNamespace(locale="ja", timezone="Asia/Tokyo"),
        scheduler=SimpleNamespace(enabled=True),
        env=SimpleNamespace(config_root=str(tmp_path)),
    )
    monkeypatch.setattr("lilla_core.core.config.get_config", lambda: cfg)


class TestBuildDetail:
    """設定画面のタスクのプロンプト欄。"""

    def _detail(self, tools, doc, defaults=None):
        return svc.build_detail(
            "memory", tools, doc, "ja", {}, False, True,
            defaults if defaults is not None else {"task_review": DEFAULT_PROMPT},
        )

    def test_llm_task_has_prompt_section(self, tools) -> None:
        """LLM のタスクだけが既定・変数名・言語つきのプロンプト欄を持つ。"""
        detail = self._detail(tools, None)
        review, plain = detail["tasks"]

        assert review["prompt"] == {
            "default": DEFAULT_PROMPT,
            "default_locale": "en",
            "overridden": False,
            "mode": None,
            "text": None,
            "invalid": False,
            "effective": DEFAULT_PROMPT,
            "template_variables": ["target_date", "remember_entries"],
        }
        assert plain["prompt"] is None

    def test_append_override_is_shown(self, tools) -> None:
        """追加の上書きは既定の後ろに空行区切りで足した実効値を見せる。"""
        doc = {"tasks": {"task_review": {"prompt": {"mode": "append", "text": "追加"}}}}

        prompt = self._detail(tools, doc)["tasks"][0]["prompt"]

        assert prompt["overridden"] is True
        assert (prompt["mode"], prompt["text"]) == ("append", "追加")
        assert prompt["effective"] == DEFAULT_PROMPT + "\n\n追加"

    def test_invalid_override_falls_back(self, tools) -> None:
        """形の不正な保存値は `invalid` にして既定を実効値にする。"""
        doc = {"tasks": {"task_review": {"prompt": "旧形式の文字列"}}}

        prompt = self._detail(tools, doc)["tasks"][0]["prompt"]

        assert prompt["invalid"] is True
        assert prompt["effective"] == DEFAULT_PROMPT

    def test_file_set_prompt_is_flagged(self, tools) -> None:
        """上書きファイルの `prompt` は `file_overridden` に挙げる（画面の値は残す）。"""
        tools["task_review"]["file_overrides"] = frozenset({"prompt", "schedule"})
        tools["task_plain"]["file_overrides"] = frozenset({"prompt"})
        doc = {"tasks": {"task_review": {"prompt": {"mode": "replace", "text": "置換"}}}}

        review, plain = self._detail(tools, doc)["tasks"]

        assert review["file_overridden"] == ["schedule", "prompt"]
        assert review["prompt"]["text"] == "置換"
        assert plain["file_overridden"] == []

    async def test_load_task_default_prompts(self, tools) -> None:
        """LLM のタスクだけ既定を読み、読めなければ空文字にする。"""
        assert await svc.load_task_default_prompts("memory", tools) == {
            "task_review": DEFAULT_PROMPT
        }
        tools["task_review"]["instance"].load_default_prompt = AsyncMock(
            side_effect=OSError("gone")
        )
        assert await svc.load_task_default_prompts("memory", tools) == {"task_review": ""}


class TestParseUpdate:
    """タスクのプロンプトの保存リクエストの検証。"""

    def test_prompt_only_does_not_touch_scheduler(self, tools) -> None:
        """プロンプトだけの保存はスケジューラへの反映対象にしない。"""
        update = svc.parse_update(
            "memory",
            {"tasks": {"task_review": {"prompt": {"mode": "append", "text": "追加"}}}},
            tools,
        )

        assert update.set_fields == {
            "tasks.task_review.prompt": {"mode": "append", "text": "追加"}
        }
        assert update.task_names == []
        assert update.task_prompts == {"task_review": "追加"}

    def test_prompt_with_schedule_applies(self, tools) -> None:
        """`schedule` も変えたときは反映対象にする。"""
        update = svc.parse_update(
            "memory",
            {"tasks": {"task_review": {
                "prompt": {"mode": "append", "text": "x"}, "schedule": "0 5 * * *",
            }}},
            tools,
        )

        assert update.task_names == ["task_review"]

    def test_null_unsets(self, tools) -> None:
        """`null` で上書きを消す。"""
        update = svc.parse_update("memory", {"tasks": {"task_review": {"prompt": None}}}, tools)

        assert update.unset_fields == ["tasks.task_review.prompt"]
        assert update.task_names == []

    @pytest.mark.parametrize("prompt, match", [
        ({"mode": "replace", "text": "  \n"}, "must not be empty"),
        ({"mode": "append", "text": "{% if %}"}, "syntax"),
        ({"mode": "nope", "text": "x"}, "mode"),
        ("文字列", "object"),
    ])
    def test_rejects_invalid_prompt(self, tools, prompt, match) -> None:
        """置き換えの空本文・構文エラー・形の不正は保存しない。"""
        with pytest.raises(OverrideValidationError, match=match):
            svc.parse_update("memory", {"tasks": {"task_review": {"prompt": prompt}}}, tools)

    def test_task_without_llm_prompt_is_rejected(self, tools) -> None:
        """LLM を使わないタスクにはプロンプトを保存できない。"""
        with pytest.raises(OverrideValidationError, match="LLM prompt"):
            svc.parse_update(
                "memory",
                {"tasks": {"task_plain": {"prompt": {"mode": "append", "text": "x"}}}},
                tools,
            )

    def test_append_empty_is_allowed(self, tools) -> None:
        """追加の空本文は既定と同じになるだけなので受け付ける。"""
        update = svc.parse_update(
            "memory", {"tasks": {"task_review": {"prompt": {"mode": "append", "text": ""}}}}, tools
        )
        assert update.task_prompts == {"task_review": ""}


@pytest.fixture
def repo(monkeypatch) -> MagicMock:
    """上書きのリポジトリのモック。"""
    repo = MagicMock()
    repo.doc = None
    repo.find_one = AsyncMock(side_effect=lambda name: repo.doc)
    repo.update_overrides = AsyncMock()
    monkeypatch.setattr(
        "lilla_core.repository.extension_override_repository.get_extension_override_repo",
        lambda: repo,
    )
    return repo


@pytest.fixture
def apply(monkeypatch) -> AsyncMock:
    """スケジューラへの反映のモック。"""
    mock = AsyncMock(return_value=task_handler.APPLY_APPLIED)
    monkeypatch.setattr(task_handler, "apply_task_overrides", mock)
    monkeypatch.setattr(task_handler, "get_applied_schedules", lambda: {})
    monkeypatch.setattr(task_handler, "is_scheduler_running", lambda: True)
    return mock


@pytest.fixture
def dashboard_server(monkeypatch):
    """他のテストが `web` をモックへ差し替えたモジュールを残しうるため、読み直して返す。"""
    monkeypatch.delitem(sys.modules, "lilla_core.handlers.dashboard_server", raising=False)
    return importlib.import_module("lilla_core.handlers.dashboard_server")


@pytest.fixture
async def client(repo, apply, tools, dashboard_server):
    """編集 API とプレビュー API を載せたアプリのクライアント。"""
    app = web.Application()
    dashboard_extensions.register_routes(app, tools)
    app.router.add_post("/api/admin/prompt-preview", dashboard_server.handle_api_prompt_preview)
    client = TestClient(TestServer(app))
    await client.start_server()
    yield client
    await client.close()


class TestApi:
    """保存 API とプレビュー API。"""

    async def test_save_prompt_without_apply(self, client, repo, apply) -> None:
        """宣言した変数を使う本文は保存し、スケジューラへは反映しない。"""
        res = await client.patch(
            "/api/admin/extensions/memory/overrides",
            json={"tasks": {"task_review": {
                "prompt": {"mode": "append", "text": "{{ remember_entries }} / {{ notes('x.md') }}"},
            }}},
        )

        assert res.status == 200
        data = await res.json()
        assert data["tasks_apply"] is None
        apply.assert_not_awaited()
        repo.update_overrides.assert_awaited_once()
        assert data["detail"]["tasks"][0]["prompt"]["default"] == DEFAULT_PROMPT

    async def test_undefined_variable_is_400(self, client, repo) -> None:
        """宣言していない名前を使う本文は保存しない。"""
        res = await client.patch(
            "/api/admin/extensions/memory/overrides",
            json={"tasks": {"task_review": {"prompt": {"mode": "replace", "text": "{{ journal_path }}"}}}},
        )

        assert res.status == 400
        assert "journal_path" in (await res.json())["error"]
        repo.update_overrides.assert_not_awaited()

    async def test_preview_uses_dummy_values(self, client, monkeypatch) -> None:
        """プレビューは実データを読まず、ダミー値で展開する。"""
        monkeypatch.setattr(
            "lilla_core.core.extension.get_file_storage",
            MagicMock(side_effect=AssertionError("notes must not be read")),
        )
        res = await client.post("/api/admin/prompt-preview", json={
            "text": "{{ target_date }} {{ notes('memory/core.md') }}",
            "target": {"type": "task", "name": "memory", "task": "task_review"},
        })

        assert res.status == 200
        assert (await res.json())["rendered"] == "[target_date] [notes: memory/core.md]"

    @pytest.mark.parametrize("target", [
        {"type": "task", "name": "memory", "task": "task_plain"},
        {"type": "task", "name": "memory", "task": "missing"},
        {"type": "task", "name": "nope", "task": "task_review"},
    ])
    async def test_preview_unknown_task_is_404(self, client, target) -> None:
        """LLM のタスクでない・無いタスクは 404。"""
        res = await client.post("/api/admin/prompt-preview", json={"text": "x", "target": target})

        assert res.status == 404


@pytest.fixture
def prompt_file(tmp_path: Path) -> Path:
    path = tmp_path / "prompt.md"
    path.write_text("既定 {{ day }}\n", encoding="utf-8")
    return path


def _task(prompt_file: Path, extension: str | None = "memory", override_keys=()) -> ScheduledLlmTask:
    with patch("lilla_core.loaders.llm_tool_loader.get_llm_tools", return_value={}):
        return ScheduledLlmTask(
            {
                "llm_provider": "p",
                "prompt": f"file:{prompt_file}",
                "_extension": extension,
                "_override_keys": list(override_keys),
            },
            "task_x",
        )


class TestRuntimeCompose:
    """実行時の合成（`ScheduledLlmTask._build_prompt`）。"""

    @pytest.fixture
    def override_repo(self, monkeypatch) -> MagicMock:
        repo = MagicMock()
        repo.find_one = AsyncMock(return_value=None)
        monkeypatch.setattr(
            "lilla_core.repository.extension_override_repository.get_extension_override_repo",
            lambda: repo,
        )
        return repo

    def _doc(self, prompt) -> dict:
        return {"tasks": {"task_x": {"prompt": prompt}}}

    async def test_append(self, prompt_file, override_repo) -> None:
        """追加は既定と追加分をそれぞれ展開して空行でつなぐ。"""
        override_repo.find_one.return_value = self._doc({"mode": "append", "text": "追加 {{ day }}"})

        prompt = await _task(prompt_file)._build_prompt({}, {"day": "月"})

        assert prompt == "既定 月\n\n追加 月"
        override_repo.find_one.assert_awaited_once_with("memory")

    async def test_replace(self, prompt_file, override_repo) -> None:
        """置き換えは入力本文だけを使う。"""
        override_repo.find_one.return_value = self._doc({"mode": "replace", "text": "置換 {{ day }}"})

        assert await _task(prompt_file)._build_prompt({}, {"day": "火"}) == "置換 火"

    async def test_follows_default_change(self, prompt_file, override_repo) -> None:
        """追加のとき、既定の本文が変われば既定の部分は追従する。"""
        override_repo.find_one.return_value = self._doc({"mode": "append", "text": "追加"})
        task = _task(prompt_file)
        prompt_file.write_text("新しい既定\n", encoding="utf-8")

        assert await task._build_prompt({}, {}) == "新しい既定\n\n追加"

    @pytest.mark.parametrize("doc", [
        {"tasks": {"task_x": {"prompt": "旧形式"}}},
        {"tasks": "壊れた値"},
        None,
    ])
    async def test_invalid_or_missing_uses_default(self, prompt_file, override_repo, doc) -> None:
        """不正な保存値・上書きなしは既定を使う。"""
        override_repo.find_one.return_value = doc

        assert await _task(prompt_file)._build_prompt({}, {"day": "水"}) == "既定 水"

    async def test_read_failure_uses_default(self, prompt_file, override_repo) -> None:
        """上書きの読み込みに失敗しても既定で動く。"""
        override_repo.find_one.side_effect = RuntimeError("db down")

        assert await _task(prompt_file)._build_prompt({}, {"day": "木"}) == "既定 木"

    async def test_file_prompt_wins(self, prompt_file, override_repo) -> None:
        """上書きファイルが `prompt` を書いていれば、画面の上書きは読まない。"""
        task = _task(prompt_file, override_keys=["prompt"])

        assert await task._build_prompt({}, {"day": "金"}) == "既定 金"
        override_repo.find_one.assert_not_awaited()

    async def test_user_task_is_not_overridden(self, prompt_file, override_repo) -> None:
        """拡張が同梱していないタスクは上書きを読まない。"""
        assert await _task(prompt_file, extension=None)._build_prompt({}, {"day": "土"}) == "既定 土"
        override_repo.find_one.assert_not_awaited()


class TestMemoryVariableNames:
    """memory の夜間タスクが宣言する変数名。"""

    def test_names_match_stems(self) -> None:
        from lilla_core.extensions.memory.tools.task_memory_llm import (
            COMPACTION_TASK,
            REVIEW_TASK,
            MemoryLlmTask,
        )

        review = MemoryLlmTask.__new__(MemoryLlmTask)
        review.name = REVIEW_TASK
        compaction = MemoryLlmTask.__new__(MemoryLlmTask)
        compaction.name = COMPACTION_TASK

        assert review.prompt_variable_names() == ["target_date", "remember_entries"]
        assert compaction.prompt_variable_names() == ["journal_path"]
