"""`lilla_core.services.llm_usage` のテスト。"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

import lilla_core.services.llm_usage as llm_usage
from lilla_core.core.llm_call_origin import llm_call_origin


def _provider(**kw) -> MagicMock:
    """具体プロバイダーの設定に見立てたモック。"""
    provider = MagicMock(type="openai_compat", model="m1")
    provider.input_price_per_million_tokens = kw.get("input_price")
    provider.output_price_per_million_tokens = kw.get("output_price")
    return provider


@pytest.fixture
def cfg(monkeypatch) -> MagicMock:
    """記録が参照する設定（有効・保持 30 日）。"""
    config = MagicMock()
    config.llm.usage_log.enabled = True
    config.llm.usage_log.retention_days = 30
    monkeypatch.setattr(llm_usage, "get_config", lambda: config)
    return config


class TestBuildUsage:
    def test_none_when_nothing_present(self) -> None:
        assert llm_usage.build_usage(None, None, None, None) is None

    def test_total_is_sum_when_missing(self) -> None:
        usage = llm_usage.build_usage(3, 4, None, None)
        assert usage.total_tokens == 7

    def test_partial_counts_leave_total_unknown(self) -> None:
        usage = llm_usage.build_usage(3, None, None, {"prompt_tokens": 3})
        assert usage.input_tokens == 3
        assert usage.total_tokens is None

    def test_rejects_non_int_values(self) -> None:
        usage = llm_usage.build_usage("3", True, -1, {"x": 1})
        assert (usage.input_tokens, usage.output_tokens, usage.total_tokens) == (None, None, None)

    def test_raw_keeps_only_numbers(self) -> None:
        raw = {"prompt_tokens": 1, "note": "hello", "details": {"cached": 2, "text": "x"}}
        usage = llm_usage.build_usage(1, 1, 2, raw)
        assert usage.raw == {"prompt_tokens": 1, "details": {"cached": 2}}


class TestEstimateCost:
    def test_none_without_prices(self) -> None:
        usage = llm_usage.LlmUsage(1000, 1000, 2000)
        assert llm_usage.estimate_cost(_provider(), usage) is None

    def test_both_prices(self) -> None:
        usage = llm_usage.LlmUsage(2_000_000, 500_000, 2_500_000)
        cost = llm_usage.estimate_cost(_provider(input_price=1.0, output_price=4.0), usage)
        assert cost == pytest.approx(2.0 + 2.0)

    def test_only_input_price(self) -> None:
        usage = llm_usage.LlmUsage(1_000_000, None, None)
        assert llm_usage.estimate_cost(_provider(input_price=0.5), usage) == pytest.approx(0.5)

    def test_unknown_tokens_for_priced_side(self) -> None:
        usage = llm_usage.LlmUsage(1_000_000, None, None)
        provider = _provider(input_price=0.5, output_price=1.0)
        assert llm_usage.estimate_cost(provider, usage) is None

    def test_no_usage(self) -> None:
        assert llm_usage.estimate_cost(_provider(input_price=1.0), None) is None


class TestBuildUsageRecord:
    def test_fields_and_origin(self, cfg, monkeypatch) -> None:
        now = datetime(2026, 10, 16, 1, 0, tzinfo=timezone.utc)
        monkeypatch.setattr(llm_usage, "utc_now", lambda: now)
        usage = llm_usage.LlmUsage(10, 5, 15, {"prompt_tokens": 10})
        with llm_call_origin(client_type="discord", discord_channel_id=42):
            record = llm_usage.build_usage_record(
                provider_name="grok",
                provider=_provider(input_price=1.0, output_price=2.0),
                api="tools",
                duration_ms=1234,
                usage=usage,
            )
        assert record == {
            "created_at": now,
            "expire_at": now + timedelta(days=30),
            "provider": "grok",
            "provider_type": "openai_compat",
            "model": "m1",
            "api": "tools",
            "entry": "conversation:discord",
            "client_type": "discord",
            "discord_channel_id": 42,
            "task_name": None,
            "tool_name": None,
            "source": None,
            "duration_ms": 1234,
            "usage_known": True,
            "input_tokens": 10,
            "output_tokens": 5,
            "total_tokens": 15,
            "raw_usage": {"prompt_tokens": 10},
            "cost": pytest.approx(10 / 1e6 + 10 / 1e6),
        }

    def test_unknown_usage(self, cfg) -> None:
        record = llm_usage.build_usage_record(
            provider_name="p", provider=_provider(), api="chat", duration_ms=1, usage=None
        )
        assert record["usage_known"] is False
        assert record["input_tokens"] is None
        assert record["total_tokens"] is None
        assert record["raw_usage"] is None
        assert record["cost"] is None
        assert record["entry"] == "unknown"


class TestRecordLlmUsage:
    def _record(self, **kw) -> None:
        llm_usage.record_llm_usage(
            provider_name="p",
            provider=_provider(),
            api="chat",
            duration_ms=1,
            usage=kw.get("usage"),
        )

    def test_schedules_write(self, cfg, monkeypatch) -> None:
        schedule = MagicMock()
        monkeypatch.setattr(llm_usage, "_schedule_write", schedule)
        self._record()
        schedule.assert_called_once()
        assert schedule.call_args.args[0]["provider"] == "p"

    def test_disabled_does_nothing(self, cfg, monkeypatch) -> None:
        cfg.llm.usage_log.enabled = False
        schedule = MagicMock()
        monkeypatch.setattr(llm_usage, "_schedule_write", schedule)
        self._record()
        schedule.assert_not_called()

    def test_failure_is_swallowed(self, cfg, monkeypatch, caplog) -> None:
        monkeypatch.setattr(llm_usage, "_schedule_write", MagicMock(side_effect=RuntimeError("x")))
        with caplog.at_level(logging.WARNING):
            self._record()
        assert any("Failed to record LLM usage" in r.message for r in caplog.records)


@pytest.mark.real_llm_usage_writes
class TestWrite:
    async def test_schedule_write_inserts_in_background(self, monkeypatch) -> None:
        repo = MagicMock()
        repo.insert = AsyncMock()
        import lilla_core.repository.llm_usage_repository as repo_module

        monkeypatch.setattr(repo_module, "get_llm_usage_repo", lambda: repo)
        llm_usage._schedule_write({"provider": "p"})
        assert llm_usage._pending_writes
        await asyncio.gather(*llm_usage._pending_writes)
        repo.insert.assert_awaited_once_with({"provider": "p"})

    async def test_write_failure_is_logged(self, monkeypatch, caplog) -> None:
        repo = MagicMock()
        repo.insert = AsyncMock(side_effect=RuntimeError("down"))
        import lilla_core.repository.llm_usage_repository as repo_module

        monkeypatch.setattr(repo_module, "get_llm_usage_repo", lambda: repo)
        with caplog.at_level(logging.WARNING):
            await llm_usage._write_record({"provider": "p"})
        assert any("Failed to record LLM usage" in r.message for r in caplog.records)
