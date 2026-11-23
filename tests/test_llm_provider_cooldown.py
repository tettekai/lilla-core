"""services/llm_provider_cooldown.py のテスト。"""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest

from lilla_core.core.exceptions import LLMError, LlmSendBlockedError, LlmSendGuardUnavailableError
from lilla_core.services import llm_provider_cooldown as cooldown
from lilla_core.utils.datetime_utils import utc_now


def _error(kind="http", status=None, retry_after=None, provider="p1", cls=LLMError):
    return cls(
        "LLM call error: x",
        provider_name=provider,
        error_kind=kind,
        status=status,
        retry_after=retry_after,
    )


class TestIsReselectableFailure:
    @pytest.mark.parametrize("status", [404, 408, 429, 500, 502, 503, 599])
    def test_reselectable_statuses(self, status) -> None:
        assert cooldown.is_reselectable_failure(_error(status=status))

    @pytest.mark.parametrize("status", [400, 401, 403, 422, None])
    def test_other_statuses_are_not(self, status) -> None:
        assert not cooldown.is_reselectable_failure(_error(status=status))

    @pytest.mark.parametrize("kind", ["connection", "timeout"])
    def test_connection_and_timeout(self, kind) -> None:
        assert cooldown.is_reselectable_failure(_error(kind=kind))

    @pytest.mark.parametrize("kind", ["invalid_response", "other"])
    def test_other_kinds_are_not(self, kind) -> None:
        assert not cooldown.is_reselectable_failure(_error(kind=kind))

    @pytest.mark.parametrize("cls", [LlmSendBlockedError, LlmSendGuardUnavailableError])
    def test_send_guard_errors_are_not(self, cls) -> None:
        assert not cooldown.is_reselectable_failure(_error(status=503, cls=cls))

    def test_missing_provider_is_not(self) -> None:
        assert not cooldown.is_reselectable_failure(_error(status=503, provider=None))

    def test_non_llm_error_is_not(self) -> None:
        assert not cooldown.is_reselectable_failure(RuntimeError("x"))


class TestRecordFailure:
    def test_uses_cooldown_seconds_without_retry_after(self) -> None:
        before = utc_now()
        cooldown.record_failure(_error(status=503), 300.0)
        (entry,) = cooldown.unavailable_providers()
        assert entry.provider == "p1"
        assert entry.error_kind == "http"
        assert entry.status == 503
        assert before + timedelta(seconds=299) < entry.until <= utc_now() + timedelta(seconds=300)

    def test_retry_after_wins_over_cooldown_seconds(self) -> None:
        cooldown.record_failure(_error(status=429, retry_after=3600.0), 300.0)
        (entry,) = cooldown.unavailable_providers()
        assert entry.until > utc_now() + timedelta(seconds=3500)

    @pytest.mark.parametrize("seconds", [0, 0.0, -1, None])
    def test_zero_or_missing_length_does_not_record(self, seconds) -> None:
        cooldown.record_failure(_error(status=503), seconds)
        assert cooldown.unavailable_providers() == ()

    def test_non_reselectable_failure_is_not_recorded(self) -> None:
        cooldown.record_failure(_error(status=400), 300.0)
        assert cooldown.unavailable_providers() == ()

    def test_new_failure_replaces_entry(self) -> None:
        cooldown.record_failure(_error(status=503), 300.0)
        cooldown.record_failure(_error(kind="timeout"), 10.0)
        (entry,) = cooldown.unavailable_providers()
        assert entry.error_kind == "timeout"
        assert entry.status is None

    def test_sorted_by_name(self) -> None:
        cooldown.record_failure(_error(status=503, provider="zeta"), 300.0)
        cooldown.record_failure(_error(status=503, provider="alpha"), 300.0)
        assert [e.provider for e in cooldown.unavailable_providers()] == ["alpha", "zeta"]


class TestExpiry:
    def test_expired_entries_are_dropped(self) -> None:
        cooldown.record_failure(_error(status=503), 60.0)
        later = utc_now() + timedelta(seconds=61)
        assert cooldown.unavailable_providers(now=later) == ()
        assert cooldown.unavailable_providers() == ()

    def test_entry_until_is_utc_now_plus_seconds(self) -> None:
        fixed = utc_now()
        with patch.object(cooldown, "utc_now", return_value=fixed):
            cooldown.record_failure(_error(status=503), 120.0)
        (entry,) = cooldown.unavailable_providers(now=fixed)
        assert entry.until == fixed + timedelta(seconds=120)


class TestFailureFromError:
    def test_copies_fields(self) -> None:
        failure = cooldown.failure_from_error(_error(status=429, retry_after=5.0))
        assert failure == cooldown.LlmFailure(
            provider="p1", error_kind="http", status=429, retry_after=5.0
        )
