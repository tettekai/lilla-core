"""builtin_tools/llm_current_datetime.py のテスト。"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_cfg() -> MagicMock:
    """llm_tool_loader が参照する AppConfig モック。"""
    cfg = MagicMock()
    cfg.paths.tool_root = Path("/fake/tools")
    cfg.env.config_root = Path("/fake/config")
    return cfg


@pytest.fixture
def with_mocked_modules(mock_cfg: MagicMock):
    """依存モジュールを patch.dict で差し替える。"""
    with patch.dict(
        sys.modules,
        {"lilla_core.core.config": MagicMock(get_config=lambda: mock_cfg)},
    ):
        yield


@pytest.fixture
def llm_tool_loader(with_mocked_modules):
    """patch.dict 有効後に llm_tool_loader をロードする。"""
    sys.modules.pop("lilla_core.loaders.llm_tool_loader", None)
    import lilla_core.loaders.llm_tool_loader as loaded
    yield loaded
    sys.modules.pop("lilla_core.loaders.llm_tool_loader", None)


class TestLlmCurrentDatetime:
    def test_loads_via_import_path(
        self, llm_tool_loader, tmp_path: Path
    ) -> None:
        """type: lilla_core.builtin_tools.llm_current_datetime の YAML からロードできる。"""
        config_root = tmp_path / "config_root"
        tools_dir = config_root / "tools"
        tools_dir.mkdir(parents=True)
        (tools_dir / "llm_current_datetime.yaml").write_text(
            "type: lilla_core.builtin_tools.llm_current_datetime\n", encoding="utf-8"
        )

        result = llm_tool_loader.load_llm_tools(tool_roots=[], config_root=config_root)

        assert "llm_current_datetime" in result
        assert callable(result["llm_current_datetime"]["execute"])
        assert result["llm_current_datetime"]["schema"]["function"]["name"] == (
            "llm_current_datetime"
        )

    async def test_execute_returns_local_now(self) -> None:
        """execute() が local_now() 由来の現在日時を返す。"""
        from lilla_core.builtin_tools.llm_current_datetime import execute
        from lilla_core.utils.datetime_utils import local_now

        with patch(
            "lilla_core.builtin_tools.llm_current_datetime.local_now"
        ) as mock_local_now:
            mock_local_now.return_value = local_now()
            result = await execute({}, {})

        assert result["success"] is True
        assert result["data"] == mock_local_now.return_value.isoformat()
