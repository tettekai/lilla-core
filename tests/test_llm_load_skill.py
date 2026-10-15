"""builtin_tools/llm_load_skill.py のテスト。"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lilla_core.services.skills import format_skill_instruction
from lilla_core.ui.messages import t

load_skill = importlib.import_module("lilla_core.builtin_tools.llm_load_skill")


@pytest.fixture
def repo(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """スキルリポジトリを差し替える。"""
    repo = MagicMock()
    repo.find_by_name = AsyncMock(return_value=None)
    repo.get_enabled = AsyncMock(return_value=[])
    repo_module = MagicMock()
    repo_module.get_skill_repo = MagicMock(return_value=repo)
    monkeypatch.setitem(sys.modules, "lilla_core.repository.skill_repository", repo_module)
    return repo


def _limits(**overrides) -> dict[str, int]:
    """既定値に上書きを重ねた上限の辞書を返す。"""
    return {**load_skill.DEFAULT_LIMITS, **overrides}


class TestExecute:
    async def test_returns_wrapped_prompt(self, repo: MagicMock) -> None:
        """有効なスキルは `format_skill_instruction`（追加指示なし）で包んで返す。"""
        repo.find_by_name.return_value = {
            "name": "proofread", "prompt": "校正して", "enabled": True,
        }

        result = await load_skill.execute({"name": "proofread"}, {})

        assert result["success"] is True
        assert result["data"] == format_skill_instruction("proofread", "校正して")
        repo.find_by_name.assert_awaited_once_with("proofread")

    async def test_name_is_case_insensitive(self, repo: MagicMock) -> None:
        """名前は小文字へ正規化して照合する。"""
        repo.find_by_name.return_value = {"name": "proofread", "prompt": "p"}

        result = await load_skill.execute({"name": "ProofRead"}, {})

        assert result["success"] is True
        repo.find_by_name.assert_awaited_once_with("proofread")
        assert result["data"] == format_skill_instruction("proofread", "p")

    async def test_reason_is_not_in_result(self, repo: MagicMock) -> None:
        """`reason` は省略でき、指定しても結果の本文に含まれない。"""
        repo.find_by_name.return_value = {"name": "a", "prompt": "body"}

        without = await load_skill.execute({"name": "a"}, {})
        with_reason = await load_skill.execute({"name": "a", "reason": "REASON-TEXT"}, {})

        assert with_reason["data"] == without["data"]
        assert "REASON-TEXT" not in with_reason["data"]
        assert "REASON-TEXT" not in with_reason["memory_entry"]

    @pytest.mark.parametrize("name", ["has space", "", "a" * 65, "日本語", None, 123])
    async def test_invalid_name_is_error(self, repo: MagicMock, name) -> None:
        """規則に合わない名前はリポジトリを引かずに `tool_error` になる。"""
        result = await load_skill.execute({"name": name}, {})

        assert result["success"] is False
        assert result["error"] == t("tool.load_skill.not_found", name=name)
        repo.find_by_name.assert_not_awaited()

    async def test_missing_name_is_error(self, repo: MagicMock) -> None:
        """`name` が無ければ `tool_error`。"""
        result = await load_skill.execute({}, {})

        assert result["success"] is False

    async def test_not_found_is_error(self, repo: MagicMock) -> None:
        """存在しないスキルは not_found の `tool_error`。"""
        result = await load_skill.execute({"name": "Missing"}, {})

        assert result["success"] is False
        assert result["error"] == t("tool.load_skill.not_found", name="missing")

    async def test_disabled_is_error(self, repo: MagicMock) -> None:
        """無効なスキルは not_found と区別した disabled の `tool_error`。"""
        repo.find_by_name.return_value = {"name": "a", "prompt": "p", "enabled": False}

        result = await load_skill.execute({"name": "a"}, {})

        assert result["success"] is False
        assert result["error"] == t("tool.load_skill.disabled", name="a")
        assert result["error"] != t("tool.load_skill.not_found", name="a")

    async def test_reads_repository_every_call(self, repo: MagicMock) -> None:
        """キャッシュせず、毎回リポジトリから読むので編集・無効化がすぐ反映される。"""
        repo.find_by_name.return_value = {"name": "a", "prompt": "old"}
        first = await load_skill.execute({"name": "a"}, {})
        repo.find_by_name.return_value = {"name": "a", "prompt": "new"}
        second = await load_skill.execute({"name": "a"}, {})
        repo.find_by_name.return_value = {"name": "a", "prompt": "new", "enabled": False}
        third = await load_skill.execute({"name": "a"}, {})

        assert first["data"] == format_skill_instruction("a", "old")
        assert second["data"] == format_skill_instruction("a", "new")
        assert third["success"] is False
        assert repo.find_by_name.await_count == 3


class TestResolveLimits:
    def test_defaults(self) -> None:
        """未指定なら既定値（50 / 200 / 4000）。"""
        assert load_skill.resolve_limits({}) == {
            "max_skills": 50,
            "max_description_chars": 200,
            "max_list_chars": 4000,
        }
        assert load_skill.resolve_limits(None) == load_skill.DEFAULT_LIMITS

    def test_overrides(self) -> None:
        """YAML の値で上書きできる。"""
        limits = load_skill.resolve_limits(
            {"max_skills": 3, "max_description_chars": 10, "max_list_chars": 100}
        )
        assert limits == {"max_skills": 3, "max_description_chars": 10, "max_list_chars": 100}

    @pytest.mark.parametrize("key", list(load_skill.DEFAULT_LIMITS))
    @pytest.mark.parametrize("value", [0, -1, "10", 1.5, True, None])
    def test_invalid_values_raise(self, key: str, value) -> None:
        """0 以下・数値でない値は ValueError。"""
        with pytest.raises(ValueError, match=key):
            load_skill.resolve_limits({key: value})

    def test_build_schema_validates(self) -> None:
        """`build_schema` は上限を検証し、正しければ SCHEMA を返す。"""
        assert load_skill.build_schema({}) is load_skill.SCHEMA
        with pytest.raises(ValueError):
            load_skill.build_schema({"max_skills": 0})


class TestIsLoadSkillTool:
    @pytest.mark.parametrize(
        "tool_type", ["llm_load_skill", "lilla_core.builtin_tools.llm_load_skill"]
    )
    def test_matches_type(self, tool_type: str) -> None:
        """ファイル名・import パスどちらの `type` でも判定できる。"""
        assert load_skill.is_load_skill_tool({"tool_config": {"type": tool_type}})

    def test_other_tool(self) -> None:
        """他のツールや `tool_config` の無いエントリは対象外。"""
        assert not load_skill.is_load_skill_tool({"tool_config": {"type": "llm_expert"}})
        assert not load_skill.is_load_skill_tool({})


class TestFormatSkillListSection:
    def test_empty_returns_empty(self) -> None:
        """有効なスキルが 0 件なら節を出さない。"""
        assert load_skill.format_skill_list_section([], "llm_load_skill", _limits()) == ""

    def test_lists_names_and_descriptions(self) -> None:
        """見出し・説明文に続けて `- 名前: 説明`（説明が無ければ名前だけ）を並べる。"""
        skills = [
            {"name": "alpha", "description": "first\nline", "prompt": "SECRET-BODY"},
            {"name": "beta", "description": ""},
            {"name": "gamma"},
        ]

        section = load_skill.format_skill_list_section(skills, "my_loader", _limits())

        assert section.split("\n") == [
            t("tool.load_skill.list_header"),
            t("tool.load_skill.list_intro", tool="my_loader"),
            "- alpha: first line",
            "- beta",
            "- gamma",
        ]
        assert "SECRET-BODY" not in section
        assert "my_loader" in t("tool.load_skill.list_intro", tool="my_loader")

    def test_description_truncated(self) -> None:
        """説明が上限を超えたら切り詰めて `…` を付ける。"""
        skills = [{"name": "a", "description": "x" * 250}]

        default = load_skill.format_skill_list_section(skills, "tool", _limits())
        custom = load_skill.format_skill_list_section(
            skills, "tool", _limits(max_description_chars=5)
        )

        assert f"- a: {'x' * 200}…" in default.split("\n")
        assert "- a: xxxxx…" in custom.split("\n")

    def test_description_at_limit_not_truncated(self) -> None:
        """ちょうど上限の長さなら切り詰めない。"""
        skills = [{"name": "a", "description": "x" * 5}]
        section = load_skill.format_skill_list_section(
            skills, "tool", _limits(max_description_chars=5)
        )
        assert "- a: xxxxx" in section.split("\n")

    def test_max_skills_default(self) -> None:
        """既定では 50 件まで載せ、残りは省略案内を付ける。"""
        skills = [{"name": f"s{i:03d}"} for i in range(53)]

        lines = load_skill.format_skill_list_section(skills, "tool", _limits()).split("\n")

        assert len(lines) == 2 + 50 + 1
        assert lines[-2] == "- s049"
        assert lines[-1] == t("tool.load_skill.list_truncated", count=3, tool="tool")

    def test_max_skills_custom(self) -> None:
        """設定した件数で打ち切る。"""
        skills = [{"name": n} for n in ["a", "b", "c"]]

        lines = load_skill.format_skill_list_section(
            skills, "tool", _limits(max_skills=2)
        ).split("\n")

        assert lines[2:] == ["- a", "- b", t("tool.load_skill.list_truncated", count=1, tool="tool")]

    def test_max_list_chars(self) -> None:
        """各行の合計文字数が上限を超える行の手前で打ち切る（後ろの短い行も載せない）。"""
        skills = [{"name": "aaaa"}, {"name": "bbbbbbbbbb"}, {"name": "c"}]
        # "- aaaa" = 6 文字、"- bbbbbbbbbb" = 12 文字
        lines = load_skill.format_skill_list_section(
            skills, "tool", _limits(max_list_chars=17)
        ).split("\n")

        assert lines[2:] == ["- aaaa", t("tool.load_skill.list_truncated", count=2, tool="tool")]

    def test_max_list_chars_default(self) -> None:
        """既定の 4000 文字で打ち切る。"""
        skills = [{"name": f"s{i:03d}", "description": "x" * 195} for i in range(30)]
        # 1 行 = "- sNNN: " (8) + 195 = 203 文字 → 4000 // 203 = 19 行
        lines = load_skill.format_skill_list_section(skills, "tool", _limits()).split("\n")

        assert len(lines) == 2 + 19 + 1
        assert lines[-1] == t("tool.load_skill.list_truncated", count=11, tool="tool")

    def test_no_truncation_notice_when_all_listed(self) -> None:
        """全件載ったら省略案内は付けない。"""
        section = load_skill.format_skill_list_section([{"name": "a"}], "tool", _limits())
        assert t("tool.load_skill.list_truncated", count=0, tool="tool") not in section
        assert section.split("\n")[-1] == "- a"


class TestBuildSkillListSection:
    async def test_uses_enabled_skills(self, repo: MagicMock) -> None:
        """リポジトリの `get_enabled()` から組み立て、YAML の上限を使う。"""
        repo.get_enabled.return_value = [{"name": "a"}, {"name": "b"}]

        section = await load_skill.build_skill_list_section(
            "llm_load_skill", {"type": "llm_load_skill", "max_skills": 1}
        )

        repo.get_enabled.assert_awaited_once_with()
        assert "- a" in section.split("\n")
        assert "- b" not in section.split("\n")

    async def test_empty(self, repo: MagicMock) -> None:
        """有効なスキルが無ければ空文字列。"""
        assert await load_skill.build_skill_list_section("llm_load_skill", {}) == ""

    async def test_repository_failure_returns_empty(self, repo: MagicMock) -> None:
        """取得に失敗しても例外にせず空文字列を返す。"""
        repo.get_enabled.side_effect = RuntimeError("down")
        assert await load_skill.build_skill_list_section("llm_load_skill", {}) == ""

    async def test_not_cached(self, repo: MagicMock) -> None:
        """呼ぶたびに取得する。"""
        repo.get_enabled.return_value = [{"name": "a"}]
        await load_skill.build_skill_list_section("llm_load_skill", {})
        repo.get_enabled.return_value = [{"name": "b"}]
        section = await load_skill.build_skill_list_section("llm_load_skill", {})

        assert "- b" in section.split("\n")
        assert repo.get_enabled.await_count == 2


class TestLoaderIntegration:
    @pytest.fixture
    def llm_tool_loader(self):
        """設定を差し替えて llm_tool_loader をロードする。"""
        cfg = MagicMock()
        cfg.paths.tool_root = Path("/fake/tools")
        cfg.env.config_root = Path("/fake/config")
        with patch.dict(
            sys.modules, {"lilla_core.core.config": MagicMock(get_config=lambda: cfg)}
        ):
            sys.modules.pop("lilla_core.loaders.llm_tool_loader", None)
            import lilla_core.loaders.llm_tool_loader as loaded
            yield loaded
            sys.modules.pop("lilla_core.loaders.llm_tool_loader", None)

    def _write_yaml(self, tmp_path: Path, body: str) -> Path:
        tools_dir = tmp_path / "config_root" / "tools"
        tools_dir.mkdir(parents=True)
        (tools_dir / "llm_load_skill.yaml").write_text(body, encoding="utf-8")
        return tmp_path / "config_root"

    def test_loads_by_file_name(self, llm_tool_loader, tmp_path: Path) -> None:
        """`type: llm_load_skill` でロードできる。"""
        from lilla_core.loaders.tool_paths import BUILTIN_TOOLS_ROOT

        config_root = self._write_yaml(tmp_path, "type: llm_load_skill\n")

        tools = llm_tool_loader.load_llm_tools(
            tool_roots=[BUILTIN_TOOLS_ROOT], config_root=config_root
        )

        assert tools["llm_load_skill"]["schema"]["function"]["name"] == "llm_load_skill"
        assert load_skill.is_load_skill_tool(tools["llm_load_skill"])

    def test_invalid_limit_fails_at_load(self, llm_tool_loader, tmp_path: Path) -> None:
        """上限が不正な YAML はロード時（起動時）に ValueError で落ちる。"""
        config_root = self._write_yaml(
            tmp_path, "type: lilla_core.builtin_tools.llm_load_skill\nmax_list_chars: 0\n"
        )

        with pytest.raises(ValueError, match="max_list_chars"):
            llm_tool_loader.load_llm_tools(tool_roots=[], config_root=config_root)
