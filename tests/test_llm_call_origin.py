"""`lilla_core.core.llm_call_origin` のテスト。"""
from __future__ import annotations

import asyncio

import pytest

from lilla_core.core.llm_call_origin import (
    UNKNOWN_ENTRY,
    LlmCallOrigin,
    get_llm_call_origin,
    llm_call_origin,
)


class TestEntryLabel:
    def test_unknown_when_empty(self) -> None:
        assert LlmCallOrigin().entry() == UNKNOWN_ENTRY

    def test_conversation_label(self) -> None:
        assert LlmCallOrigin(client_type="discord").entry() == "conversation:discord"

    def test_source_wins_over_client_type(self) -> None:
        assert LlmCallOrigin(client_type="discord", source="selftest").entry() == "selftest"

    def test_task_wins_over_source_and_client_type(self) -> None:
        origin = LlmCallOrigin(client_type="task", task_name="task_daily", source="x")
        assert origin.entry() == "task:task_daily"

    def test_tool_is_innermost(self) -> None:
        origin = LlmCallOrigin(client_type="task", task_name="task_daily", tool_name="llm_expert")
        assert origin.entry() == "tool:llm_expert"


class TestContext:
    def test_default_is_empty(self) -> None:
        assert get_llm_call_origin() == LlmCallOrigin()

    def test_with_sets_and_resets(self) -> None:
        with llm_call_origin(task_name="t1") as origin:
            assert origin.task_name == "t1"
            assert get_llm_call_origin().task_name == "t1"
        assert get_llm_call_origin() == LlmCallOrigin()

    def test_nested_keeps_outer_fields_and_ignores_none(self) -> None:
        with llm_call_origin(task_name="t1"):
            with llm_call_origin(client_type="task", discord_channel_id=None):
                with llm_call_origin(tool_name="llm_expert"):
                    origin = get_llm_call_origin()
        assert origin == LlmCallOrigin(client_type="task", task_name="t1", tool_name="llm_expert")

    def test_unknown_field_raises(self) -> None:
        with pytest.raises(TypeError):
            with llm_call_origin(message="secret"):
                pass

    async def test_propagates_to_child_tasks(self) -> None:
        async def read():
            return get_llm_call_origin().task_name

        with llm_call_origin(task_name="t1"):
            assert await asyncio.create_task(read()) == "t1"
