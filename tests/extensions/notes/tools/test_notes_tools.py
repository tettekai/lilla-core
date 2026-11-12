"""`notes` 拡張の LLM ツール（`llm_notes_*`）のテスト。"""
from __future__ import annotations

from lilla_core.extensions.notes import service


class TestNotesWrite:
    """`llm_notes_write`。"""

    async def test_writes(self, notes_repo, load_tool) -> None:
        """書いた結果を `files` と同じ形で返す。"""
        tool = load_tool("llm_notes_write")
        result = await tool.execute({"path": "diary/a.md", "content": "abc", "mode": "create"}, {})
        assert result["success"] is True
        assert result["data"] == {"path": "diary/a.md", "mode": "create", "created": True, "size": 3}

    async def test_validation_errors(self, notes_repo, load_tool) -> None:
        """不正な引数・パス・既存は `tool_error`。"""
        tool = load_tool("llm_notes_write")
        assert (await tool.execute({"path": "a.md", "content": 1, "mode": "create"}, {}))["success"] is False
        assert (await tool.execute({"path": "a.md", "content": "x", "mode": "bad"}, {}))["success"] is False
        bad_path = await tool.execute({"path": "../a", "content": "x", "mode": "create"}, {})
        assert bad_path["success"] is False and "相対パス" in bad_path["error"]
        await tool.execute({"path": "a.md", "content": "x", "mode": "create"}, {})
        exists = await tool.execute({"path": "a.md", "content": "x", "mode": "create"}, {})
        assert exists["success"] is False and "既に" in exists["error"]

    async def test_unexpected_error_is_notified(self, notes_repo, load_tool, notify, monkeypatch) -> None:
        """想定外の例外は汎用の文言にして通知へ流す。"""
        tool = load_tool("llm_notes_write")

        async def boom(*args, **kwargs):
            raise RuntimeError("git down secret")

        monkeypatch.setattr(notes_repo, "commit", boom)
        result = await tool.execute({"path": "a.md", "content": "x", "mode": "create"}, {})
        assert result["success"] is False
        assert "secret" not in result["error"]
        notify.assert_awaited_once()


class TestNotesRead:
    """`llm_notes_read`。"""

    async def test_reads_with_offset(self, notes_repo, load_tool) -> None:
        """`max_read_chars`（10）で切り詰め、`next_offset` で続きを読める。"""
        tool = load_tool("llm_notes_read")
        await service.write_note("a.md", "0123456789abcdef", "create")
        first = await tool.execute({"path": "a.md"}, {})
        assert first["data"]["content"] == "0123456789"
        assert first["data"]["truncated"] is True
        rest = await tool.execute({"path": "a.md", "offset": first["data"]["next_offset"]}, {})
        assert rest["data"]["content"] == "abcdef"
        assert rest["data"]["truncated"] is False
        assert "next_offset" not in rest["data"]

    async def test_errors(self, notes_repo, load_tool) -> None:
        """無い・不正な offset は `tool_error`。"""
        tool = load_tool("llm_notes_read")
        assert (await tool.execute({"path": "none.md"}, {}))["success"] is False
        assert (await tool.execute({"path": "a.md", "offset": -1}, {}))["success"] is False
        assert (await tool.execute({"path": "a.md", "limit": True}, {}))["success"] is False


class TestNotesList:
    """`llm_notes_list`。"""

    async def test_lists(self, notes_repo, load_tool) -> None:
        """直下のエントリと、パターンでの絞り込み。"""
        tool = load_tool("llm_notes_list")
        await service.write_note("diary/a.md", "x", "create")
        await service.write_note("diary/b.txt", "yy", "create")
        await service.write_note("diary/2026/c.md", "z", "create")
        result = await tool.execute({"path": "diary"}, {})
        data = result["data"]
        assert data["path"] == "diary"
        assert [(e["name"], e["type"]) for e in data["entries"]] == [
            ("2026", "dir"), ("a.md", "file"), ("b.txt", "file"),
        ]
        assert "size" not in data["entries"][0]
        assert data["entries"][2]["size"] == 2
        filtered = await tool.execute({"path": "diary", "pattern": "*.md"}, {})
        assert [e["name"] for e in filtered["data"]["entries"]] == ["a.md"]

    async def test_errors(self, notes_repo, load_tool) -> None:
        """無いディレクトリ・不正な pattern は `tool_error`。"""
        tool = load_tool("llm_notes_list")
        assert (await tool.execute({"path": "none"}, {}))["success"] is False
        assert (await tool.execute({"path": "", "pattern": " "}, {}))["success"] is False
        top = await tool.execute({"path": ""}, {})
        assert top["success"] is True and top["data"]["entries"] == []


class TestNotesSearch:
    """`llm_notes_search`。"""

    async def test_searches(self, notes_repo, load_tool) -> None:
        """該当行を `files` と同じ形で返す。"""
        tool = load_tool("llm_notes_search")
        await service.write_note("a.md", "x\nHello\n", "create")
        result = await tool.execute({"path": "", "query": "hello"}, {})
        assert result["data"]["matches"] == [{"path": "a.md", "line": 2, "text": "Hello"}]
        assert result["data"]["truncated"] is False

    async def test_requires_query(self, notes_repo, load_tool) -> None:
        """空の query は `tool_error`。"""
        tool = load_tool("llm_notes_search")
        assert (await tool.execute({"path": "", "query": "  "}, {}))["success"] is False
