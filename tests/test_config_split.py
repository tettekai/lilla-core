"""AppConfig のコア確定フィールドが YAML / 環境変数から正しく読み取れることを
固定する特性化テスト。

YAML 由来の項目は lilla.yaml と同じネスト（`cfg.discord.my_user_id`）で、
`.env` / OS 環境変数由来の項目は `cfg.env.*` に入る。

`lilla_core.core.config` は他テスト（test_bot など）が sys.modules へ MagicMock を
注入するため、実クラスを確実に得る目的でファイルから直接ロードする。
"""

import importlib.util
import os
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

_CONFIG_PATH = Path(
    importlib.util.find_spec("lilla_core.core.config").origin
)
_REAL_CONFIG_MODULE_NAME = "_lilla_real_config_for_split_test"


def _load_real_config_module():
    """`lilla_core/core/config.py` を実クラスとして独立ロードする。"""
    spec = importlib.util.spec_from_file_location(_REAL_CONFIG_MODULE_NAME, _CONFIG_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[_REAL_CONFIG_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


_config_module = _load_real_config_module()
AppConfig = _config_module.AppConfig
ProxyConfig = _config_module.ProxyConfig


# `.env` の実値で YAML 期待値や既定値が上書きされるのを防ぐため、テストで
# 検証する `env` セクションの変数は事前に削除する。
_CORE_ENV_NAMES = [
    "DISCORD_TOKEN",
    "MONGODB_URI",
    "HTTP_PROXY_USER",
    "HTTP_PROXY_PASS",
]

_DUMMY_LLM_YAML = """\
llm:
  default: dummy
  providers:
    dummy:
      type: ollama
      url: http://localhost:11434
      model: dummy
"""

_YAML_WITH_ALL_SECTIONS = """\
discord:
  my_user_id: "1234"
mongodb:
  db_name: my_db
memory:
  max_history_turns: 50
tools:
  main_available_tools:
    - llm_a
    - llm_b
commands:
  mongodata:
    allowed_collections:
      - asken_daily
llm:
  default: prov-a
  providers:
    prov-a:
      type: ollama
      url: http://ollama
      model: some-model
"""


@pytest.fixture
def isolated_config_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """lilla.yaml も環境変数も汚染されていない config_root を用意する。

    後続のテストで tmp_path に lilla.yaml を書くための土台。テスト対象の
    env 変数は事前にすべて削除しておく（開発環境の `.env` 由来の値が
    混ざるのを防ぐ）。
    """
    monkeypatch.setenv("CONFIG_ROOT", str(tmp_path))
    for env_name in _CORE_ENV_NAMES:
        monkeypatch.delenv(env_name, raising=False)
    return tmp_path


def _write_yaml(config_root: Path, body: str = _YAML_WITH_ALL_SECTIONS) -> None:
    """テスト用の lilla.yaml を config_root へ書き出す。"""
    (config_root / "lilla.yaml").write_text(body, encoding="utf-8")


class TestCoreFieldsCharacterization:
    """コア確定フィールドが YAML / 環境変数から正しく読めることを固定する。"""

    def _build(self, isolated_config_root: Path) -> "AppConfig":
        _write_yaml(isolated_config_root)
        return AppConfig(env={"discord_token": "dummy"}, _env_file=None)

    def test_discord_my_user_id_from_yaml(self, isolated_config_root: Path) -> None:
        cfg = self._build(isolated_config_root)
        assert cfg.discord.my_user_id == "1234"

    def test_mongodb_db_name_from_yaml(self, isolated_config_root: Path) -> None:
        cfg = self._build(isolated_config_root)
        assert cfg.mongodb.db_name == "my_db"

    def test_memory_max_history_turns_from_yaml(self, isolated_config_root: Path) -> None:
        cfg = self._build(isolated_config_root)
        assert cfg.memory.max_history_turns == 50

    def test_main_available_tools_from_yaml(self, isolated_config_root: Path) -> None:
        cfg = self._build(isolated_config_root)
        assert cfg.tools.main_available_tools == ["llm_a", "llm_b"]

    def test_mongodata_allowed_collections_from_yaml(
        self, isolated_config_root: Path
    ) -> None:
        cfg = self._build(isolated_config_root)
        assert cfg.commands.mongodata.allowed_collections == ["asken_daily"]

    def test_llm_default_and_providers_from_yaml(self, isolated_config_root: Path) -> None:
        """llm セクションの構造がそのまま読み取られる。"""
        cfg = self._build(isolated_config_root)
        assert cfg.llm.default == "prov-a"
        assert "prov-a" in cfg.llm.providers
        assert cfg.llm.providers["prov-a"].model == "some-model"

    def test_discord_token_from_env(
        self, isolated_config_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """OS 環境変数の DISCORD_TOKEN が `cfg.env.discord_token` に入る。"""
        monkeypatch.setenv("DISCORD_TOKEN", "env-token")
        _write_yaml(isolated_config_root)
        cfg = AppConfig(_env_file=None)
        assert cfg.env.discord_token == "env-token"

    def test_config_root_default_matches_settings_source_fallback(
        self, isolated_config_root: Path
    ) -> None:
        """`EnvConfig.config_root` の既定は settings source のフォールバックと同じ。"""
        assert _config_module.EnvConfig(discord_token="x").config_root == Path("/app/config")


class TestYamlEnvSectionIsIgnored:
    """YAML の `env:` セクションは読まず、警告だけ出す。"""

    def test_env_section_is_not_loaded_and_warns(
        self,
        isolated_config_root: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        _write_yaml(
            isolated_config_root,
            _YAML_WITH_ALL_SECTIONS + 'env:\n  discord_token: yaml-token\n',
        )
        with caplog.at_level("WARNING"):
            with pytest.raises(Exception):
                AppConfig(_env_file=None)

        assert any("env:" in record.message for record in caplog.records)


class TestBotSectionLegacyKeysAreIgnored:
    """履歴系の正は `memory:` で、`bot:` の名残キーは無視する。"""

    def test_bot_max_history_turns_does_not_reach_memory(
        self, isolated_config_root: Path
    ) -> None:
        _write_yaml(
            isolated_config_root,
            'discord:\n  my_user_id: "1234"\nbot:\n  max_history_turns: 99\n' + _DUMMY_LLM_YAML,
        )
        cfg = AppConfig(env={"discord_token": "dummy"}, _env_file=None)
        assert cfg.memory.max_history_turns == 30


class TestProxyConfig:
    """プロキシ URL の解決規則。"""

    def test_prefers_https(self) -> None:
        proxy = ProxyConfig(http="http://h:1", https="http://s:2")
        assert proxy.resolve_url() == "http://s:2"

    def test_falls_back_to_http(self) -> None:
        assert ProxyConfig(http="http://h:1").resolve_url() == "http://h:1"

    def test_returns_none_when_unset(self) -> None:
        assert ProxyConfig().resolve_url() is None


class TestPathsConfig:
    """`paths` セクション。"""

    def test_legacy_allowed_tool_paths_is_ignored(self) -> None:
        """撤去済みの `allowed_tool_paths` が YAML に残っていても起動は落ちない（読みもしない）。"""
        paths = _config_module.PathsConfig(
            tool_root="/app/tools", allowed_tool_paths="/app/tools,/app/config/tools"
        )
        assert paths.tool_root == Path("/app/tools")
        assert not hasattr(paths, "allowed_tool_paths")


class TestUiConfig:
    """`ui` セクション（Discord 向け文言のロケール）。"""

    def test_locale_defaults_to_ja(self, isolated_config_root: Path) -> None:
        """`ui:` を書かなければ既定の `ja` になる。"""
        _write_yaml(isolated_config_root)
        cfg = AppConfig(env={"discord_token": "dummy"}, _env_file=None)
        assert cfg.ui.locale == "ja"

    def test_locale_from_yaml(self, isolated_config_root: Path) -> None:
        """`ui.locale` を YAML から読み取る。"""
        _write_yaml(
            isolated_config_root,
            'discord:\n  my_user_id: "1"\nui:\n  locale: en\n' + _DUMMY_LLM_YAML,
        )
        cfg = AppConfig(env={"discord_token": "dummy"}, _env_file=None)
        assert cfg.ui.locale == "en"

    def test_unknown_locale_does_not_fail_startup(self, isolated_config_root: Path) -> None:
        """未知のロケール名でも設定の読み込み自体は成功する（文言側でフォールバックする）。"""
        _write_yaml(
            isolated_config_root,
            'discord:\n  my_user_id: "1"\nui:\n  locale: fr\n' + _DUMMY_LLM_YAML,
        )
        cfg = AppConfig(env={"discord_token": "dummy"}, _env_file=None)
        assert cfg.ui.locale == "fr"

    def test_timezone_defaults_to_none(self, isolated_config_root: Path) -> None:
        """`ui.timezone` を書かなければ `None`（OS のローカルに従う）になる。"""
        _write_yaml(isolated_config_root)
        cfg = AppConfig(env={"discord_token": "dummy"}, _env_file=None)
        assert cfg.ui.timezone is None

    def test_timezone_from_yaml(self, isolated_config_root: Path) -> None:
        """`ui.timezone` を YAML から読み取る。"""
        _write_yaml(
            isolated_config_root,
            'discord:\n  my_user_id: "1"\nui:\n  timezone: Asia/Tokyo\n' + _DUMMY_LLM_YAML,
        )
        cfg = AppConfig(env={"discord_token": "dummy"}, _env_file=None)
        assert cfg.ui.timezone == "Asia/Tokyo"

    def test_explicit_null_timezone_is_none(self, isolated_config_root: Path) -> None:
        """YAML の `timezone:`（null）も未指定と同じ扱いになる。"""
        _write_yaml(
            isolated_config_root,
            'discord:\n  my_user_id: "1"\nui:\n  timezone:\n' + _DUMMY_LLM_YAML,
        )
        cfg = AppConfig(env={"discord_token": "dummy"}, _env_file=None)
        assert cfg.ui.timezone is None

    def test_unknown_timezone_fails_startup(self, isolated_config_root: Path) -> None:
        """`ZoneInfo` が受け付けない名前は起動時に失敗する（ロケールと異なり落とす）。"""
        _write_yaml(
            isolated_config_root,
            'discord:\n  my_user_id: "1"\nui:\n  timezone: Nowhere/Nothing\n' + _DUMMY_LLM_YAML,
        )
        with pytest.raises(ValidationError):
            AppConfig(env={"discord_token": "dummy"}, _env_file=None)

    def test_empty_timezone_fails_startup(self, isolated_config_root: Path) -> None:
        """空文字はフォールバックせず起動時に失敗する。"""
        _write_yaml(
            isolated_config_root,
            'discord:\n  my_user_id: "1"\nui:\n  timezone: ""\n' + _DUMMY_LLM_YAML,
        )
        with pytest.raises(ValidationError):
            AppConfig(env={"discord_token": "dummy"}, _env_file=None)


class TestYamlSearchUsesEnvConfigRoot:
    """`lilla.yaml` の探索先が `.env` の `CONFIG_ROOT` にも従うことを固定する。

    `EnvConfigSettingsSource` と同じ優先順位（OS 環境変数 > `.env` > 既定値）で
    `CONFIG_ROOT` を解決し、その結果のディレクトリの `lilla.yaml` を読む。
    """

    def test_config_root_from_dotenv_only(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """OS に `CONFIG_ROOT` が無く `.env` にだけあるとき、`.env` 側のディレクトリを見る。

        `settings_customise_sources` は `cls.model_config["env_file"]`（既定 `.env`）を
        読むため、`.env` を検証する際はカレントディレクトリを切り替えて配置する。
        """
        for env_name in [*_CORE_ENV_NAMES, "CONFIG_ROOT"]:
            monkeypatch.delenv(env_name, raising=False)
        monkeypatch.chdir(tmp_path)

        yaml_dir = tmp_path / "from_dotenv"
        yaml_dir.mkdir()
        _write_yaml(yaml_dir, 'discord:\n  my_user_id: "from-dotenv"\n' + _DUMMY_LLM_YAML)

        (tmp_path / ".env").write_text(
            f"DISCORD_TOKEN=dummy\nCONFIG_ROOT={yaml_dir}\n", encoding="utf-8"
        )

        cfg = AppConfig()
        assert cfg.discord.my_user_id == "from-dotenv"
        assert cfg.env.config_root == yaml_dir

    def test_os_config_root_wins_over_dotenv(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """OS 環境変数と `.env` の両方に `CONFIG_ROOT` があるとき、OS 側が優先される。"""
        for env_name in _CORE_ENV_NAMES:
            monkeypatch.delenv(env_name, raising=False)
        monkeypatch.chdir(tmp_path)

        os_dir = tmp_path / "from_os"
        os_dir.mkdir()
        _write_yaml(os_dir, 'discord:\n  my_user_id: "from-os"\n' + _DUMMY_LLM_YAML)

        dotenv_dir = tmp_path / "from_dotenv"
        dotenv_dir.mkdir()
        _write_yaml(dotenv_dir, 'discord:\n  my_user_id: "from-dotenv"\n' + _DUMMY_LLM_YAML)

        (tmp_path / ".env").write_text(
            f"DISCORD_TOKEN=dummy\nCONFIG_ROOT={dotenv_dir}\n", encoding="utf-8"
        )
        monkeypatch.setenv("CONFIG_ROOT", str(os_dir))

        cfg = AppConfig()
        assert cfg.discord.my_user_id == "from-os"
        assert cfg.env.config_root == os_dir


class TestDotenvAppliedToOsEnviron:
    """`.env` の値を `os.environ` へ反映する（プロバイダ依存の LLM API キー向け）。"""

    def test_dotenv_only_value_is_applied(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """OS に無く `.env` にだけある変数は `os.environ` から読めるようになる。"""
        monkeypatch.delenv("GROK_API_KEY", raising=False)
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text("GROK_API_KEY=from-dotenv\n", encoding="utf-8")

        try:
            _config_module._apply_dotenv_to_os_environ()
            assert os.environ["GROK_API_KEY"] == "from-dotenv"
        finally:
            os.environ.pop("GROK_API_KEY", None)

    def test_os_environ_value_wins_over_dotenv(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """同じ名前が OS にも `.env` にもあるとき、OS 側の値が優先される。"""
        monkeypatch.setenv("GROK_API_KEY", "from-os")
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text("GROK_API_KEY=from-dotenv\n", encoding="utf-8")

        _config_module._apply_dotenv_to_os_environ()

        assert os.environ["GROK_API_KEY"] == "from-os"


class TestLlmConfigValidation:
    """`llm.default` が `llm.providers` に存在することを起動時に検証する。"""

    def test_missing_llm_section_fails(self, isolated_config_root: Path) -> None:
        """`llm:` を書かないと、クラスデフォルト（`providers={}`）に対して検証が走り落ちる。"""
        _write_yaml(isolated_config_root, 'discord:\n  my_user_id: "1"\n')
        with pytest.raises(ValidationError):
            AppConfig(env={"discord_token": "dummy"}, _env_file=None)

    def test_default_with_empty_providers_fails(self, isolated_config_root: Path) -> None:
        _write_yaml(
            isolated_config_root,
            'discord:\n  my_user_id: "1"\nllm:\n  default: dummy\n  providers: {}\n',
        )
        with pytest.raises(ValidationError):
            AppConfig(env={"discord_token": "dummy"}, _env_file=None)

    def test_default_not_in_providers_fails(self, isolated_config_root: Path) -> None:
        _write_yaml(
            isolated_config_root,
            'discord:\n  my_user_id: "1"\n'
            "llm:\n"
            "  default: missing\n"
            "  providers:\n"
            "    other:\n"
            "      type: ollama\n"
            "      url: http://localhost:11434\n"
            "      model: dummy\n",
        )
        with pytest.raises(ValidationError):
            AppConfig(env={"discord_token": "dummy"}, _env_file=None)

    def test_default_matching_provider_succeeds(self, isolated_config_root: Path) -> None:
        _write_yaml(isolated_config_root, 'discord:\n  my_user_id: "1"\n' + _DUMMY_LLM_YAML)
        cfg = AppConfig(env={"discord_token": "dummy"}, _env_file=None)
        assert cfg.llm.default == "dummy"

    def test_provider_type_typo_fails(self, isolated_config_root: Path) -> None:
        _write_yaml(
            isolated_config_root,
            'discord:\n  my_user_id: "1"\n'
            "llm:\n"
            "  default: dummy\n"
            "  providers:\n"
            "    dummy:\n"
            "      type: ollmaa\n"
            "      url: http://localhost:11434\n"
            "      model: dummy\n",
        )
        with pytest.raises(ValidationError):
            AppConfig(env={"discord_token": "dummy"}, _env_file=None)

    @pytest.mark.parametrize("provider_type", ["ollama", "openai_compat"])
    def test_provider_type_valid_values_succeed(
        self, isolated_config_root: Path, provider_type: str
    ) -> None:
        _write_yaml(
            isolated_config_root,
            'discord:\n  my_user_id: "1"\n'
            "llm:\n"
            "  default: dummy\n"
            "  providers:\n"
            "    dummy:\n"
            f"      type: {provider_type}\n"
            "      url: http://localhost:11434\n"
            "      model: dummy\n",
        )
        cfg = AppConfig(env={"discord_token": "dummy"}, _env_file=None)
        assert cfg.llm.providers["dummy"].type == provider_type


class TestNoFlattenApiRemains:
    """flatten 方式の名残がコアに残っていないことを保証する。"""

    @pytest.mark.parametrize(
        "name",
        ["_flatten_yaml_config", "register_yaml_flatten_extension", "_yaml_flatten_extensions"],
    )
    def test_flatten_symbols_are_gone(self, name: str) -> None:
        assert not hasattr(_config_module, name)

    def test_no_public_name_contains_flatten(self) -> None:
        assert [n for n in dir(_config_module) if "flatten" in n.lower()] == []
