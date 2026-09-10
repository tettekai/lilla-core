"""commands/selftest.py のテスト。"""
from __future__ import annotations

import importlib
from unittest.mock import AsyncMock, MagicMock

import pytest

from lilla_core.services.system_checks import CheckResult

selftest_command = importlib.import_module("lilla_core.commands.selftest")


def _make_message() -> MagicMock:
    """channel.send が AsyncMock の Discord メッセージモックを返す。"""
    msg = MagicMock()
    msg.channel.send = AsyncMock()
    return msg


def _result(name: str, ok: bool = True, detail: str = "詳細") -> CheckResult:
    """テスト用の CheckResult を生成する。"""
    return CheckResult(name=name, ok=ok, detail=detail, elapsed_ms=1.5)


@pytest.fixture
def patched_checks(monkeypatch: pytest.MonkeyPatch) -> dict[str, AsyncMock]:
    """各チェック関数を成功固定のモックへ差し替える。"""
    mocks: dict[str, AsyncMock] = {}
    for name in (
        "check_process_alive",
        "check_mongodb",
        "check_command_registry",
        "check_task_tools",
        "check_llm",
    ):
        mock = AsyncMock(return_value=_result(name.removeprefix("check_")))
        monkeypatch.setattr(selftest_command, name, mock)
        mocks[name] = mock
    return mocks


def _sent(msg: MagicMock) -> tuple[str, str]:
    """送信された本文と添付ファイルの中身を取り出す。"""
    kwargs = msg.channel.send.call_args[1]
    attachment = kwargs["file"]
    return kwargs["content"], attachment.fp.getvalue().decode("utf-8")


class TestHandleSelftest:
    async def test_runs_basic_checks(self, patched_checks: dict[str, AsyncMock]) -> None:
        """引数なしでは 4 項目を実行し、LLM 疎通確認は行わない。"""
        msg = _make_message()

        await selftest_command.handle_selftest(msg, "", {"task_a": object()}, MagicMock())

        patched_checks["check_process_alive"].assert_awaited_once()
        patched_checks["check_mongodb"].assert_awaited_once()
        patched_checks["check_command_registry"].assert_awaited_once()
        patched_checks["check_task_tools"].assert_awaited_once()
        patched_checks["check_llm"].assert_not_awaited()

    async def test_passes_tools_to_task_tools_check(
        self, patched_checks: dict[str, AsyncMock]
    ) -> None:
        """タスクツール件数チェックには受け取った tools をそのまま渡す。"""
        tools = {"task_a": object()}
        msg = _make_message()

        await selftest_command.handle_selftest(msg, "", tools, MagicMock())

        patched_checks["check_task_tools"].assert_awaited_once_with(tools)

    async def test_full_arg_adds_llm_check(self, patched_checks: dict[str, AsyncMock]) -> None:
        """`full` 指定時は LLM 疎通確認も実行する。"""
        msg = _make_message()

        await selftest_command.handle_selftest(msg, " full ", {}, MagicMock())

        patched_checks["check_llm"].assert_awaited_once()
        content, _ = _sent(msg)
        assert "llm" in content

    async def test_replies_to_source_channel(self, patched_checks: dict[str, AsyncMock]) -> None:
        """診断結果は常に呼び出し元チャンネルへ返す。"""
        msg = _make_message()

        await selftest_command.handle_selftest(msg, "", {}, MagicMock())

        msg.channel.send.assert_awaited_once()

    async def test_summary_lists_each_check(self, patched_checks: dict[str, AsyncMock]) -> None:
        """本文には各チェックの成否が ✅ / ❌ の箇条書きで載る。"""
        patched_checks["check_mongodb"].return_value = _result("mongodb", ok=False)
        msg = _make_message()

        await selftest_command.handle_selftest(msg, "", {}, MagicMock())

        content, _ = _sent(msg)
        assert "3/4 成功" in content
        assert "✅ process_alive" in content
        assert "❌ mongodb" in content

    async def test_detail_attached_as_file(self, patched_checks: dict[str, AsyncMock]) -> None:
        """detail と elapsed_ms は添付ファイルに載る。"""
        patched_checks["check_mongodb"].return_value = _result(
            "mongodb", ok=False, detail="3秒でタイムアウトしました"
        )
        msg = _make_message()

        await selftest_command.handle_selftest(msg, "", {}, MagicMock())

        assert msg.channel.send.call_args[1]["file"].filename == "selftest_result.txt"
        _, detail = _sent(msg)
        assert "[NG] mongodb (1.5ms)" in detail
        assert "3秒でタイムアウトしました" in detail

    async def test_failed_check_does_not_stop_others(
        self, patched_checks: dict[str, AsyncMock]
    ) -> None:
        """1 件のチェック失敗が他のチェックの実行を妨げない。"""
        patched_checks["check_process_alive"].return_value = _result(
            "process_alive", ok=False
        )
        msg = _make_message()

        await selftest_command.handle_selftest(msg, "full", {}, MagicMock())

        for mock in patched_checks.values():
            mock.assert_awaited_once()

    async def test_registered_in_registry(self) -> None:
        """`!selftest` としてコマンドレジストリに登録されている。"""
        from lilla_core.commands.registry import get_command_handler

        assert get_command_handler("selftest") is selftest_command.handle_selftest
