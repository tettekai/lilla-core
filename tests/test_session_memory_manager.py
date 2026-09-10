"""session_memory_manager.py のテスト。"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_cfg() -> MagicMock:
    """session_memory_manager が参照する AppConfig モック。"""
    cfg = MagicMock()
    cfg.memory.session_memory_ttl_hours = 3
    return cfg


@pytest.fixture
def with_mocked_modules(mock_cfg: MagicMock):
    """依存モジュールを patch.dict で差し替える。"""
    with patch.dict(
        sys.modules,
        {
            "lilla_core.core.config": MagicMock(AppConfig=MagicMock(), get_config=lambda: mock_cfg),
        },
    ):
        yield


@pytest.fixture
def smm_module(with_mocked_modules):
    """patch.dict 有効後に session_memory_manager をロードする。"""
    import importlib
    sys.modules.pop("lilla_core.services.session_memory_manager", None)
    loaded = importlib.import_module("lilla_core.services.session_memory_manager")
    yield loaded
    sys.modules.pop("lilla_core.services.session_memory_manager", None)


class _FakeClock:
    """テスト用の可変時刻クロック。utc_now の差し替え先として使う。"""

    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> None:
        self.now += timedelta(**kwargs)


@pytest.fixture()
def clock(smm_module, monkeypatch: pytest.MonkeyPatch) -> _FakeClock:
    """smm_module.utc_now を差し替え可能な fake clock にする。"""
    fake = _FakeClock(datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc))
    monkeypatch.setattr(smm_module, "utc_now", fake)
    return fake


@pytest.fixture()
def manager(smm_module):
    """テスト用 SessionMemoryManager インスタンスを返す（TTL=3時間）。"""
    cfg = MagicMock()
    cfg.memory.session_memory_ttl_hours = 3
    return smm_module.SessionMemoryManager(cfg)


class TestSessionMemoryManager:
    def test_get_returns_none_when_unset(
        self, manager: smm_module.SessionMemoryManager, clock: _FakeClock
    ) -> None:
        """未設定の場合 get は None を返す。"""
        assert manager.get() is None

    def test_set_then_get_returns_content(
        self, manager: smm_module.SessionMemoryManager, clock: _FakeClock
    ) -> None:
        """set した内容が get で取得できる。"""
        manager.set("作業中: XXXを実装中")
        assert manager.get() == "作業中: XXXを実装中"

    def test_second_set_overwrites(
        self, manager: smm_module.SessionMemoryManager, clock: _FakeClock
    ) -> None:
        """2回目の set は上書きされる（単一スロットで蓄積されない）。"""
        manager.set("最初の内容")
        manager.set("次の内容")
        assert manager.get() == "次の内容"

    def test_clear_then_get_returns_none(
        self, manager: smm_module.SessionMemoryManager, clock: _FakeClock
    ) -> None:
        """clear 後は get が None を返す。"""
        manager.set("内容")
        manager.clear()
        assert manager.get() is None

    def test_clear_when_already_empty_is_noop(
        self, manager: smm_module.SessionMemoryManager, clock: _FakeClock
    ) -> None:
        """既に空の状態で clear を呼んでもエラーにならない。"""
        manager.clear()
        assert manager.get() is None

    def test_get_within_ttl_returns_content(
        self, manager: smm_module.SessionMemoryManager, clock: _FakeClock
    ) -> None:
        """TTL 内であれば有効な内容を返す。"""
        manager.set("内容")
        clock.advance(hours=2, minutes=59)
        assert manager.get() == "内容"

    def test_get_after_ttl_returns_none(
        self, manager: smm_module.SessionMemoryManager, clock: _FakeClock
    ) -> None:
        """TTL を超過すると None を返す。"""
        manager.set("内容")
        clock.advance(hours=3, minutes=1)
        assert manager.get() is None

    def test_get_at_exact_ttl_boundary_returns_none(
        self, manager: smm_module.SessionMemoryManager, clock: _FakeClock
    ) -> None:
        """TTL ちょうど経過時点で None を返す（>= 境界）。"""
        manager.set("内容")
        clock.advance(hours=3)
        assert manager.get() is None

    def test_expired_get_releases_slot(
        self, manager: smm_module.SessionMemoryManager, clock: _FakeClock
    ) -> None:
        """TTL 切れ後の get 呼び出しで内部状態が実際に解放される。"""
        manager.set("内容")
        clock.advance(hours=3, minutes=1)
        manager.get()
        assert manager._content is None
        assert manager._updated_at is None

    def test_set_after_expiry_restores_fresh_ttl(
        self, manager: smm_module.SessionMemoryManager, clock: _FakeClock
    ) -> None:
        """TTL 切れ後に set すると新しい TTL ウィンドウが始まる。"""
        manager.set("古い内容")
        clock.advance(hours=3, minutes=1)
        assert manager.get() is None
        manager.set("新しい内容")
        clock.advance(hours=2, minutes=59)
        assert manager.get() == "新しい内容"

    def test_custom_ttl_honored(self, smm_module, clock: _FakeClock) -> None:
        """config の session_memory_ttl_hours がインスタンスごとに反映される。"""
        cfg = MagicMock()
        cfg.memory.session_memory_ttl_hours = 1
        manager = smm_module.SessionMemoryManager(cfg)
        manager.set("内容")
        clock.advance(hours=1, minutes=1)
        assert manager.get() is None


class TestGetSessionMemoryManager:
    def test_returns_session_memory_manager_instance(self, smm_module) -> None:
        """get_session_memory_manager が SessionMemoryManager インスタンスを返す。"""
        mock_cfg = MagicMock()
        mock_cfg.memory.session_memory_ttl_hours = 3

        smm_module.get_session_memory_manager.cache_clear()
        with patch.object(smm_module, "get_config", return_value=mock_cfg):
            instance = smm_module.get_session_memory_manager()
        assert isinstance(instance, smm_module.SessionMemoryManager)

    def test_returns_same_instance_on_repeated_calls(self, smm_module) -> None:
        """get_session_memory_manager を複数回呼んでも同じインスタンスを返す。"""
        mock_cfg = MagicMock()
        mock_cfg.memory.session_memory_ttl_hours = 3

        smm_module.get_session_memory_manager.cache_clear()
        with patch.object(smm_module, "get_config", return_value=mock_cfg):
            first = smm_module.get_session_memory_manager()
            second = smm_module.get_session_memory_manager()
        assert first is second
