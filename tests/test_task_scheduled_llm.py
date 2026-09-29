"""builtin_tools/task_scheduled_llm.py のテスト。"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from lilla_core.builtin_tools.task_scheduled_llm import ScheduledLlmTask

_TZ = ZoneInfo("Asia/Tokyo")
#: 2026-09-28 21:30 (+09:00) 実行。
_NOW = datetime(2026, 9, 28, 21, 30, tzinfo=_TZ)


@pytest.fixture
def prompt_file(tmp_path: Path) -> Path:
    """`{{now}}` を含むプロンプトファイルを作る。"""
    path = tmp_path / "prompt.md"
    path.write_text("Now is {{now}}. Anything to report?", encoding="utf-8")
    return path


#: タスクの初期化で `available_tools` を展開する対象のロード済み LLM ツール。
_LLM_TOOLS = {
    "llm_weather": {"schema": {"name": "weather"}, "supported_client_type": "all"},
    "llm_diary": {"schema": {"name": "diary"}, "supported_client_type": "task"},
}


@pytest.fixture(autouse=True)
def loaded_llm_tools():
    """タスクの初期化が参照するロード済み LLM ツールを差し替える。"""
    with patch(
        "lilla_core.loaders.llm_tool_loader.get_llm_tools", return_value=_LLM_TOOLS
    ):
        yield _LLM_TOOLS


@pytest.fixture
def mock_cfg() -> MagicMock:
    """`discord.my_user_id` を持つ AppConfig モック。"""
    cfg = MagicMock()
    cfg.discord.my_user_id = "42"
    return cfg


@pytest.fixture
def mock_repo() -> MagicMock:
    repo = MagicMock()
    repo.save = AsyncMock(return_value="id")
    return repo


@pytest.fixture
def mock_run_conversation() -> AsyncMock:
    return AsyncMock(return_value="今日はごみの日だよ")


@pytest.fixture
def mock_send_to_discord() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def wired(mock_cfg, mock_repo, mock_run_conversation, mock_send_to_discord):
    """タスクが実行時に import する依存をまとめて差し替える。"""
    with patch("lilla_core.core.config.get_config", return_value=mock_cfg), patch(
        "lilla_core.repository.conversation_repository.get_conversation_repo",
        return_value=mock_repo,
    ), patch(
        "lilla_core.services.conversation_service.run_conversation",
        mock_run_conversation,
    ), patch(
        "lilla_core.services.message_util.send_to_discord", mock_send_to_discord
    ), patch(
        "lilla_core.builtin_tools.task_scheduled_llm.local_timezone", return_value=_TZ
    ):
        yield


def _make_task(prompt_file: Path, **overrides) -> ScheduledLlmTask:
    """既定の YAML 設定でタスクを組み立てる。"""
    config = {
        "type": "lilla_core.builtin_tools.task_scheduled_llm",
        "schedule": "*/30 * * * *",
        "target": "dm:{DISCORD_MY_USER_ID}",
        "llm_provider": "reminder",
        "prompt": f"file:{prompt_file}",
        "available_tools": [],
    }
    config.update(overrides)
    return ScheduledLlmTask(config, "task_scheduled_llm")


class TestScheduledLlmTask:
    def test_requires_llm_provider(self, prompt_file: Path) -> None:
        """`llm_provider` が無ければ初期化時に落ちる。"""
        with pytest.raises(ValueError, match="llm_provider"):
            _make_task(prompt_file, llm_provider=None)

    def test_requires_prompt(self, prompt_file: Path) -> None:
        """`prompt` が無ければ初期化時に落ちる。"""
        with pytest.raises(ValueError, match="prompt"):
            _make_task(prompt_file, prompt=None)

    def test_requires_available_tools(self, prompt_file: Path) -> None:
        """`available_tools` のキーが無ければ初期化時に落ちる（継承しない）。"""
        config = {
            "type": "lilla_core.builtin_tools.task_scheduled_llm",
            "llm_provider": "reminder",
            "prompt": f"file:{prompt_file}",
        }
        with pytest.raises(ValueError, match="available_tools"):
            ScheduledLlmTask(config, "task_scheduled_llm")

    @pytest.mark.parametrize(
        "available_tools", [None, "llm_weather", ["llm_missing"], ["$unknown"]]
    )
    def test_invalid_available_tools_fail_on_init(
        self, prompt_file: Path, available_tools
    ) -> None:
        """展開できない `available_tools` は初期化時に落ちる。"""
        with pytest.raises(ValueError, match="available_tools"):
            _make_task(prompt_file, available_tools=available_tools)

    async def test_empty_available_tools_passes_empty_allow_list(
        self, wired, prompt_file: Path, mock_run_conversation
    ) -> None:
        """空リストは空の許可リスト（ツールなし）として渡す。"""
        await _make_task(prompt_file).execute(
            {"discord_client": MagicMock(), "now": _NOW}
        )

        assert mock_run_conversation.await_args.kwargs["allowed_tool_names"] == []

    async def test_passes_expanded_available_tools(
        self, wired, prompt_file: Path, mock_run_conversation
    ) -> None:
        """`$main` を展開し、並べた stem と合わせた許可リストを渡す。"""
        cfg = MagicMock()
        cfg.tools.main_available_tools = ["llm_weather"]
        with patch("lilla_core.loaders.llm_tool_loader.get_config", return_value=cfg):
            task = _make_task(
                prompt_file, available_tools=["$main", "llm_diary", "llm_weather"]
            )

        await task.execute({"discord_client": MagicMock(), "now": _NOW})

        assert mock_run_conversation.await_args.kwargs["allowed_tool_names"] == [
            "llm_weather",
            "llm_diary",
        ]

    def test_exposes_schedule_from_yaml(self, prompt_file: Path) -> None:
        """`schedule` は YAML の値をそのまま公開する（未設定なら None）。"""
        assert _make_task(prompt_file).schedule == "*/30 * * * *"
        assert _make_task(prompt_file, schedule=None).schedule is None

    async def test_sends_reply_and_saves_assistant_message(
        self, wired, prompt_file: Path, mock_run_conversation, mock_send_to_discord,
        mock_repo,
    ) -> None:
        """通知が必要な返答は `target` へ送り、会話履歴にも残す。"""
        task = _make_task(prompt_file)
        bot = MagicMock()

        await task.execute({"discord_client": bot, "now": _NOW, "llm_tools": {"a": {}}})

        kwargs = mock_run_conversation.await_args.kwargs
        assert mock_run_conversation.await_args.args[0] == {"a": {}}
        assert kwargs["client_type"] == "task"
        assert kwargs["llm_name"] == "reminder"
        # `{{now}}` は解決済みタイムゾーンの日時へ置き換わる。
        assert kwargs["inject_user_content"] == (
            "Now is 2026-09-28 21:30. Anything to report?"
        )
        # 会話への追記は send_to_discord に渡したリポジトリが担う。
        mock_send_to_discord.assert_awaited_once_with(
            bot, "dm:42", "今日はごみの日だよ", mock_repo
        )

    @pytest.mark.parametrize(
        "reply",
        [
            "NO_NOTIFICATION",
            "  NO_NOTIFICATION  ",
            "**NO_NOTIFICATION**",
            "* NO_NOTIFICATION *",
            "",
            "   ",
            None,
        ],
    )
    async def test_skips_notification(
        self, wired, prompt_file: Path, mock_run_conversation, mock_send_to_discord,
        mock_repo, reply,
    ) -> None:
        """`NO_NOTIFICATION` と空の返答は会話にも Discord にも残さない。"""
        mock_run_conversation.return_value = reply
        task = _make_task(prompt_file)

        await task.execute({"discord_client": MagicMock(), "now": _NOW})

        mock_send_to_discord.assert_not_awaited()
        mock_repo.save.assert_not_awaited()

    async def test_skips_when_target_is_missing(
        self, wired, prompt_file: Path, mock_run_conversation, mock_send_to_discord
    ) -> None:
        """`target` が無ければ LLM も呼ばずに何もしない。"""
        task = _make_task(prompt_file, target=None)

        await task.execute({"discord_client": MagicMock(), "now": _NOW})

        mock_run_conversation.assert_not_awaited()
        mock_send_to_discord.assert_not_awaited()

    async def test_skips_when_prompt_resolves_to_empty(
        self, wired, tmp_path: Path, mock_run_conversation, mock_send_to_discord
    ) -> None:
        """source spec が何も読めなければ LLM を呼ばない。"""
        task = _make_task(tmp_path / "missing.md")

        await task.execute({"discord_client": MagicMock(), "now": _NOW})

        mock_run_conversation.assert_not_awaited()
        mock_send_to_discord.assert_not_awaited()

    async def test_uses_local_now_when_context_has_no_now(
        self, wired, prompt_file: Path, mock_run_conversation
    ) -> None:
        """context に `now` が無ければ `local_now()` を使う。"""
        task = _make_task(prompt_file)

        with patch(
            "lilla_core.builtin_tools.task_scheduled_llm.local_now", return_value=_NOW
        ):
            await task.execute({"discord_client": MagicMock()})

        assert mock_run_conversation.await_args.kwargs["inject_user_content"] == (
            "Now is 2026-09-28 21:30. Anything to report?"
        )

    async def test_swallows_exceptions(
        self, wired, prompt_file: Path, mock_run_conversation
    ) -> None:
        """実行中の例外は外へ漏らさない。"""
        mock_run_conversation.side_effect = RuntimeError("boom")
        task = _make_task(prompt_file)

        await task.execute({"discord_client": MagicMock(), "now": _NOW})

    def test_loads_via_import_path(self, tmp_path: Path, prompt_file: Path) -> None:
        """type: lilla_core.builtin_tools.task_scheduled_llm の YAML からロードできる。"""
        from lilla_core.loaders import task_tool_loader

        config_root = tmp_path / "config_root"
        tools_dir = config_root / "tools"
        tools_dir.mkdir(parents=True)
        (tools_dir / "task_scheduled_llm.yaml").write_text(
            "type: lilla_core.builtin_tools.task_scheduled_llm\n"
            'schedule: "0 8 * * *"\n'
            "target: dm:{DISCORD_MY_USER_ID}\n"
            "llm_provider: reminder\n"
            f"prompt: file:{prompt_file}\n"
            "available_tools: []\n",
            encoding="utf-8",
        )

        result = task_tool_loader.load_all_tools(
            tool_roots=[], config_root=config_root, class_map={}
        )

        entry = result["task_scheduled_llm"]
        assert isinstance(entry["instance"], ScheduledLlmTask)
        assert entry["trigger"] == "task"
        assert entry["scheduled"] is True
