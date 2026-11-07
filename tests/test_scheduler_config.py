"""`scheduler.enabled`（#309）の設定検証。"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from lilla_core.core.config import SchedulerConfig, core_config_section_names
from lilla_core.testing import use_extensions, write_minimal_lilla_yaml


class TestSchedulerConfig:
    def test_default_is_enabled(self) -> None:
        """省略時は従来どおり内部スケジューラを使う。"""
        assert SchedulerConfig().enabled is True

    @pytest.mark.parametrize("value", [True, False])
    def test_accepts_booleans(self, value: bool) -> None:
        assert SchedulerConfig(enabled=value).enabled is value

    @pytest.mark.parametrize("value", ["false", "yes", 0, 1, None])
    def test_rejects_non_booleans(self, value) -> None:
        """文字列や数値を真偽値へ読み替えない（`"false"` が真になる事故を防ぐ）。"""
        with pytest.raises(ValidationError):
            SchedulerConfig(enabled=value)

    def test_is_core_section(self) -> None:
        assert "scheduler" in core_config_section_names()


class TestSchedulerInLillaYaml:
    def test_omitted_section_defaults_to_enabled(self, tmp_path) -> None:
        write_minimal_lilla_yaml(tmp_path)
        with use_extensions(config_root=tmp_path) as cfg:
            assert cfg.scheduler.enabled is True

    def test_disabled_in_yaml(self, tmp_path) -> None:
        write_minimal_lilla_yaml(tmp_path, extra={"scheduler": {"enabled": False}})
        with use_extensions(config_root=tmp_path) as cfg:
            assert cfg.scheduler.enabled is False

    def test_invalid_type_fails_validation(self, tmp_path) -> None:
        write_minimal_lilla_yaml(tmp_path, extra={"scheduler": {"enabled": "no"}})
        with pytest.raises(ValidationError):
            with use_extensions(config_root=tmp_path):
                pass
