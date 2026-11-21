"""日記の聞き取りタスク（`diary.tools.task_diary_llm`）と同梱タスクのロードのテスト。"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from lilla_core.testing import use_extensions, write_minimal_lilla_yaml

from lilla_core.extensions import diary
from lilla_core.extensions.diary.tools import task_diary_llm
from lilla_core.extensions.diary.tools.task_diary_llm import DiaryLlmTask

_TZ = ZoneInfo("Asia/Tokyo")
_NOW = datetime(2026, 9, 28, 22, 0, tzinfo=_TZ)
_PROMPT_DIR = Path(diary.__file__).resolve().parent / "task_prompts"

#: タスクの初期化で `available_tools` を展開する対象のロード済み LLM ツール。
_LLM_TOOLS = {
    "llm_diary_draft_get": {"schema": {}, "supported_client_type": "all"},
    "llm_diary_draft_set": {"schema": {}, "supported_client_type": "all"},
    "llm_diary_draft_faircopy_get": {"schema": {}, "supported_client_type": "task"},
    "llm_writer": {"schema": {}, "supported_client_type": "all"},
}


@pytest.fixture(autouse=True)
def loaded_llm_tools():
    """タスクの初期化が参照するロード済み LLM ツールを差し替える。"""
    with patch(
        "lilla_core.loaders.llm_tool_loader.get_llm_tools", return_value=_LLM_TOOLS
    ):
        yield


def _cfg(locale: str = "ja", main: list[str] | None = None) -> SimpleNamespace:
    """タスクが読む設定（`llm.default` / `ui.locale` / `discord` / `tools.sets`）を返す。"""
    sets = {"main": main} if main is not None else {}
    return SimpleNamespace(
        llm=SimpleNamespace(default="default-llm"),
        ui=SimpleNamespace(locale=locale, timezone="Asia/Tokyo"),
        discord=SimpleNamespace(my_user_id="42"),
        tools=SimpleNamespace(sets=sets),
        env=SimpleNamespace(config_root="/nonexistent"),
    )


@pytest.fixture
def cfg() -> SimpleNamespace:
    """既定の設定（ja・`main` セットは llm_diary_draft_get / set）。"""
    return _cfg(main=["llm_diary_draft_get", "llm_diary_draft_set"])


def _patch_config(cfg):
    """`get_config()`（コアの設定と、`tools.sets` を読むツールローダー）を差し替える。"""
    return patch.multiple(
        "lilla_core.core.config", get_config=MagicMock(return_value=cfg)
    ), patch("lilla_core.loaders.llm_tool_loader.get_config", return_value=cfg)


@pytest.fixture
def with_cfg(cfg):
    """`get_config()` を差し替える。"""
    core_patch, loader_patch = _patch_config(cfg)
    with core_patch, loader_patch:
        yield cfg


class TestDefaults:
    """YAML で省略した項目の既定値。"""

    def test_schedule(self, with_cfg) -> None:
        """`schedule` は stem ごとの既定値になる。"""
        assert DiaryLlmTask({"type": "task_diary_llm"}, "task_hearing_diary").schedule == "0 22 * * *"

    def test_llm_provider_falls_back_to_llm_default(self, with_cfg) -> None:
        """`llm_provider` を省略すると `llm.default` を使う。"""
        task = DiaryLlmTask({"type": "task_diary_llm"}, "task_hearing_diary")

        assert task._llm_provider == "default-llm"

    def test_target(self, with_cfg) -> None:
        """`target` を省略すると `discord.my_user_id` への DM になる。"""
        task = DiaryLlmTask({"type": "task_diary_llm"}, "task_hearing_diary")

        assert task._resolve_target() == "dm:42"

    def test_hearing_available_tools(self, with_cfg) -> None:
        """聞き取りの既定は `$main`。"""
        task = DiaryLlmTask({"type": "task_diary_llm"}, "task_hearing_diary")

        assert task._allowed_tool_names == ["llm_diary_draft_get", "llm_diary_draft_set"]

    def test_unknown_stem_fails(self, with_cfg) -> None:
        """日記の既定タスク以外の stem では使えない。"""
        with pytest.raises(ValueError, match="task_other"):
            DiaryLlmTask({"type": "task_diary_llm"}, "task_other")

    def test_write_diary_is_not_an_llm_task(self, with_cfg) -> None:
        """清書（`task_write_diary`）はこのクラスでは動かない（`task_diary_faircopy` の担当）。"""
        with pytest.raises(ValueError, match="not a diary task"):
            DiaryLlmTask(
                {"type": "task_diary_llm", "prompt": "file:/x.md"}, "task_write_diary"
            )


class TestOverrides:
    """YAML で指定した値が既定値より優先される。"""

    def test_yaml_values_win(self, with_cfg) -> None:
        """`schedule` / `llm_provider` / `target` / `available_tools` は指定値を使う。"""
        task = DiaryLlmTask(
            {
                "type": "task_diary_llm",
                "schedule": "15 21 * * *",
                "llm_provider": "grok",
                "target": "channel:99",
                "available_tools": ["$main", "llm_diary_draft_faircopy_get", "llm_writer"],
                "max_tool_call_iterations": 7,
            },
            "task_hearing_diary",
        )

        assert task.schedule == "15 21 * * *"
        assert task._llm_provider == "grok"
        assert task._resolve_target() == "channel:99"
        assert task._allowed_tool_names[-1] == "llm_writer"
        assert task._max_tool_call_iterations == 7

    @pytest.mark.parametrize("available_tools", [None, ["llm_missing"], ["$unknown"]])
    def test_invalid_available_tools_fail_on_init(self, with_cfg, available_tools) -> None:
        """展開できない `available_tools` はロード時に落ち、メッセージに stem が出る。"""
        with pytest.raises(ValueError, match="task_hearing_diary.*available_tools"):
            DiaryLlmTask(
                {"type": "task_diary_llm", "available_tools": available_tools},
                "task_hearing_diary",
            )

    def test_default_available_tools_fail_when_not_loaded(self, with_cfg) -> None:
        """既定の `available_tools`（`$main`）が展開できないときも落ちる。"""
        core_patch, loader_patch = _patch_config(_cfg(main=["llm_missing"]))
        with core_patch, loader_patch:
            with pytest.raises(ValueError, match="task_hearing_diary.*available_tools"):
                DiaryLlmTask({"type": "task_diary_llm"}, "task_hearing_diary")


class TestPrompt:
    """プロンプトの選択と読み込み。"""

    @pytest.mark.parametrize(
        ("locale", "lang"), [("ja", "ja"), ("en", "en"), ("fr", "ja"), (None, "ja")]
    )
    @pytest.mark.parametrize("name", ["task_hearing_diary"])
    def test_selects_by_ui_locale(self, name: str, locale, lang: str) -> None:
        """`ui.locale` に一致する言語、無ければ `ja` の同梱プロンプトを選ぶ。"""
        core_patch, loader_patch = _patch_config(_cfg(locale, main=[]))
        with core_patch, loader_patch:
            task = DiaryLlmTask({"type": "task_diary_llm"}, name)

        assert task._prompt_sources == f"file:{_PROMPT_DIR / f'{name}.{lang}.md'}"

    def test_falls_back_to_en_when_ja_is_missing(self, tmp_path: Path) -> None:
        """`ja` も無ければ `en` を選ぶ。"""
        (tmp_path / "task_hearing_diary.en.md").write_text("en", encoding="utf-8")
        with patch.object(task_diary_llm, "_PROMPT_DIR", tmp_path):
            path = task_diary_llm.select_default_prompt_path("task_hearing_diary", "fr")

        assert path == tmp_path / "task_hearing_diary.en.md"

    @pytest.mark.parametrize("lang", ["ja", "en"])
    def test_bundled_prompts_use_now_and_diary_tools(self, lang: str) -> None:
        """同梱プロンプトは `{{ current_hour }}` と日記のツールを使い、特定の書き出し先に依存しない。"""
        text = (_PROMPT_DIR / f"task_hearing_diary.{lang}.md").read_text(encoding="utf-8")

        assert "{{ current_hour }}" in text
        assert "{{now}}" not in text
        assert "04:00" in text
        assert "obsidian" not in text.lower()
        assert "llm_diary_draft_get" in text

    def test_write_diary_has_no_bundled_prompt(self) -> None:
        """清書は LLM を通さないため、既定プロンプトを同梱しない。"""
        assert not list(_PROMPT_DIR.glob("task_write_diary.*"))

    async def test_prompt_spec_replaces_default(self, with_cfg, tmp_path: Path) -> None:
        """`prompt` に source spec を書くと、同梱の既定の代わりに使い `{{ current_hour }}` を展開する。"""
        prompt = tmp_path / "p.md"
        prompt.write_text("now={{ current_hour }}", encoding="utf-8")
        task = DiaryLlmTask(
            {"type": "task_diary_llm", "prompt": f"file:{prompt}"}, "task_hearing_diary"
        )

        with patch(
            "lilla_core.utils.datetime_utils.local_timezone", return_value=_TZ
        ):
            assert await task._build_prompt({"now": _NOW}) == "now=2026-09-28 22:xx"

    async def test_default_prompt_renders_current_hour(self, with_cfg) -> None:
        """同梱の既定プロンプトも `{{ current_hour }}` を展開する。"""
        task = DiaryLlmTask({"type": "task_diary_llm"}, "task_hearing_diary")

        with patch(
            "lilla_core.utils.datetime_utils.local_timezone", return_value=_TZ
        ):
            text = await task._build_prompt({"now": _NOW})

        assert "2026-09-28 22:xx" in text
        assert "{{" not in text


class TestExecute:
    """実行時の挙動は `task_scheduled_llm` と同じ。"""

    @pytest.fixture
    def wired(self, cfg):
        """実行時に import する依存を差し替える。

        差し替え先のモジュールは import 時に設定を読むものがあるため、本物の設定のまま
        先に差し替えてから `get_config()` を差し替える。
        """
        run = AsyncMock(return_value="今日はどんな一日でしたか？")
        send = AsyncMock()
        repo = MagicMock()
        with patch("lilla_core.services.conversation_service.run_conversation", run), patch(
            "lilla_core.services.message_util.send_to_discord", send
        ), patch(
            "lilla_core.repository.conversation_repository.get_conversation_repo",
            return_value=repo,
        ), patch(
            "lilla_core.utils.datetime_utils.local_timezone", return_value=_TZ
        ):
            core_patch, loader_patch = _patch_config(cfg)
            with core_patch, loader_patch:
                yield SimpleNamespace(run=run, send=send, repo=repo)

    async def test_sends_with_defaults(self, wired) -> None:
        """既定値（`llm.default`・DM・`task` の会話）で 1 往復して通知する。"""
        client = MagicMock()
        task = DiaryLlmTask({"type": "task_diary_llm"}, "task_hearing_diary")

        await task.execute({"discord_client": client, "now": _NOW, "llm_tools": {}})

        kwargs = wired.run.await_args.kwargs
        assert kwargs["client_type"] == "task"
        assert kwargs["llm_name"] == "default-llm"
        assert "2026-09-28 22:xx" in kwargs["inject_user_content"]
        wired.send.assert_awaited_once_with(
            client, "dm:42", "今日はどんな一日でしたか？", wired.repo
        )

    async def test_no_notification_is_not_sent(self, wired) -> None:
        """`NO_NOTIFICATION` なら送らない。"""
        wired.run.return_value = "NO_NOTIFICATION"
        task = DiaryLlmTask({"type": "task_diary_llm"}, "task_hearing_diary")

        await task.execute({"discord_client": MagicMock(), "now": _NOW})

        wired.send.assert_not_awaited()


class TestLoading:
    """同梱 YAML からのロード。"""

    @pytest.fixture
    def config_root(self, tmp_path: Path) -> Path:
        """拡張の節を持たない最小構成の config_root。"""
        write_minimal_lilla_yaml(tmp_path)
        return tmp_path

    def _load(self, config_root: Path) -> dict:
        """`diary` 拡張を登録した状態でタスクツールをロードする。"""
        from lilla_core.loaders.task_tool_loader import load_all_tools

        with use_extensions(diary.extension, config_root=config_root):
            tools = load_all_tools(
                tool_roots=diary.extension.tool_roots(),
                config_root=config_root,
                class_map={},
            )
        return {k: v for k, v in tools.items() if k.startswith("task_")}

    def test_bundled_tasks_load_as_diary_tasks(self, config_root: Path) -> None:
        """YAML が無くても 2 つのタスクが `diary` 拡張のタスクとしてスケジュール付きでロードされる。"""
        tools = self._load(config_root)

        assert set(tools) == {"task_hearing_diary", "task_write_diary"}
        for entry in tools.values():
            assert entry["extension"] == "diary"
            assert entry["scheduled"] is True
        # ツールローダーはファイルから別モジュールとして読むため、クラス名で確かめる
        assert type(tools["task_hearing_diary"]["instance"]).__name__ == "DiaryLlmTask"
        assert type(tools["task_write_diary"]["instance"]).__name__ == "DiaryFaircopyTask"
        assert tools["task_hearing_diary"]["instance"].schedule == "0 22 * * *"
        assert tools["task_write_diary"]["instance"].schedule == "30 3 * * *"

    def test_user_yaml_replaces_bundled_one(self, config_root: Path) -> None:
        """同じ stem の YAML で置き換えても `diary` 拡張のタスクのままで、省略項目は既定値で埋まる。"""
        tools_dir = config_root / "tools"
        tools_dir.mkdir()
        (tools_dir / "task_hearing_diary.yaml").write_text(
            "type: task_diary_llm\n"
            'schedule: "0 5 * * *"\n'
            "available_tools:\n"
            "  - $main\n"
            "  - llm_diary_draft_faircopy_get\n"
            "  - llm_writer\n",
            encoding="utf-8",
        )
        (tools_dir / "task_write_diary.yaml").write_text(
            "type: task_diary_faircopy\n" 'schedule: "15 4 * * *"\n', encoding="utf-8"
        )

        tools = self._load(config_root)

        task = tools["task_hearing_diary"]["instance"]
        assert tools["task_hearing_diary"]["extension"] == "diary"
        assert task.schedule == "0 5 * * *"
        assert task._llm_provider == "dummy"
        assert task._prompt_sources.endswith("task_hearing_diary.ja.md")
        assert task._allowed_tool_names == ["llm_diary_draft_faircopy_get", "llm_writer"]
        assert tools["task_write_diary"]["extension"] == "diary"
        assert tools["task_write_diary"]["instance"].schedule == "15 4 * * *"

    def test_user_yaml_can_disable(self, config_root: Path) -> None:
        """同じ stem の YAML に `enabled: false` を書くとロードされない。"""
        tools_dir = config_root / "tools"
        tools_dir.mkdir()
        (tools_dir / "task_hearing_diary.yaml").write_text(
            "type: task_diary_llm\nenabled: false\n", encoding="utf-8"
        )

        assert set(self._load(config_root)) == {"task_write_diary"}


class TestNoWriterDependency:
    """拡張のコードは特定の書き出し先に依存しない。"""

    def test_no_obsidian_reference(self) -> None:
        """`lilla_core/extensions/diary/` のコード・YAML・プロンプト（会話向けの説明を含む）に Obsidian の参照が無い。"""
        root = Path(diary.__file__).resolve().parent
        files = [
            p for p in root.rglob("*")
            if p.is_file() and p.suffix in {".py", ".yaml", ".md"}
        ]

        assert files
        for path in files:
            assert "obsidian" not in path.read_text(encoding="utf-8").lower(), path
