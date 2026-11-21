"""`llm_notes_read` のツール結果のキャッシュ（`read_cache`）のテスト。"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest
import yaml

from lilla_core.extensions import notes
from lilla_core.extensions.notes import read_cache, service
from lilla_core.extensions.notes.read_cache import READ_TOOL_NAME, build_cache_write

_CACHED = "---\ntitle: x\nlilla-cache-expiration-minutes: 30\n---\n\nbody line\n"


class TestBuildCacheWrite:
    """FrontMatter からキャッシュの 1 件を作る。"""

    def test_positive_integer_is_cached(self) -> None:
        """正の整数があれば、パス・FrontMatter を除いた本文・その値で 1 件を作る。"""
        assert build_cache_write("user/a.md", _CACHED) == {
            "key": "user/a.md",
            "data": "body line",
            "expiration_minutes": 30,
        }

    @pytest.mark.parametrize("text", [
        "no front matter\n",
        "---\ntitle: x\n---\nbody\n",
        "---\nnot closed\n",
        "---\n---\nbody\n",
    ])
    def test_absent_is_skipped_silently(self, text: str, caplog: pytest.LogCaptureFixture) -> None:
        """FrontMatter・キーが無いノートは WARNING を出さずにスキップする。"""
        with caplog.at_level(logging.WARNING):
            assert build_cache_write("user/a.md", text) is None
        assert caplog.records == []

    @pytest.mark.parametrize("value", ["0", "-5", "abc", "1.5", "true", "'30'", "[1]"])
    def test_invalid_value_warns(self, value: str, caplog: pytest.LogCaptureFixture) -> None:
        """正の整数でない値は WARNING を出してスキップする。"""
        text = f"---\nlilla-cache-expiration-minutes: {value}\n---\nbody\n"
        with caplog.at_level(logging.WARNING):
            assert build_cache_write("user/a.md", text) is None
        assert "lilla-cache-expiration-minutes" in caplog.text

    @pytest.mark.parametrize("front", ["a: [unclosed", "just a string"])
    def test_broken_front_matter_warns(self, front: str, caplog: pytest.LogCaptureFixture) -> None:
        """読めない・マッピングでない FrontMatter は WARNING を出してスキップする。"""
        with caplog.at_level(logging.WARNING):
            assert build_cache_write("user/a.md", f"---\n{front}\n---\nbody\n") is None
        assert "user/a.md" in caplog.text


class TestReadToolCacheWrites:
    """`llm_notes_read` の結果の `cache_writes`。"""

    async def test_cache_writes_hold_untruncated_body(self, notes_repo, load_tool) -> None:
        """`max_read_chars`（10）で切り詰めても、`data` は FrontMatter を除いた全文。"""
        tool = load_tool("llm_notes_read")
        text = "---\nlilla-cache-expiration-minutes: 5\n---\n0123456789abcdef\n"
        await service.write_note("user/a.md", text, "create")
        result = await tool.execute({"path": "user/a.md"}, {})
        assert result["success"] is True
        assert result["data"]["truncated"] is True
        assert result["cache_writes"] == [
            {"key": "user/a.md", "data": "0123456789abcdef", "expiration_minutes": 5}
        ]

    async def test_invalid_value_does_not_change_the_result(self, notes_repo, load_tool) -> None:
        """不正な値ならキャッシュしないが、読み取り結果は変わらない。"""
        tool = load_tool("llm_notes_read")
        text = "---\nlilla-cache-expiration-minutes: 0\n---\nx\n"
        await service.write_note("user/a.md", text, "create")
        result = await tool.execute({"path": "user/a.md"}, {})
        assert result["success"] is True
        assert result["cache_writes"] == []
        assert result["data"]["content"] == text[:10]

    async def test_failure_has_no_cache_writes(self, notes_repo, load_tool) -> None:
        """失敗結果には `cache_writes` を付けない。"""
        tool = load_tool("llm_notes_read")
        result = await tool.execute({"path": "user/none.md"}, {})
        assert result["success"] is False
        assert "cache_writes" not in result

    def test_bundled_yaml_uses_auto_cache(self) -> None:
        """同梱の YAML は `cache.mode: auto`。"""
        path = Path(notes.__file__).resolve().parent / "tool_configs" / "llm_notes_read.yaml"
        assert yaml.safe_load(path.read_text(encoding="utf-8"))["cache"] == {"mode": "auto"}

    async def test_saved_through_loader_and_refreshed_after_write(
        self, notes_repo, load_tool, tool_cache
    ) -> None:
        """ローダー経由で `(llm_notes_read, path)` に保存し、書き込みで消え、次の読み取りで入れ直す。"""
        from lilla_core.loaders.llm_tool_loader import _save_tool_cache

        tool = load_tool("llm_notes_read")
        config = {"cache": {"mode": "auto"}}
        await service.write_note("user/a.md", _CACHED, "create")
        result = await tool.execute({"path": "user/a.md"}, {})
        await _save_tool_cache(READ_TOOL_NAME, {"path": "user/a.md"}, config, result)
        assert tool_cache.entries[(READ_TOOL_NAME, "user/a.md")]["data"] == "body line"

        await service.write_note("user/a.md", _CACHED.replace("body line", "new body"), "overwrite")
        assert (READ_TOOL_NAME, "user/a.md") not in tool_cache.entries

        result = await tool.execute({"path": "user/a.md"}, {})
        await _save_tool_cache(READ_TOOL_NAME, {"path": "user/a.md"}, config, result)
        assert tool_cache.entries[(READ_TOOL_NAME, "user/a.md")]["data"] == "new body"


class TestInvalidation:
    """書き込みのあと、変わったノートのキャッシュを消す。"""

    async def _seed(self, tool_cache, *paths: str) -> None:
        """パスごとにキャッシュを入れる（別のツールのキャッシュも 1 件入れる）。"""
        for path in paths:
            await tool_cache.set(READ_TOOL_NAME, path, "cached", 30)
        await tool_cache.set("llm_other", paths[0], "other", 30)

    async def test_write_delete_restore_invalidate_only_changed(self, notes_repo, tool_cache) -> None:
        """書き込み・削除・復元は、そのパスだけを消す（他のノートと別のツールのキャッシュは残す）。"""
        await service.write_note("user/a.md", "a1", "create")
        await service.write_note("user/b.md", "b1", "create")

        await self._seed(tool_cache, "user/a.md", "user/b.md")
        await service.write_note("user/a.md", "a2", "overwrite")
        assert set(tool_cache.entries) == {(READ_TOOL_NAME, "user/b.md"), ("llm_other", "user/a.md")}

        await self._seed(tool_cache, "user/a.md", "user/b.md")
        await service.restore_previous_version("user/a.md")
        assert (READ_TOOL_NAME, "user/a.md") not in tool_cache.entries
        assert (READ_TOOL_NAME, "user/b.md") in tool_cache.entries

        await self._seed(tool_cache, "user/a.md", "user/b.md")
        await service.delete_note("user/a.md")
        assert (READ_TOOL_NAME, "user/a.md") not in tool_cache.entries
        assert (READ_TOOL_NAME, "user/b.md") in tool_cache.entries

    async def test_multiple_changes_invalidate_all(self, notes_repo, tool_cache, notes_config) -> None:
        """1 回のコミットで複数のファイルが変わったら（push など）、その分を全部消す。"""
        await self._seed(tool_cache, "user/a.md", "user/b.md", "user/c.md")
        await service.notify_changes(
            [("user/a.md", "x", "y"), ("user/b.md", "", "new"), ("user/c.md", "same", "same")],
            "0" * 40,
            notes_config,
        )
        assert (READ_TOOL_NAME, "user/a.md") not in tool_cache.entries
        assert (READ_TOOL_NAME, "user/b.md") not in tool_cache.entries
        assert (READ_TOOL_NAME, "user/c.md") in tool_cache.entries

    async def test_delete_failure_only_warns(
        self, notes_repo, tool_cache, monkeypatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """キャッシュを消せなくても WARNING だけで、書き込みは成功する。"""
        async def boom(*args, **kwargs):
            raise RuntimeError("mongo down")

        monkeypatch.setattr(tool_cache, "delete", boom)
        with caplog.at_level(logging.WARNING, logger=read_cache.__name__):
            result = await service.write_note("user/a.md", "x", "create")
        assert result.created is True
        assert "user/a.md" in caplog.text
