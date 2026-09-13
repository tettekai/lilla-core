"""llm_tool_loader.py のテスト。"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------


def _write_yaml(directory: Path, name: str, content: str) -> Path:
    p = directory / name
    p.write_text(content, encoding="utf-8")
    return p


def _write_py(directory: Path, name: str, schema: dict, execute_return: str = "ok") -> Path:
    """SCHEMA と execute を持つ最小 LLM ツールモジュールを書き出す。"""
    directory.mkdir(parents=True, exist_ok=True)
    p = directory / name
    schema_repr = repr(schema)
    p.write_text(
        f"SCHEMA = {schema_repr}\n\nasync def execute(input, context):\n    return {repr(execute_return)}\n",
        encoding="utf-8",
    )
    return p


# ---------------------------------------------------------------------------
# TestLoadLlmTools
# ---------------------------------------------------------------------------


class TestLoadLlmTools:
    @pytest.fixture()
    def config_root(self, llm_tool_loader, tmp_path: Path) -> Path:
        cr = tmp_path / "config_root"
        (cr / "tools").mkdir(parents=True)
        return cr

    @pytest.fixture()
    def tool_root(self, llm_tool_loader, tmp_path: Path) -> Path:
        return tmp_path / "tool_root"

    def test_returns_empty_when_no_yaml(
        self, llm_tool_loader, tool_root: Path, config_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """llm_*.yaml が存在しないとき空 dict を返す。"""
        result = llm_tool_loader.load_llm_tools(tool_roots=[tool_root], config_root=config_root)
        assert result == {}

    def test_skips_yaml_without_type(
        self, llm_tool_loader, tool_root: Path, config_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """type フィールドなし YAML はスキップされる。"""
        _write_yaml(config_root / "tools", "llm_no_type.yaml", "description: test\n")
        result = llm_tool_loader.load_llm_tools(tool_roots=[tool_root], config_root=config_root)
        assert result == {}

    def test_skips_when_tool_file_not_found(
        self, llm_tool_loader, tool_root: Path, config_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """対応する .py が見つからない → スキップ。"""
        _write_yaml(config_root / "tools", "llm_obsidian_write.yaml", "type: llm_obsidian_write\n")
        result = llm_tool_loader.load_llm_tools(tool_roots=[tool_root], config_root=config_root)
        assert result == {}

    def test_loads_tool_with_schema_and_execute(
        self, llm_tool_loader, tool_root: Path, config_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SCHEMA と execute を持つツールを正しくロードする。"""
        schema = {"name": "obsidian_write", "description": "デフォルト説明", "input_schema": {}}
        _write_py(tool_root / "obsidian", "llm_obsidian_write.py", schema)
        _write_yaml(config_root / "tools", "llm_obsidian_write.yaml", "type: llm_obsidian_write\n")

        result = llm_tool_loader.load_llm_tools(tool_roots=[tool_root], config_root=config_root)

        assert "llm_obsidian_write" in result
        assert result["llm_obsidian_write"]["schema"]["name"] == "obsidian_write"
        assert callable(result["llm_obsidian_write"]["execute"])

    def test_yaml_description_overrides_schema_description(
        self, llm_tool_loader, tool_root: Path, config_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """YAML の description フィールドが SCHEMA["description"] を上書きすること。"""
        schema = {"name": "obsidian_write", "description": "デフォルト説明", "input_schema": {}}
        _write_py(tool_root / "obsidian", "llm_obsidian_write.py", schema)
        _write_yaml(
            config_root / "tools",
            "llm_obsidian_write.yaml",
            "type: llm_obsidian_write\ndescription: '上書き済み説明'\n",
        )

        result = llm_tool_loader.load_llm_tools(tool_roots=[tool_root], config_root=config_root)

        assert result["llm_obsidian_write"]["schema"]["description"] == "上書き済み説明"

    def test_yaml_without_description_keeps_schema_description(
        self, llm_tool_loader, tool_root: Path, config_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """YAML に description がない場合は SCHEMA のデフォルト説明が維持される。"""
        schema = {"name": "obsidian_write", "description": "デフォルト説明", "input_schema": {}}
        _write_py(tool_root / "obsidian", "llm_obsidian_write.py", schema)
        _write_yaml(config_root / "tools", "llm_obsidian_write.yaml", "type: llm_obsidian_write\n")

        result = llm_tool_loader.load_llm_tools(tool_roots=[tool_root], config_root=config_root)

        assert result["llm_obsidian_write"]["schema"]["description"] == "デフォルト説明"

    def test_supported_client_type_stored_in_entry(
        self, llm_tool_loader, tool_root: Path, config_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """YAML の supported_client_type がエントリに保持される。"""
        schema = {"name": "media_change", "description": "テスト", "input_schema": {}}
        _write_py(tool_root / "media", "llm_media_change.py", schema)
        _write_yaml(
            config_root / "tools",
            "llm_media_change.yaml",
            "type: llm_media_change\nsupported_client_type: lilla-client\n",
        )
        result = llm_tool_loader.load_llm_tools(tool_roots=[tool_root], config_root=config_root)
        assert result["llm_media_change"]["supported_client_type"] == "lilla-client"

    def test_default_supported_client_type_is_all(
        self, llm_tool_loader, tool_root: Path, config_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """YAML に supported_client_type がない場合は 'all' になる。"""
        schema = {"name": "obsidian_write", "description": "テスト", "input_schema": {}}
        _write_py(tool_root / "obsidian", "llm_obsidian_write.py", schema)
        _write_yaml(config_root / "tools", "llm_obsidian_write.yaml", "type: llm_obsidian_write\n")
        result = llm_tool_loader.load_llm_tools(tool_roots=[tool_root], config_root=config_root)
        assert result["llm_obsidian_write"]["supported_client_type"] == "all"

    def test_function_name_overridden_with_yaml_stem(
        self, llm_tool_loader, tool_root: Path, config_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """schema.function.name が YAML ファイル名（stem）で上書きされる。"""
        schema = {
            "type": "function",
            "function": {"name": "original_name", "description": "テスト"},
        }
        _write_py(tool_root / "obsidian", "llm_obsidian_write.py", schema)
        _write_yaml(config_root / "tools", "llm_obsidian_write.yaml", "type: llm_obsidian_write\n")

        result = llm_tool_loader.load_llm_tools(tool_roots=[tool_root], config_root=config_root)

        assert result["llm_obsidian_write"]["schema"]["function"]["name"] == "llm_obsidian_write"

    def test_does_not_mutate_original_function_dict(
        self, llm_tool_loader, tool_root: Path, config_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """function.name 上書き時にモジュールの SCHEMA['function'] を変更しない。"""
        schema = {
            "type": "function",
            "function": {"name": "original_name", "description": "テスト"},
        }
        py_file = _write_py(tool_root / "obsidian", "llm_obsidian_write.py", schema)
        _write_yaml(config_root / "tools", "llm_obsidian_write.yaml", "type: llm_obsidian_write\n")

        llm_tool_loader.load_llm_tools(tool_roots=[tool_root], config_root=config_root)

        import importlib.util
        spec = importlib.util.spec_from_file_location("llm_obsidian_write_check2", str(py_file))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert mod.SCHEMA["function"]["name"] == "original_name"

    def test_does_not_mutate_original_schema(
        self, llm_tool_loader, tool_root: Path, config_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """description 上書き時にモジュールの SCHEMA オリジナルを変更しない。"""
        schema = {"name": "obsidian_write", "description": "オリジナル", "input_schema": {}}
        py_file = _write_py(tool_root / "obsidian", "llm_obsidian_write.py", schema)
        _write_yaml(
            config_root / "tools",
            "llm_obsidian_write.yaml",
            "type: llm_obsidian_write\ndescription: '上書き'\n",
        )

        result = llm_tool_loader.load_llm_tools(tool_roots=[tool_root], config_root=config_root)

        # ロード済みの schema は上書きされている
        assert result["llm_obsidian_write"]["schema"]["description"] == "上書き"
        # 元のモジュールを再ロードして SCHEMA が変更されていないか確認
        import importlib.util
        spec = importlib.util.spec_from_file_location("llm_obsidian_write_check", str(py_file))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert mod.SCHEMA["description"] == "オリジナル"


class TestLoadLlmToolsFromExtensionRoots:
    """拡張の `tool_roots()` から追加されたルートの探索と、ルート外へ出るパスの拒否。"""

    @pytest.fixture()
    def config_root(self, llm_tool_loader, tmp_path: Path) -> Path:
        cr = tmp_path / "config_root"
        (cr / "tools").mkdir(parents=True)
        return cr

    @pytest.fixture()
    def core_root(self, llm_tool_loader, tmp_path: Path) -> Path:
        root = tmp_path / "core_tools"
        root.mkdir()
        return root

    @pytest.fixture()
    def pack_root(self, llm_tool_loader, tmp_path: Path) -> Path:
        root = tmp_path / "pack_tools"
        root.mkdir()
        return root

    def test_loads_tool_found_in_extension_root(
        self, llm_tool_loader, core_root: Path, pack_root: Path, config_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """コアのルートに無いツールでも、拡張の追加ルートから読める。"""
        schema = {"name": "extra", "description": "説明", "input_schema": {}}
        _write_py(pack_root, "llm_extra.py", schema)
        _write_yaml(config_root / "tools", "llm_extra.yaml", "type: llm_extra\n")

        result = llm_tool_loader.load_llm_tools(
            tool_roots=[core_root, pack_root], config_root=config_root
        )

        assert "llm_extra" in result

    def test_extension_root_needs_no_extra_configuration(
        self, llm_tool_loader, core_root: Path, pack_root: Path, config_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """追加ルートは探索ルートに含まれた時点でロード対象になる（別途の許可設定は不要）。"""
        schema = {"name": "extra", "description": "説明", "input_schema": {}}
        _write_py(pack_root, "llm_extra.py", schema)
        _write_yaml(config_root / "tools", "llm_extra.yaml", "type: llm_extra\n")

        result = llm_tool_loader.load_llm_tools(
            tool_roots=[pack_root], config_root=config_root
        )

        assert "llm_extra" in result

    def test_symlink_escaping_the_roots_is_rejected(
        self, llm_tool_loader, core_root: Path, config_root: Path, tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """ルート内のシンボリックリンクが外を指していれば、解決後のパスで弾いてロードしない。"""
        schema = {"name": "extra", "description": "説明", "input_schema": {}}
        outside = tmp_path / "outside"
        _write_py(outside, "llm_extra.py", schema)
        (core_root / "llm_extra.py").symlink_to(outside / "llm_extra.py")
        _write_yaml(config_root / "tools", "llm_extra.yaml", "type: llm_extra\n")

        result = llm_tool_loader.load_llm_tools(
            tool_roots=[core_root], config_root=config_root
        )

        assert result == {}

    def test_type_with_parent_segments_is_rejected(
        self, llm_tool_loader, core_root: Path, config_root: Path, tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """YAML の `type` に `..` を含めてルートの外を指しても、解決後のパスで弾く。"""
        schema = {"name": "extra", "description": "説明", "input_schema": {}}
        _write_py(tmp_path / "outside", "llm_extra.py", schema)
        _write_yaml(
            config_root / "tools", "llm_extra.yaml", "type: ../outside/llm_extra\n"
        )

        result = llm_tool_loader.load_llm_tools(
            tool_roots=[core_root], config_root=config_root
        )

        assert result == {}

    def test_same_tool_name_in_two_roots_raises(
        self, llm_tool_loader, core_root: Path, pack_root: Path, config_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """同名ツールが複数ルートにあるとロード時に落ちる。"""
        schema = {"name": "extra", "description": "説明", "input_schema": {}}
        _write_py(core_root, "llm_extra.py", schema)
        _write_py(pack_root, "llm_extra.py", schema)
        _write_yaml(config_root / "tools", "llm_extra.yaml", "type: llm_extra\n")

        with pytest.raises(ValueError, match="multiple tool roots"):
            llm_tool_loader.load_llm_tools(
                tool_roots=[core_root, pack_root], config_root=config_root
            )


class TestResolveSelfToolFile:
    """_resolve_self_tool_file の単体テスト。"""

    def test_returns_py_path_when_exists(self, llm_tool_loader, tmp_path: Path) -> None:
        """YAML と同じ stem の .py が存在する場合、そのパスを返す。"""
        yaml_path = tmp_path / "llm_foo.yaml"
        yaml_path.write_text("type: self\n", encoding="utf-8")
        py_path = tmp_path / "llm_foo.py"
        py_path.write_text("SCHEMA = {}\n", encoding="utf-8")

        result = llm_tool_loader._resolve_self_tool_file(yaml_path)
        assert result == py_path

    def test_returns_none_when_not_exists(self, llm_tool_loader, tmp_path: Path) -> None:
        """同名の .py が存在しない場合、None を返す。"""
        yaml_path = tmp_path / "llm_foo.yaml"
        yaml_path.write_text("type: self\n", encoding="utf-8")

        result = llm_tool_loader._resolve_self_tool_file(yaml_path)
        assert result is None


class TestLoadLlmToolsSelfType:
    """type: self の統合テスト。"""

    @pytest.fixture()
    def config_root(self, llm_tool_loader, tmp_path: Path) -> Path:
        cr = tmp_path / "config_root"
        (cr / "tools").mkdir(parents=True)
        return cr

    @pytest.fixture()
    def tool_root(self, llm_tool_loader, tmp_path: Path) -> Path:
        return tmp_path / "tool_root"

    def test_loads_self_type_from_same_directory(
        self, llm_tool_loader, tool_root: Path, config_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """type: self のとき YAML と同じディレクトリ・同名の .py がロードされる。"""
        schema = {"name": "foo", "description": "自分用ツール", "input_schema": {}}
        tools_dir = config_root / "tools"
        _write_py(tools_dir, "llm_foo.py", schema)
        _write_yaml(tools_dir, "llm_foo.yaml", "type: self\n")

        result = llm_tool_loader.load_llm_tools(tool_roots=[tool_root], config_root=config_root)

        assert "llm_foo" in result
        assert result["llm_foo"]["schema"]["name"] == "foo"
        assert callable(result["llm_foo"]["execute"])

    def test_self_type_skipped_when_py_missing(
        self, llm_tool_loader, tool_root: Path, config_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """type: self で対応する .py が存在しないとき例外を投げずスキップされる。"""
        _write_yaml(config_root / "tools", "llm_missing.yaml", "type: self\n")

        result = llm_tool_loader.load_llm_tools(tool_roots=[tool_root], config_root=config_root)

        assert result == {}

    def test_existing_type_pattern_still_works(
        self, llm_tool_loader, tool_root: Path, config_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """type: <既存ツール名> パターンがリグレッションなく動作する。"""
        schema = {"name": "obsidian_write", "description": "デフォルト", "input_schema": {}}
        _write_py(tool_root / "obsidian", "llm_obsidian_write.py", schema)
        _write_yaml(
            config_root / "tools", "llm_obsidian_write.yaml", "type: llm_obsidian_write\n"
        )

        result = llm_tool_loader.load_llm_tools(tool_roots=[tool_root], config_root=config_root)

        assert "llm_obsidian_write" in result
        assert result["llm_obsidian_write"]["schema"]["name"] == "obsidian_write"


# ---------------------------------------------------------------------------
# TestBuildToolsParam
# ---------------------------------------------------------------------------


class TestBuildToolsParam:
    def test_returns_list_of_schemas(self, llm_tool_loader) -> None:
        """llm_tools の各 schema をリストにして返す。"""
        schema_a = {"name": "tool_a"}
        schema_b = {"name": "tool_b"}
        llm_tools = {
            "tool_a": {"schema": schema_a, "execute": AsyncMock()},
            "tool_b": {"schema": schema_b, "execute": AsyncMock()},
        }
        result = llm_tool_loader.build_tools_param(llm_tools)
        assert result == [schema_a, schema_b]

    def test_returns_empty_list_for_empty_tools(self, llm_tool_loader) -> None:
        """空 dict を渡すと空リストが返る。"""
        assert llm_tool_loader.build_tools_param({}) == []

    def test_filters_lilla_client_only_tool_for_discord(self, llm_tool_loader) -> None:
        """supported_client_type=lilla-client のツールは discord クライアントに含まれない。"""
        schema_a = {"name": "tool_a"}
        schema_b = {"name": "media_change"}
        llm_tools = {
            "tool_a": {"schema": schema_a, "execute": AsyncMock(), "supported_client_type": "all"},
            "llm_media_change": {"schema": schema_b, "execute": AsyncMock(), "supported_client_type": "lilla-client"},
        }
        result = llm_tool_loader.build_tools_param(llm_tools, client_type="discord")
        assert result == [schema_a]

    def test_includes_lilla_client_only_tool_for_lilla_client(self, llm_tool_loader) -> None:
        """supported_client_type=lilla-client のツールは lilla-client に含まれる。"""
        schema_a = {"name": "tool_a"}
        schema_b = {"name": "media_change"}
        llm_tools = {
            "tool_a": {"schema": schema_a, "execute": AsyncMock(), "supported_client_type": "all"},
            "llm_media_change": {"schema": schema_b, "execute": AsyncMock(), "supported_client_type": "lilla-client"},
        }
        result = llm_tool_loader.build_tools_param(llm_tools, client_type="lilla-client")
        assert result == [schema_a, schema_b]

    def test_allowed_names_none_is_no_filter(self, llm_tool_loader) -> None:
        """allowed_names=None のとき client_type フィルタのみ適用される。"""
        schema_a = {"name": "tool_a"}
        schema_b = {"name": "tool_b"}
        llm_tools = {
            "tool_a": {"schema": schema_a, "execute": AsyncMock(), "supported_client_type": "all"},
            "tool_b": {"schema": schema_b, "execute": AsyncMock(), "supported_client_type": "all"},
        }
        result = llm_tool_loader.build_tools_param(llm_tools, allowed_names=None)
        assert result == [schema_a, schema_b]

    def test_allowed_names_filters_by_key(self, llm_tool_loader) -> None:
        """allowed_names で指定したキーのツールのみ返る。"""
        schema_a = {"name": "tool_a"}
        schema_b = {"name": "tool_b"}
        schema_c = {"name": "tool_c"}
        llm_tools = {
            "tool_a": {"schema": schema_a, "execute": AsyncMock(), "supported_client_type": "all"},
            "tool_b": {"schema": schema_b, "execute": AsyncMock(), "supported_client_type": "all"},
            "tool_c": {"schema": schema_c, "execute": AsyncMock(), "supported_client_type": "all"},
        }
        result = llm_tool_loader.build_tools_param(
            llm_tools, allowed_names=["tool_a", "tool_c"]
        )
        assert result == [schema_a, schema_c]

    def test_allowed_names_empty_returns_empty(self, llm_tool_loader) -> None:
        """allowed_names=[] のとき空リストが返る。"""
        schema_a = {"name": "tool_a"}
        llm_tools = {
            "tool_a": {"schema": schema_a, "execute": AsyncMock(), "supported_client_type": "all"},
        }
        result = llm_tool_loader.build_tools_param(llm_tools, allowed_names=[])
        assert result == []

    def test_allowed_names_and_client_type_both_applied(self, llm_tool_loader) -> None:
        """allowed_names と supported_client_type の両方が適用される。"""
        schema_a = {"name": "tool_a"}
        schema_b = {"name": "tool_b"}
        llm_tools = {
            "tool_a": {"schema": schema_a, "execute": AsyncMock(), "supported_client_type": "all"},
            "tool_b": {"schema": schema_b, "execute": AsyncMock(), "supported_client_type": "lilla-client"},
        }
        # tool_b は allowed だが client_type=discord では除外される
        result = llm_tool_loader.build_tools_param(
            llm_tools, client_type="discord", allowed_names=["tool_a", "tool_b"]
        )
        assert result == [schema_a]



# ---------------------------------------------------------------------------
# TestExecuteToolCall
# ---------------------------------------------------------------------------


class TestExecuteToolCall:
    async def test_calls_execute_by_schema_name(self, llm_tool_loader) -> None:
        """LLM が返した tool_name（schema.function.name）でツールを検索して execute を呼ぶ。"""
        mock_execute = AsyncMock(return_value="PR作成完了")
        llm_tools = {
            "llm_obsidian_write": {
                "schema": {"function": {"name": "obsidian_write"}},
                "execute": mock_execute,
            }
        }
        result = await llm_tool_loader.execute_tool_call(
            "obsidian_write", {"notes": []}, llm_tools, {}
        )
        assert result == "PR作成完了"
        mock_execute.assert_called_once()
        called_input, called_context = mock_execute.call_args[0]
        assert called_input == {"notes": []}
        # call_tool と _tool_call_depth は自動注入される
        assert callable(called_context["call_tool"])
        assert called_context[llm_tool_loader._TOOL_CALL_DEPTH_KEY] == 1

    def test_returns_error_dict_when_tool_not_found(self, llm_tool_loader) -> None:
        """ツールが見つからないとき例外を上げずエラー情報を含む dict を返す。"""
        import asyncio
        result = asyncio.get_event_loop().run_until_complete(
            llm_tool_loader.execute_tool_call("unknown_tool", {}, {}, {})
        )
        assert result["success"] is False
        assert result["error"] is not None
        assert "unknown_tool" in result["error"]

    async def test_calls_execute_by_dict_key(self, llm_tool_loader) -> None:
        """llm_tools のキー名が tool_name と一致する場合もロードできる。"""
        mock_execute = AsyncMock(return_value="ok")
        llm_tools = {
            "llm_obsidian_write": {
                "schema": {"function": {"name": "obsidian_write"}},
                "execute": mock_execute,
            }
        }
        result = await llm_tool_loader.execute_tool_call(
            "llm_obsidian_write", {}, llm_tools, {}
        )
        assert result == "ok"

    async def test_returns_error_dict_on_execute_exception(self, llm_tool_loader) -> None:
        """execute が例外を送出したとき、例外を伝播させずエラー情報を含む dict を返す。"""
        failing_execute = AsyncMock(side_effect=RuntimeError("API error"))
        llm_tools = {
            "llm_obsidian_write": {
                "schema": {"function": {"name": "obsidian_write"}},
                "execute": failing_execute,
            }
        }
        result = await llm_tool_loader.execute_tool_call(
            "obsidian_write", {}, llm_tools, {}
        )
        assert result["success"] is False
        assert result["error"] is not None

    async def test_tool_config_merged_into_context(self, llm_tool_loader) -> None:
        """tool_config のキーが context にマージされて execute に渡される。"""
        mock_execute = AsyncMock(return_value="ok")
        llm_tools = {
            "llm_calendar_get": {
                "schema": {"function": {"name": "calendar_get"}},
                "execute": mock_execute,
                "tool_config": {"calendar_ids": ["cal1", "cal2"], "type": "llm_calendar_get"},
            }
        }
        result = await llm_tool_loader.execute_tool_call(
            "calendar_get", {}, llm_tools, {}
        )
        assert result == "ok"
        called_context = mock_execute.call_args[0][1]
        assert called_context["calendar_ids"] == ["cal1", "cal2"]

    async def test_tool_config_takes_precedence_over_context(self, llm_tool_loader) -> None:
        """ツール自身の tool_config は、呼び出し元(親)由来の context を上書きする。

        ネストしたツール呼び出しでは context に親ツールの tool_config が
        マージ済みで渡ってくる。子ツール自身の設定値が優先されなければ
        ならないため、tool_config が context より後にマージされる。
        """
        mock_execute = AsyncMock(return_value="ok")
        llm_tools = {
            "llm_calendar_get": {
                "schema": {"function": {"name": "calendar_get"}},
                "execute": mock_execute,
                "tool_config": {"calendar_ids": ["from_config"]},
            }
        }
        result = await llm_tool_loader.execute_tool_call(
            "calendar_get", {}, llm_tools, {"calendar_ids": ["from_context"]}
        )
        assert result == "ok"
        called_context = mock_execute.call_args[0][1]
        assert called_context["calendar_ids"] == ["from_config"]

    async def test_no_tool_config_passes_context_unchanged(self, llm_tool_loader) -> None:
        """tool_config がないとき context の内容がそのまま渡される。"""
        mock_execute = AsyncMock(return_value="ok")
        llm_tools = {
            "llm_obsidian_write": {
                "schema": {"function": {"name": "obsidian_write"}},
                "execute": mock_execute,
            }
        }
        obj = object()
        ctx = {"some_client": obj}
        await llm_tool_loader.execute_tool_call("obsidian_write", {}, llm_tools, ctx)
        called_context = mock_execute.call_args[0][1]
        assert called_context["some_client"] is obj


class TestExecuteToolCallCallToolInjection:
    """context["call_tool"] 注入と深さガードのテスト。"""

    async def test_tool_can_call_another_tool_via_call_tool(self, llm_tool_loader) -> None:
        """ツールAが context["call_tool"] 経由でツールBを呼び、結果がAに反映される。"""
        async def execute_b(input, context):
            return {"success": True, "data": f"B={input['x']}", "summary": ""}

        async def execute_a(input, context):
            b_result = await context["call_tool"]("tool_b", {"x": input["x"] * 2})
            return {"success": True, "data": f"A->{b_result['data']}", "summary": ""}

        llm_tools = {
            "tool_a": {
                "schema": {"function": {"name": "tool_a"}},
                "execute": execute_a,
                "tool_config": {},
            },
            "tool_b": {
                "schema": {"function": {"name": "tool_b"}},
                "execute": execute_b,
                "tool_config": {},
            },
        }
        result = await llm_tool_loader.execute_tool_call(
            "tool_a", {"x": 3}, llm_tools, {}
        )
        assert result["success"] is True
        assert result["data"] == "A->B=6"

    async def test_recursive_call_allowed_until_depth_limit(self, llm_tool_loader) -> None:
        """同名ツールを call_tool で再帰的に呼び、上限深さまでは正常に実行できる。"""
        async def execute_a(input, context):
            n = input["n"]
            if n <= 0:
                return {"success": True, "data": "leaf", "summary": ""}
            child = await context["call_tool"]("tool_a", {"n": n - 1})
            return {"success": child["success"], "data": child["data"], "summary": ""}

        llm_tools = {
            "tool_a": {
                "schema": {"function": {"name": "tool_a"}},
                "execute": execute_a,
                "tool_config": {},
            }
        }
        # 深さちょうど MAX_TOOL_CALL_DEPTH: 呼び出しチェーン内で最深時 depth==MAX。
        # 各呼び出しは context[depth]==MAX 以上ならブロックされるので、
        # n=MAX-1 まで再帰できる。
        depth_limit = llm_tool_loader.MAX_TOOL_CALL_DEPTH
        result = await llm_tool_loader.execute_tool_call(
            "tool_a", {"n": depth_limit - 1}, llm_tools, {}
        )
        assert result["success"] is True
        assert result["data"] == "leaf"

    async def test_recursive_call_blocked_beyond_depth_limit(self, llm_tool_loader) -> None:
        """MAX_TOOL_CALL_DEPTH を超える再帰チェーンは深さ上限エラーになる。"""
        async def execute_a(input, context):
            n = input["n"]
            if n <= 0:
                return {"success": True, "data": "leaf", "summary": ""}
            child = await context["call_tool"]("tool_a", {"n": n - 1})
            return child

        llm_tools = {
            "tool_a": {
                "schema": {"function": {"name": "tool_a"}},
                "execute": execute_a,
                "tool_config": {},
            }
        }
        # n を十分大きくして必ず上限を超えさせる
        result = await llm_tool_loader.execute_tool_call(
            "tool_a",
            {"n": llm_tool_loader.MAX_TOOL_CALL_DEPTH + 5},
            llm_tools,
            {},
        )
        assert result["success"] is False
        assert "深さ" in result["error"] or "上限" in result["error"]

    async def test_cyclic_chain_blocked_beyond_depth_limit(self, llm_tool_loader) -> None:
        """A→B→A→B... の循環チェーンでも深さ上限で止まる。"""
        async def execute_a(input, context):
            return await context["call_tool"]("tool_b", {})

        async def execute_b(input, context):
            return await context["call_tool"]("tool_a", {})

        llm_tools = {
            "tool_a": {
                "schema": {"function": {"name": "tool_a"}},
                "execute": execute_a,
                "tool_config": {},
            },
            "tool_b": {
                "schema": {"function": {"name": "tool_b"}},
                "execute": execute_b,
                "tool_config": {},
            },
        }
        result = await llm_tool_loader.execute_tool_call(
            "tool_a", {}, llm_tools, {}
        )
        assert result["success"] is False
        assert "深さ" in result["error"] or "上限" in result["error"]

    async def test_existing_tool_not_using_call_tool_still_works(self, llm_tool_loader) -> None:
        """call_tool を使わない既存ツールは従来通り実行される（リグレッションなし）。"""
        mock_execute = AsyncMock(return_value={"success": True, "data": "ok"})
        llm_tools = {
            "llm_foo": {
                "schema": {"function": {"name": "llm_foo"}},
                "execute": mock_execute,
                "tool_config": {"some_key": "some_val"},
            }
        }
        result = await llm_tool_loader.execute_tool_call(
            "llm_foo", {"arg": 1}, llm_tools, {"client": "obj"}
        )
        assert result == {"success": True, "data": "ok"}
        called_input, called_context = mock_execute.call_args[0]
        assert called_input == {"arg": 1}
        assert called_context["some_key"] == "some_val"
        assert called_context["client"] == "obj"
        # 新規注入されたキーも存在する
        assert callable(called_context["call_tool"])
        assert called_context[llm_tool_loader._TOOL_CALL_DEPTH_KEY] == 1


class TestExecuteToolCallCache:
    """execute_tool_call の cache.mode 連携テスト。"""

    async def test_saves_to_cache_when_enable_and_success(
        self, llm_tool_loader, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """cache.mode=enable かつ success=True のとき MongoDB に保存される。"""
        mock_repo = MagicMock()
        mock_repo.set = AsyncMock()
        mock_module = MagicMock(get_tool_cache_repo=lambda: mock_repo)
        sys.modules["lilla_core.repository.tool_cache_repository"] = mock_module

        mock_execute = AsyncMock(
            return_value={"success": True, "data": "寿司、焼肉、カレー", "summary": ""}
        )
        llm_tools = {
            "llm_personal_info": {
                "schema": {"function": {"name": "llm_get_fixed_info"}},
                "execute": mock_execute,
                "tool_config": {
                    "cache": {"mode": "enable", "expiration_minutes": 30},
                },
            }
        }
        await llm_tool_loader.execute_tool_call(
            "llm_get_fixed_info", {"info_type": "FAVORITE_FOODS"}, llm_tools, {}
        )
        mock_repo.set.assert_called_once()
        call_args = mock_repo.set.call_args
        assert call_args.args[0] == "llm_personal_info"
        assert call_args.args[1] == '{"info_type": "FAVORITE_FOODS"}'
        assert call_args.args[2] == "寿司、焼肉、カレー"
        assert call_args.args[3] == 30

    async def test_does_not_save_when_mode_disable(self, llm_tool_loader) -> None:
        """cache.mode=disable のときキャッシュ保存しない。"""
        mock_repo = MagicMock()
        mock_repo.set = AsyncMock()
        mock_module = MagicMock(get_tool_cache_repo=lambda: mock_repo)
        sys.modules["lilla_core.repository.tool_cache_repository"] = mock_module

        mock_execute = AsyncMock(
            return_value={"success": True, "data": "data", "summary": ""}
        )
        llm_tools = {
            "llm_foo": {
                "schema": {"function": {"name": "llm_foo"}},
                "execute": mock_execute,
                "tool_config": {"cache": {"mode": "disable"}},
            }
        }
        await llm_tool_loader.execute_tool_call("llm_foo", {}, llm_tools, {})
        mock_repo.set.assert_not_called()

    async def test_does_not_save_when_no_cache_config(self, llm_tool_loader) -> None:
        """cache 設定がない（mode 未設定）ツールはキャッシュ保存しない。"""
        mock_repo = MagicMock()
        mock_repo.set = AsyncMock()
        mock_module = MagicMock(get_tool_cache_repo=lambda: mock_repo)
        sys.modules["lilla_core.repository.tool_cache_repository"] = mock_module

        mock_execute = AsyncMock(
            return_value={"success": True, "data": "data", "summary": ""}
        )
        llm_tools = {
            "llm_foo": {
                "schema": {"function": {"name": "llm_foo"}},
                "execute": mock_execute,
                "tool_config": {},
            }
        }
        await llm_tool_loader.execute_tool_call("llm_foo", {}, llm_tools, {})
        mock_repo.set.assert_not_called()

    async def test_does_not_save_when_result_not_success(self, llm_tool_loader) -> None:
        """ツール失敗時はキャッシュ保存しない。"""
        mock_repo = MagicMock()
        mock_repo.set = AsyncMock()
        mock_module = MagicMock(get_tool_cache_repo=lambda: mock_repo)
        sys.modules["lilla_core.repository.tool_cache_repository"] = mock_module

        mock_execute = AsyncMock(
            return_value={"success": False, "data": None, "error": "fail"}
        )
        llm_tools = {
            "llm_foo": {
                "schema": {"function": {"name": "llm_foo"}},
                "execute": mock_execute,
                "tool_config": {"cache": {"mode": "enable"}},
            }
        }
        await llm_tool_loader.execute_tool_call("llm_foo", {}, llm_tools, {})
        mock_repo.set.assert_not_called()

    async def test_args_key_is_sorted_json(self, llm_tool_loader) -> None:
        """enable モードの args_key は sort_keys=True でシリアライズされる。"""
        mock_repo = MagicMock()
        mock_repo.set = AsyncMock()
        mock_module = MagicMock(get_tool_cache_repo=lambda: mock_repo)
        sys.modules["lilla_core.repository.tool_cache_repository"] = mock_module

        mock_execute = AsyncMock(
            return_value={"success": True, "data": "ok"}
        )
        llm_tools = {
            "llm_foo": {
                "schema": {"function": {"name": "llm_foo"}},
                "execute": mock_execute,
                "tool_config": {"cache": {"mode": "enable"}},
            }
        }
        await llm_tool_loader.execute_tool_call(
            "llm_foo", {"b": 2, "a": 1}, llm_tools, {}
        )
        call_args = mock_repo.set.call_args
        assert call_args.args[1] == '{"a": 1, "b": 2}'

    async def test_auto_mode_saves_each_cache_write_entry(self, llm_tool_loader) -> None:
        """cache.mode=auto のとき cache_writes の各エントリが個別キーで保存される。"""
        mock_repo = MagicMock()
        mock_repo.set = AsyncMock()
        mock_module = MagicMock(get_tool_cache_repo=lambda: mock_repo)
        sys.modules["lilla_core.repository.tool_cache_repository"] = mock_module

        mock_execute = AsyncMock(
            return_value={
                "success": True,
                "data": "summary",
                "cache_writes": [
                    {"key": "notes/a.md", "data": "A本文", "expiration_minutes": 60},
                    {"key": "notes/b.md", "data": "B本文"},
                ],
            }
        )
        llm_tools = {
            "llm_obsidian_read": {
                "schema": {"function": {"name": "llm_obsidian_read"}},
                "execute": mock_execute,
                "tool_config": {"cache": {"mode": "auto"}},
            }
        }
        await llm_tool_loader.execute_tool_call(
            "llm_obsidian_read", {"paths": ["notes/a.md", "notes/b.md"]}, llm_tools, {}
        )
        assert mock_repo.set.call_count == 2
        first, second = mock_repo.set.call_args_list
        assert first.args == ("llm_obsidian_read", "notes/a.md", "A本文", 60)
        assert second.args == ("llm_obsidian_read", "notes/b.md", "B本文", 30)

    async def test_auto_mode_no_cache_writes_does_not_error(self, llm_tool_loader) -> None:
        """cache.mode=auto で cache_writes が無い/空でもエラーにならず保存しない。"""
        mock_repo = MagicMock()
        mock_repo.set = AsyncMock()
        mock_module = MagicMock(get_tool_cache_repo=lambda: mock_repo)
        sys.modules["lilla_core.repository.tool_cache_repository"] = mock_module

        # cache_writes 未設定
        mock_execute = AsyncMock(return_value={"success": True, "data": "ok"})
        llm_tools = {
            "llm_foo": {
                "schema": {"function": {"name": "llm_foo"}},
                "execute": mock_execute,
                "tool_config": {"cache": {"mode": "auto"}},
            }
        }
        await llm_tool_loader.execute_tool_call("llm_foo", {}, llm_tools, {})
        mock_repo.set.assert_not_called()

        # cache_writes が空リスト
        mock_execute2 = AsyncMock(
            return_value={"success": True, "data": "ok", "cache_writes": []}
        )
        llm_tools["llm_foo"]["execute"] = mock_execute2
        await llm_tool_loader.execute_tool_call("llm_foo", {}, llm_tools, {})
        mock_repo.set.assert_not_called()

    async def test_cache_writes_removed_from_result(self, llm_tool_loader) -> None:
        """cache_writes は保存後に結果から除去される。"""
        mock_repo = MagicMock()
        mock_repo.set = AsyncMock()
        mock_module = MagicMock(get_tool_cache_repo=lambda: mock_repo)
        sys.modules["lilla_core.repository.tool_cache_repository"] = mock_module

        mock_execute = AsyncMock(
            return_value={
                "success": True,
                "data": "summary",
                "cache_writes": [{"key": "k", "data": "v"}],
            }
        )
        llm_tools = {
            "llm_foo": {
                "schema": {"function": {"name": "llm_foo"}},
                "execute": mock_execute,
                "tool_config": {"cache": {"mode": "auto"}},
            }
        }
        result = await llm_tool_loader.execute_tool_call(
            "llm_foo", {}, llm_tools, {}
        )
        assert "cache_writes" not in result


class TestLoadLlmToolsConfig:
    """load_llm_tools が tool_config を保持するかのテスト。"""

    @pytest.fixture()
    def config_root(self, llm_tool_loader, tmp_path: Path) -> Path:
        cr = tmp_path / "config_root"
        (cr / "tools").mkdir(parents=True)
        return cr

    @pytest.fixture()
    def tool_root(self, llm_tool_loader, tmp_path: Path) -> Path:
        return tmp_path / "tool_root"

    def test_tool_config_stored_in_entry(
        self, llm_tool_loader, tool_root: Path, config_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """load_llm_tools が YAML 全体を tool_config として保持する。"""
        schema = {"name": "calendar_get", "description": "テスト", "input_schema": {}}
        _write_py(tool_root / "calendar", "llm_calendar_get.py", schema)
        _write_yaml(
            config_root / "tools",
            "llm_calendar_get.yaml",
            "type: llm_calendar_get\ncalendar_ids:\n  - cal1\n  - cal2\n",
        )

        result = llm_tool_loader.load_llm_tools(tool_roots=[tool_root], config_root=config_root)

        assert "llm_calendar_get" in result
        tool_config = result["llm_calendar_get"]["tool_config"]
        assert tool_config["calendar_ids"] == ["cal1", "cal2"]
        assert tool_config["type"] == "llm_calendar_get"


class TestValidateNoRuntimeKeyCollision:
    """load_llm_tools 起動時の実行時共通キー衝突検証のテスト。

    検証対象キーは「コア自身のフレームワークキー」と
    「拡張の `tool_context_providers()` が返すキー」の合成なので、
    provider 登録の有無それぞれで衝突検知が正しく働くことを確認する。
    """

    @pytest.fixture()
    def provider_registry(self, llm_tool_loader, monkeypatch: pytest.MonkeyPatch) -> dict:
        """ツール context プロバイダのレジストリを空の dict に差し替えて貸し出す。

        `get_tool_context_providers()` はモジュールグローバルを呼び出し時に
        参照するため、差し替えた dict へ直接登録できる。
        """
        from lilla_core.core import extension

        registry: dict = {}
        monkeypatch.setattr(extension, "_tool_context_providers", registry)
        return registry

    @pytest.fixture()
    def config_root(self, llm_tool_loader, tmp_path: Path) -> Path:
        cr = tmp_path / "config_root"
        (cr / "tools").mkdir(parents=True)
        return cr

    @pytest.fixture()
    def tool_root(self, llm_tool_loader, tmp_path: Path) -> Path:
        return tmp_path / "tool_root"

    def test_raises_when_tool_config_collides_with_runtime_key(
        self, llm_tool_loader, tool_root: Path, config_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """tool_config が実行時共通キーと衝突する場合、起動時に ValueError を送出する。"""
        schema = {"name": "calendar_get", "description": "テスト", "input_schema": {}}
        _write_py(tool_root / "calendar", "llm_calendar_get.py", schema)
        # llm_tools は _RUNTIME_CONTEXT_KEYS に含まれる衝突キー
        _write_yaml(
            config_root / "tools",
            "llm_calendar_get.yaml",
            "type: llm_calendar_get\nllm_tools: broken\n",
        )

        with pytest.raises(ValueError) as exc_info:
            llm_tool_loader.load_llm_tools(tool_roots=[tool_root], config_root=config_root)
        assert "llm_calendar_get" in str(exc_info.value)
        assert "llm_tools" in str(exc_info.value)

    def test_no_error_when_no_collision(
        self, llm_tool_loader, tool_root: Path, config_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """衝突するキーがなければ ValueError を送出せず正常にロードする。"""
        schema = {"name": "calendar_get", "description": "テスト", "input_schema": {}}
        _write_py(tool_root / "calendar", "llm_calendar_get.py", schema)
        _write_yaml(
            config_root / "tools",
            "llm_calendar_get.yaml",
            "type: llm_calendar_get\ncalendar_ids:\n  - cal1\n",
        )

        result = llm_tool_loader.load_llm_tools(tool_roots=[tool_root], config_root=config_root)
        assert "llm_calendar_get" in result

    def test_validate_function_raises_on_collision(
        self, llm_tool_loader, provider_registry
    ) -> None:
        """_validate_no_runtime_key_collision が衝突検知で ValueError を送出する。

        コア自身のキー（client_type）と、provider 登録由来のキー
        （obsidian_client）の双方を検知することを確認する。
        """
        provider_registry["obsidian_client"] = lambda: MagicMock()
        llm_tools = {
            "llm_foo": {
                "tool_config": {"client_type": "x", "obsidian_client": "y", "other": 1},
            }
        }
        with pytest.raises(ValueError) as exc_info:
            llm_tool_loader._validate_no_runtime_key_collision(llm_tools)
        msg = str(exc_info.value)
        assert "client_type" in msg
        assert "obsidian_client" in msg

    def test_validate_function_passes_without_collision(
        self, llm_tool_loader, provider_registry
    ) -> None:
        """衝突がなければ _validate_no_runtime_key_collision は何もしない。"""
        llm_tools = {
            "llm_foo": {"tool_config": {"calendar_ids": [], "prompt": "x"}},
        }
        # 例外が出なければ OK
        llm_tool_loader._validate_no_runtime_key_collision(llm_tools)

    def test_detects_core_keys_without_any_provider(
        self, llm_tool_loader, provider_registry
    ) -> None:
        """provider 未登録（コア単独起動）でもコア自身のキーは検知する。"""
        llm_tools = {"llm_foo": {"tool_config": {"ws_clients": "x"}}}
        with pytest.raises(ValueError) as exc_info:
            llm_tool_loader._validate_no_runtime_key_collision(llm_tools)
        assert "ws_clients" in str(exc_info.value)

    def test_media_base_url_follows_provider_registration(
        self, llm_tool_loader, provider_registry
    ) -> None:
        """media_base_url も特殊枠ではなく provider registry の登録内容に従う。

        未登録（コア単独起動）なら実行時に注入されないので衝突扱いせず、
        拡張が登録したあとは他のプロバイダキーと同様に検知する。
        """
        llm_tools = {"llm_foo": {"tool_config": {"media_base_url": "http://x"}}}
        # 登録前は衝突なし
        llm_tool_loader._validate_no_runtime_key_collision(llm_tools)

        provider_registry["media_base_url"] = lambda: "http://lilla.example"
        with pytest.raises(ValueError) as exc_info:
            llm_tool_loader._validate_no_runtime_key_collision(llm_tools)
        assert "media_base_url" in str(exc_info.value)

    def test_provider_key_is_not_a_collision_when_unregistered(
        self, llm_tool_loader, provider_registry
    ) -> None:
        """provider 未登録なら、そのキーは実行時に注入されないので衝突扱いしない。"""
        llm_tools = {"llm_foo": {"tool_config": {"obsidian_client": "x"}}}
        # 例外が出なければ OK
        llm_tool_loader._validate_no_runtime_key_collision(llm_tools)

    def test_newly_registered_provider_key_becomes_a_collision(
        self, llm_tool_loader, provider_registry
    ) -> None:
        """ハードコードされていない任意のキーでも、provider 登録後は検知する。"""
        llm_tools = {"llm_foo": {"tool_config": {"brand_new_client": "x"}}}
        # 登録前は衝突なし
        llm_tool_loader._validate_no_runtime_key_collision(llm_tools)

        provider_registry["brand_new_client"] = lambda: MagicMock()
        with pytest.raises(ValueError) as exc_info:
            llm_tool_loader._validate_no_runtime_key_collision(llm_tools)
        assert "brand_new_client" in str(exc_info.value)

    def test_load_llm_tools_works_without_any_provider(
        self,
        llm_tool_loader,
        provider_registry,
        tool_root: Path,
        config_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """provider 未登録（コア単独起動）でも load_llm_tools がエラーなく動く。"""
        schema = {"name": "calendar_get", "description": "テスト", "input_schema": {}}
        _write_py(tool_root / "calendar", "llm_calendar_get.py", schema)
        _write_yaml(
            config_root / "tools",
            "llm_calendar_get.yaml",
            "type: llm_calendar_get\nobsidian_client: dummy\n",
        )

        result = llm_tool_loader.load_llm_tools(tool_roots=[tool_root], config_root=config_root)
        assert "llm_calendar_get" in result


class TestGetLlmTools:
    """get_llm_tools のキャッシュ挙動のテスト。"""

    @pytest.fixture(autouse=True)
    def clear_cache(self, llm_tool_loader):
        """各テストの前後で lru_cache をクリアする。"""
        llm_tool_loader.get_llm_tools.cache_clear()
        yield
        llm_tool_loader.get_llm_tools.cache_clear()

    def test_returns_load_llm_tools_result(
        self, llm_tool_loader, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """load_llm_tools の戻り値をそのまま返す。"""
        expected = {"llm_foo": {"schema": {}, "execute": lambda: None}}
        monkeypatch.setattr(llm_tool_loader, "load_llm_tools", lambda: expected)

        assert llm_tool_loader.get_llm_tools() is expected

    def test_load_llm_tools_called_only_once(
        self, llm_tool_loader, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """複数回呼び出しても load_llm_tools は 1 回しか実行されない。"""
        calls = []

        def fake_load():
            calls.append(1)
            return {"llm_foo": {}}

        monkeypatch.setattr(llm_tool_loader, "load_llm_tools", fake_load)

        first = llm_tool_loader.get_llm_tools()
        second = llm_tool_loader.get_llm_tools()
        third = llm_tool_loader.get_llm_tools()

        assert len(calls) == 1
        assert first is second is third

    def test_cache_clear_triggers_reload(
        self, llm_tool_loader, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """cache_clear 後は再び load_llm_tools が実行される。"""
        calls = []

        def fake_load():
            calls.append(1)
            return {"llm_foo": {}}

        monkeypatch.setattr(llm_tool_loader, "load_llm_tools", fake_load)

        llm_tool_loader.get_llm_tools()
        llm_tool_loader.get_llm_tools.cache_clear()
        llm_tool_loader.get_llm_tools()

        assert len(calls) == 2


class TestNestedToolConfigOverride:
    """ネストしたツール呼び出しで子ツール自身の tool_config が優先されることのテスト。"""

    async def test_child_tool_config_used_in_nested_call(self, llm_tool_loader) -> None:
        """親ツール経由(call_tool)で子ツールを呼んでも、子は自身の tool_config で動く。

        expert が expert を呼ぶケースの再現。親の prompt が子に漏れないこと。
        """
        captured: dict[str, str] = {}

        async def execute_parent(input, context):
            # 親は自身の prompt を持ち、context 経由で子を呼ぶ
            return await context["call_tool"]("child_tool", {})

        async def execute_child(input, context):
            captured["prompt"] = context["prompt"]
            return {"success": True, "data": "done", "summary": ""}

        llm_tools = {
            "parent_tool": {
                "schema": {"function": {"name": "parent_tool"}},
                "execute": execute_parent,
                "tool_config": {"prompt": "親のプロンプト"},
            },
            "child_tool": {
                "schema": {"function": {"name": "child_tool"}},
                "execute": execute_child,
                "tool_config": {"prompt": "子のプロンプト"},
            },
        }
        result = await llm_tool_loader.execute_tool_call(
            "parent_tool", {}, llm_tools, {}
        )
        assert result["success"] is True
        # 子ツールは親のプロンプトではなく自身のプロンプトで動作する
        assert captured["prompt"] == "子のプロンプト"


class TestFormatToolCallLogLine:
    """_format_tool_call_log_line() の表示フォーマットテスト。"""

    def test_depth_zero_is_flat_wrench_line(self, llm_tool_loader) -> None:
        """depth==0 は "-# 🔧 <tool_name>" 形式になる。"""
        line = llm_tool_loader._format_tool_call_log_line("llm_health_expert", 0)
        assert line == "-# 🔧 llm_health_expert"

    def test_depth_one_is_indented_with_corner(self, llm_tool_loader) -> None:
        """depth==1 はインデント付きの "└" 形式になる。"""
        line = llm_tool_loader._format_tool_call_log_line("llm_health_get", 1)
        assert line == "-#   └ llm_health_get"

    def test_deeper_depth_increases_indent(self, llm_tool_loader) -> None:
        """depth が増えるほどインデントも増える。"""
        line1 = llm_tool_loader._format_tool_call_log_line("tool", 1)
        line2 = llm_tool_loader._format_tool_call_log_line("tool", 2)
        indent1 = line1.split("└")[0]
        indent2 = line2.split("└")[0]
        assert len(indent2) > len(indent1)

    def test_depth_beyond_max_does_not_grow_indent_further(self, llm_tool_loader) -> None:
        """MAX_TOOL_CALL_DEPTH を超える depth はそれ以上インデントが増えない。"""
        at_max = llm_tool_loader._format_tool_call_log_line(
            "tool", llm_tool_loader.MAX_TOOL_CALL_DEPTH
        )
        beyond_max = llm_tool_loader._format_tool_call_log_line(
            "tool", llm_tool_loader.MAX_TOOL_CALL_DEPTH + 10
        )
        assert at_max == beyond_max


class TestNotifyToolCall:
    """execute_tool_call からの Discord 通知フックのテスト。"""

    async def test_sends_notification_when_discord_and_notifier_present(self, llm_tool_loader) -> None:
        """client_type==discord かつ notifier 注入時、実行前に通知される。"""
        sent_lines: list[str] = []

        async def notifier(line: str) -> None:
            sent_lines.append(line)

        mock_execute = AsyncMock(return_value={"success": True, "data": "ok"})
        llm_tools = {
            "llm_foo": {
                "schema": {"function": {"name": "llm_foo"}},
                "execute": mock_execute,
                "tool_config": {},
            }
        }
        context = {
            "client_type": "discord",
            llm_tool_loader._TOOL_CALL_NOTIFIER_KEY: notifier,
        }
        await llm_tool_loader.execute_tool_call("llm_foo", {}, llm_tools, context)
        assert sent_lines == ["-# 🔧 llm_foo"]

    async def test_no_notification_when_not_discord(self, llm_tool_loader) -> None:
        """client_type が discord 以外なら notifier があっても呼ばれない（lilla-client / task）。"""
        sent_lines: list[str] = []

        async def notifier(line: str) -> None:
            sent_lines.append(line)

        mock_execute = AsyncMock(return_value={"success": True, "data": "ok"})
        llm_tools = {
            "llm_foo": {
                "schema": {"function": {"name": "llm_foo"}},
                "execute": mock_execute,
                "tool_config": {},
            }
        }
        for client_type in ("lilla-client", "task"):
            context = {
                "client_type": client_type,
                llm_tool_loader._TOOL_CALL_NOTIFIER_KEY: notifier,
            }
            await llm_tool_loader.execute_tool_call("llm_foo", {}, llm_tools, context)
        assert sent_lines == []

    async def test_no_notification_when_notifier_absent(self, llm_tool_loader) -> None:
        """notifier が context に注入されていなければ discord でも何もしない（例外も出さない）。"""
        mock_execute = AsyncMock(return_value={"success": True, "data": "ok"})
        llm_tools = {
            "llm_foo": {
                "schema": {"function": {"name": "llm_foo"}},
                "execute": mock_execute,
                "tool_config": {},
            }
        }
        result = await llm_tool_loader.execute_tool_call(
            "llm_foo", {}, llm_tools, {"client_type": "discord"}
        )
        assert result["success"] is True

    async def test_notifier_propagates_to_nested_call_with_incremented_depth(self, llm_tool_loader) -> None:
        """親ツールが call_tool 経由で子ツールを呼ぶと、子の通知は depth+1 で送られる。

        expert (depth 0) 内部から execute_tool_call が再入されるケースの再現。
        """
        sent: list[tuple[str, int]] = []

        async def notifier(line: str) -> None:
            sent.append((line, len(sent)))

        async def execute_child(input, context):
            return {"success": True, "data": "child", "summary": ""}

        async def execute_parent(input, context):
            return await context["call_tool"]("child_tool", {})

        llm_tools = {
            "parent_tool": {
                "schema": {"function": {"name": "parent_tool"}},
                "execute": execute_parent,
                "tool_config": {},
            },
            "child_tool": {
                "schema": {"function": {"name": "child_tool"}},
                "execute": execute_child,
                "tool_config": {},
            },
        }
        context = {
            "client_type": "discord",
            llm_tool_loader._TOOL_CALL_NOTIFIER_KEY: notifier,
        }
        result = await llm_tool_loader.execute_tool_call(
            "parent_tool", {}, llm_tools, context
        )
        assert result["success"] is True
        lines = [line for line, _ in sent]
        assert lines == ["-# 🔧 parent_tool", "-#   └ child_tool"]

    async def test_notification_failure_does_not_break_tool_execution(self, llm_tool_loader) -> None:
        """notifier が例外を送出しても、ツール自体の実行結果は正常に返る。"""
        async def failing_notifier(line: str) -> None:
            raise RuntimeError("discord send failed")

        mock_execute = AsyncMock(return_value={"success": True, "data": "ok"})
        llm_tools = {
            "llm_foo": {
                "schema": {"function": {"name": "llm_foo"}},
                "execute": mock_execute,
                "tool_config": {},
            }
        }
        context = {
            "client_type": "discord",
            llm_tool_loader._TOOL_CALL_NOTIFIER_KEY: failing_notifier,
        }
        result = await llm_tool_loader.execute_tool_call("llm_foo", {}, llm_tools, context)
        assert result["success"] is True

    async def test_notifier_key_reserved_in_runtime_context_keys(self, llm_tool_loader) -> None:
        """notifier キーはコア自身の予約キーとして collision 検証の対象になる。"""
        llm_tools = {
            "llm_foo": {"tool_config": {llm_tool_loader._TOOL_CALL_NOTIFIER_KEY: "x"}},
        }
        with pytest.raises(ValueError) as exc_info:
            llm_tool_loader._validate_no_runtime_key_collision(llm_tools)
        assert llm_tool_loader._TOOL_CALL_NOTIFIER_KEY in str(exc_info.value)
