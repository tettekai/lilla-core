"""`compose_config()` による設定合成のテスト。

拡張が `config_models()` / `env_fields()` で申告した差分を、コアが起動時に 1 つの
`AppConfig` へ組む。YAML セクションは `pydantic.create_model` で `AppConfig` に、
秘匿フィールドは `EnvConfig` に足し、OS 変数名のマッピングも合わせて延ばす。

`lilla_core.core.config` は他テスト（test_bot など）が sys.modules へ MagicMock を
注入するため、実クラスを確実に得る目的でファイルから直接ロードする。
"""

import importlib.util
import sys
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

_CONFIG_PATH = Path(importlib.util.find_spec("lilla_core.core.config").origin)
_REAL_CONFIG_MODULE_NAME = "_lilla_real_config_for_compose_test"


def _load_real_config_module():
    """`lilla_core/core/config.py` を実クラスとして独立ロードする。"""
    spec = importlib.util.spec_from_file_location(_REAL_CONFIG_MODULE_NAME, _CONFIG_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[_REAL_CONFIG_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


_config_module = _load_real_config_module()
compose_config = _config_module.compose_config

_DUMMY_LLM_YAML = """\
llm:
  default: dummy
  providers:
    dummy:
      type: ollama
      url: http://localhost:11434
      model: dummy
"""


class MediaConfig(BaseModel):
    """全フィールドにデフォルトがあるセクション（省略可能になる想定）。"""

    default: str = ""
    reset_minutes: int = 5


class HabitsConfig(BaseModel):
    """必須フィールドを持つセクション（セクション自体が必須になる想定）。"""

    channel: str


@pytest.fixture(autouse=True)
def reset_compose_state():
    """合成が触るプロセス全体の状態を、テストごとに前後で初期化する。

    `_extra_env_var_names` は `EnvConfigSettingsSource` が読むレジストリ、
    `_default_config` は `@lru_cache(maxsize=1)` を持つため、どちらも残すと
    後続のテストへ漏れる。
    """
    def _reset() -> None:
        _config_module._config_instance = None
        _config_module._extra_env_var_names.clear()
        _config_module._default_config.cache_clear()

    _reset()
    yield
    _reset()


@pytest.fixture
def config_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """`lilla.yaml` を書き出して `CONFIG_ROOT` に指定するファクトリを返す。

    `.env` 由来の値で期待値が揺れないよう、`.env` の読み込みも止める。
    """
    monkeypatch.setenv("CONFIG_ROOT", str(tmp_path))
    monkeypatch.setenv("DISCORD_TOKEN", "dummy")

    def _write(extra_yaml: str = "") -> Path:
        (tmp_path / "lilla.yaml").write_text(
            'discord:\n  my_user_id: "1"\n' + _DUMMY_LLM_YAML + extra_yaml,
            encoding="utf-8",
        )
        return tmp_path

    return _write


# ---------------------------------------------------------------------------
# TestComposeSections
# ---------------------------------------------------------------------------


class TestComposeSections:
    """拡張が申告した YAML セクションの合成。"""

    def test_section_is_readable_as_typed_model(self, config_root) -> None:
        """申告したセクションが YAML から型付きで読める。"""
        config_root("media:\n  default: idle.png\n  reset_minutes: 30\n")

        cfg = compose_config({"media": MediaConfig}, {})

        assert isinstance(cfg.media, MediaConfig)
        assert cfg.media.default == "idle.png"
        assert cfg.media.reset_minutes == 30

    def test_composed_model_is_an_appconfig_subclass(self, config_root) -> None:
        """合成結果は `AppConfig` のサブクラスで、コア確定の項目もそのまま読める。"""
        config_root()

        cfg = compose_config({"media": MediaConfig}, {})

        assert isinstance(cfg, _config_module.AppConfig)
        assert cfg.discord.my_user_id == "1"
        assert cfg.ui.locale == "ja"

    def test_section_with_all_defaults_may_be_omitted(self, config_root) -> None:
        """全フィールドにデフォルトがあるセクションは YAML に無くてもよい。"""
        config_root()

        cfg = compose_config({"media": MediaConfig}, {})

        assert cfg.media.default == ""
        assert cfg.media.reset_minutes == 5

    def test_section_with_required_field_must_be_present(self, config_root) -> None:
        """必須フィールドを持つセクションは、YAML に無ければ起動時に落ちる。"""
        config_root()

        with pytest.raises(ValidationError) as exc_info:
            compose_config({"habits": HabitsConfig}, {})

        assert [error["loc"] for error in exc_info.value.errors()] == [("habits",)]

    def test_section_with_required_field_is_read_when_present(self, config_root) -> None:
        """必須フィールドを持つセクションも、YAML にあれば通常どおり読める。"""
        config_root("habits:\n  channel: habits-test\n")

        cfg = compose_config({"habits": HabitsConfig}, {})

        assert cfg.habits.channel == "habits-test"

    def test_multiple_sections_are_composed_together(self, config_root) -> None:
        """複数セクションをまとめて合成できる。"""
        config_root("habits:\n  channel: habits-test\nmedia:\n  default: a.png\n")

        cfg = compose_config({"habits": HabitsConfig, "media": MediaConfig}, {})

        assert cfg.habits.channel == "habits-test"
        assert cfg.media.default == "a.png"

    def test_undeclared_yaml_section_is_ignored(self, config_root) -> None:
        """申告していない YAML セクションは従来どおり無視する。"""
        config_root("unknown_section:\n  foo: bar\n")

        cfg = compose_config({"media": MediaConfig}, {})

        assert not hasattr(cfg, "unknown_section")


# ---------------------------------------------------------------------------
# TestComposeEnvFields
# ---------------------------------------------------------------------------


class TestComposeEnvFields:
    """拡張が申告した秘匿フィールドの合成。"""

    def test_env_field_is_read_from_the_declared_os_variable(
        self, config_root, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """申告した OS 変数名の値が `cfg.env.<フィールド名>` に入る。"""
        config_root()
        monkeypatch.setenv("HMAC_SECRET", "s3cret")

        cfg = compose_config({}, {"hmac_secret": "HMAC_SECRET"})

        assert cfg.env.hmac_secret == "s3cret"

    def test_env_field_defaults_to_none_when_unset(
        self, config_root, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """OS 変数が無ければ `None` になる（合成されるフィールドは常に任意）。"""
        config_root()
        monkeypatch.delenv("HMAC_SECRET", raising=False)

        cfg = compose_config({}, {"hmac_secret": "HMAC_SECRET"})

        assert cfg.env.hmac_secret is None

    def test_core_env_fields_survive_composition(self, config_root) -> None:
        """コア確定の `env` フィールドは合成後もそのまま読める。"""
        config_root()

        cfg = compose_config({}, {"hmac_secret": "HMAC_SECRET"})

        assert cfg.env.discord_token == "dummy"
        assert isinstance(cfg.env, _config_module.EnvConfig)

    def test_var_name_registry_is_replaced_on_each_compose(self, config_root) -> None:
        """合成のたびに OS 変数名のレジストリを入れ替える（前回分を持ち越さない）。"""
        config_root()

        compose_config({}, {"hmac_secret": "HMAC_SECRET"})
        assert _config_module._extra_env_var_names == {"hmac_secret": "HMAC_SECRET"}

        compose_config({}, {})
        assert _config_module._extra_env_var_names == {}


# ---------------------------------------------------------------------------
# TestComposeWithoutContributions
# ---------------------------------------------------------------------------


class TestComposeWithoutContributions:
    """申告が空のとき（拡張 0 個を含む）の挙動。"""

    def test_empty_contributions_build_plain_appconfig(self, config_root) -> None:
        """どちらの申告も空なら素の `AppConfig` を組む。"""
        config_root()

        cfg = compose_config({}, {})

        assert type(cfg) is _config_module.AppConfig

    def test_default_config_cache_is_cleared(self, config_root) -> None:
        """合成前に踏まれた `_default_config()` のキャッシュを捨てる。"""
        config_root()
        stale = _config_module._default_config()

        compose_config({}, {})

        assert _config_module._default_config() is not stale


# ---------------------------------------------------------------------------
# TestComposeNameValidation
# ---------------------------------------------------------------------------


class TestComposeNameValidation:
    """セクション名・フィールド名の妥当性検査。"""

    @pytest.mark.parametrize("name", ["my section", "1media", "_media", "media-x"])
    def test_invalid_section_name_fails_fast(self, config_root, name: str) -> None:
        """識別子でない、またはアンダースコア始まりのセクション名は落とす。"""
        config_root()

        with pytest.raises(ValueError, match="Invalid config section name"):
            compose_config({name: MediaConfig}, {})

    @pytest.mark.parametrize("name", ["my secret", "_secret", "1secret"])
    def test_invalid_env_field_name_fails_fast(self, config_root, name: str) -> None:
        """識別子でない、またはアンダースコア始まりのフィールド名は落とす。"""
        config_root()

        with pytest.raises(ValueError, match="Invalid env field name"):
            compose_config({}, {name: "SOME_VAR"})
