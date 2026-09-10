"""AppConfig のプロンプト読み込みプロパティのテスト。

`system_prompt` / `conversation_prompt` / `discord_client_prompt` は共通ヘルパー
`_load_prompt` に委譲される。ここでは「override（`prompt` セクション）未設定なら
{env.config_root}/prompt/{subdir} を、設定済みならその値をそのまま
load_text_resources へ渡す」という各プロパティの解決規則を検証する。

`lilla_core.core.config` は他テスト（test_bot など）が sys.modules へ MagicMock を注入
するため、実クラスを確実に得る目的でファイルから直接ロードする。
"""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "src" / "lilla_core" / "core" / "config.py"
_REAL_CONFIG_MODULE_NAME = "_lilla_real_config_for_test"


def _load_real_config_module():
    """`src/core/config.py` を実クラスとして独立ロードする。

    `lilla_core.core.config` を上書きしないよう専用のモジュール名で登録する。Pydantic が
    `from __future__ import annotations` による遅延アノテーション（`Path` など）を
    解決できるよう、exec 前に sys.modules へ登録しておく必要がある。
    """
    spec = importlib.util.spec_from_file_location(_REAL_CONFIG_MODULE_NAME, _CONFIG_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[_REAL_CONFIG_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


_config = _load_real_config_module()
AppConfig = _config.AppConfig


class TestAppConfigPromptProperties:
    """コアの 3 つのプロンプトプロパティの解決規則を検証する。"""

    @pytest.fixture
    def make_config(self):
        """env.config_root を固定した AppConfig を生成するファクトリを返す。"""

        def _make(**prompt_overrides):
            return AppConfig(
                env={"discord_token": "dummy", "config_root": Path("/cfgroot")},
                discord={"my_user_id": "1"},
                prompt=prompt_overrides,
            )

        return _make

    def test_default_paths_use_config_root_and_subdir(self, make_config):
        """override 未設定時は dir:{config_root}/prompt/{subdir} が使われる。"""
        cfg = make_config(system=None, conversation=None, discord=None)
        with patch.object(_config, "load_text_resources", side_effect=lambda src: src):
            assert cfg.system_prompt == "dir:/cfgroot/prompt/system"
            assert cfg.conversation_prompt == "dir:/cfgroot/prompt/conversation"
            assert cfg.discord_client_prompt == "dir:/cfgroot/prompt/discord"

    def test_override_paths_are_passed_through(self, make_config):
        """override 設定時はその値がそのまま load_text_resources へ渡される。"""
        cfg = make_config(
            system="file:/x/system.md",
            conversation="dir:${config_root}/custom/conv",
            discord="file:/x/discord.md",
        )
        with patch.object(_config, "load_text_resources", side_effect=lambda src: src):
            assert cfg.system_prompt == "file:/x/system.md"
            assert cfg.conversation_prompt == "dir:${config_root}/custom/conv"
            assert cfg.discord_client_prompt == "file:/x/discord.md"

    def test_list_source_spec_is_accepted_and_passed_through(self, make_config):
        """prompt の各項目にリスト形式の source spec を指定してもバリデーションエラーに
        ならず、そのまま load_text_resources へ渡される。"""
        cfg = make_config(system=["file:/x/a.md", "dir:${config_root}/b"])
        with patch.object(_config, "load_text_resources", side_effect=lambda src: src):
            assert cfg.system_prompt == ["file:/x/a.md", "dir:${config_root}/b"]

    def test_returns_value_from_load_text_resources(self, make_config):
        """プロパティの戻り値は load_text_resources の戻り値をそのまま返す。"""
        cfg = make_config(system=None)
        with patch.object(_config, "load_text_resources", return_value="LOADED") as loader:
            assert cfg.system_prompt == "LOADED"
            loader.assert_called_once_with("dir:/cfgroot/prompt/system")
