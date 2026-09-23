"""`compose_config()` による設定合成のテスト。

拡張が `config_models()` / `env_fields()` で申告した差分を、コアが起動時に 1 つの
`AppConfig` へ組む。YAML セクションは `pydantic.create_model` で `ExtensionsConfig` に
足して `cfg.extensions` の下へ置き、秘匿フィールドは `EnvConfig` に足し、OS 変数名の
マッピングも合わせて延ばす。

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
    """拡張が申告した YAML セクションの合成（`extensions:` の下）。"""

    def test_section_is_readable_as_typed_model(self, config_root) -> None:
        """申告したセクションが `extensions:` の下の YAML から型付きで読める。"""
        config_root("extensions:\n  media:\n    default: idle.png\n    reset_minutes: 30\n")

        cfg = compose_config({"media": MediaConfig}, {})

        assert isinstance(cfg.extensions.media, MediaConfig)
        assert cfg.extensions.media.default == "idle.png"
        assert cfg.extensions.media.reset_minutes == 30

    def test_section_is_not_added_at_the_top_level(self, config_root) -> None:
        """拡張のセクションはトップレベルの `cfg.<節名>` には作らない。"""
        config_root()

        cfg = compose_config({"media": MediaConfig}, {})

        assert not hasattr(cfg, "media")
        assert "media" not in type(cfg).model_fields

    def test_composed_model_is_an_appconfig_subclass(self, config_root) -> None:
        """合成結果は `AppConfig` のサブクラスで、コア確定の項目もそのまま読める。"""
        config_root()

        cfg = compose_config({"media": MediaConfig}, {})

        assert isinstance(cfg, _config_module.AppConfig)
        assert isinstance(cfg.extensions, _config_module.ExtensionsConfig)
        assert cfg.discord.my_user_id == "1"
        assert cfg.ui.locale == "ja"

    def test_section_with_all_defaults_may_be_omitted(self, config_root) -> None:
        """全フィールドにデフォルトがあるセクションは YAML に無くてもよい。"""
        config_root()

        cfg = compose_config({"media": MediaConfig}, {})

        assert cfg.extensions.media.default == ""
        assert cfg.extensions.media.reset_minutes == 5

    def test_section_with_required_field_must_be_present(self, config_root) -> None:
        """必須フィールドを持つセクションは、YAML に無ければ起動時に落ちる。"""
        config_root()

        with pytest.raises(ValidationError) as exc_info:
            compose_config({"habits": HabitsConfig}, {})

        assert [error["loc"] for error in exc_info.value.errors()] == [("extensions",)]

    def test_required_section_missing_under_extensions_fails(self, config_root) -> None:
        """`extensions:` はあっても必須セクションが無ければ、その位置を示して落ちる。"""
        config_root("extensions:\n  media:\n    default: a.png\n")

        with pytest.raises(ValidationError) as exc_info:
            compose_config({"habits": HabitsConfig, "media": MediaConfig}, {})

        assert [error["loc"] for error in exc_info.value.errors()] == [
            ("extensions", "habits")
        ]

    def test_section_with_required_field_is_read_when_present(self, config_root) -> None:
        """必須フィールドを持つセクションも、YAML にあれば通常どおり読める。"""
        config_root("extensions:\n  habits:\n    channel: habits-test\n")

        cfg = compose_config({"habits": HabitsConfig}, {})

        assert cfg.extensions.habits.channel == "habits-test"

    def test_multiple_sections_are_composed_together(self, config_root) -> None:
        """複数セクションをまとめて合成できる。"""
        config_root(
            "extensions:\n"
            "  habits:\n    channel: habits-test\n"
            "  media:\n    default: a.png\n"
        )

        cfg = compose_config({"habits": HabitsConfig, "media": MediaConfig}, {})

        assert cfg.extensions.habits.channel == "habits-test"
        assert cfg.extensions.media.default == "a.png"

    def test_section_named_like_a_core_section_does_not_collide(self, config_root) -> None:
        """コア確定の節と同名の拡張セクションも、名前空間が別なので共存できる。"""
        config_root("extensions:\n  discord:\n    default: ext.png\n")

        cfg = compose_config({"discord": MediaConfig}, {})

        assert cfg.discord.my_user_id == "1"
        assert cfg.extensions.discord.default == "ext.png"

    def test_undeclared_top_level_section_is_ignored(self, config_root) -> None:
        """誰も申告していないトップレベルの節は従来どおり無視する。"""
        config_root("unknown_section:\n  foo: bar\n")

        cfg = compose_config({"media": MediaConfig}, {})

        assert not hasattr(cfg, "unknown_section")

    def test_declared_section_at_top_level_fails(self, config_root) -> None:
        """申告した節をトップレベルに書いたままだと、移し忘れとして起動時に落とす。"""
        config_root("media:\n  default: idle.png\n")

        with pytest.raises(ValidationError, match="must be placed under 'extensions:'.*media"):
            compose_config({"media": MediaConfig}, {})

    def test_core_named_section_at_top_level_is_the_core_one(self, config_root) -> None:
        """コア確定と同名の節がトップレベルにあっても、それはコアの節なので落とさない。"""
        config_root()

        cfg = compose_config({"discord": MediaConfig}, {})

        assert cfg.discord.my_user_id == "1"
        assert cfg.extensions.discord.default == ""


# ---------------------------------------------------------------------------
# TestExtensionsSection
# ---------------------------------------------------------------------------


class TestExtensionsSection:
    """`extensions:` 節そのものの扱い。"""

    def test_unknown_key_under_extensions_fails(self, config_root) -> None:
        """ロードしていない拡張の節が `extensions:` の下にあれば起動時に落とす。"""
        config_root("extensions:\n  media:\n    default: a.png\n  leftover:\n    x: 1\n")

        with pytest.raises(ValidationError) as exc_info:
            compose_config({"media": MediaConfig}, {})

        assert [error["loc"] for error in exc_info.value.errors()] == [
            ("extensions", "leftover")
        ]

    def test_unknown_key_fails_without_any_extension(self, config_root) -> None:
        """拡張が 0 個でも `extensions:` の下に何か書いてあれば落とす。"""
        config_root("extensions:\n  google:\n    client_id: x\n")

        with pytest.raises(ValidationError) as exc_info:
            compose_config({}, {})

        assert [error["loc"] for error in exc_info.value.errors()] == [
            ("extensions", "google")
        ]

    def test_extensions_is_empty_without_any_extension(self, config_root) -> None:
        """拡張が 0 個なら `extensions` は空で起動する。"""
        config_root()

        cfg = compose_config({}, {})

        assert type(cfg.extensions) is _config_module.ExtensionsConfig
        assert cfg.extensions.model_dump() == {}

    def test_empty_extensions_key_is_accepted(self, config_root) -> None:
        """中身の無い `extensions:`（YAML の null）は空の節として扱う。"""
        config_root("extensions:\n")

        cfg = compose_config({"media": MediaConfig}, {})

        assert cfg.extensions.media.reset_minutes == 5


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


class TestGetSection:
    """`get_section()` による型付きのセクション取得。"""

    def test_returns_declared_section_as_model(self, config_root) -> None:
        """申告したセクションを、そのモデルの型で返す。"""
        config_root("extensions:\n  media:\n    default: idle.png\n")
        cfg = compose_config({"media": MediaConfig}, {})

        section = _config_module.get_section("media", MediaConfig, config=cfg)

        assert isinstance(section, MediaConfig)
        assert section.default == "idle.png"
        assert section is cfg.extensions.media

    def test_accepts_the_extension_name(self, config_root) -> None:
        """拡張の `name`（ハイフン入り）を渡しても、導いた節名で引ける。"""
        config_root("extensions:\n  google_oauth:\n    default: g.png\n")
        cfg = compose_config({"google_oauth": MediaConfig}, {})

        by_name = _config_module.get_section("google-oauth", MediaConfig, config=cfg)
        by_section = _config_module.get_section("google_oauth", MediaConfig, config=cfg)

        assert by_name is by_section is cfg.extensions.google_oauth
        assert by_name.default == "g.png"

    def test_core_section_is_not_looked_up(self, config_root) -> None:
        """コア確定のトップレベル節は探さない（`extensions:` の下だけを見る）。"""
        config_root()
        cfg = compose_config({}, {})

        with pytest.raises(ValueError, match="'extensions.ui' is not declared"):
            _config_module.get_section("ui", _config_module.UiConfig, config=cfg)

    def test_extension_section_wins_over_same_named_core_section(self, config_root) -> None:
        """コアと同名の拡張セクションは、拡張側（`extensions:` の下）を返す。"""
        config_root()
        cfg = compose_config({"discord": MediaConfig}, {})

        section = _config_module.get_section("discord", MediaConfig, config=cfg)

        assert section is cfg.extensions.discord

    def test_defaults_to_process_config(self, config_root) -> None:
        """`config` を省略すると `get_config()` から読む。"""
        config_root("extensions:\n  media:\n    reset_minutes: 42\n")
        cfg = compose_config({"media": MediaConfig}, {})
        _config_module.set_config(cfg)
        try:
            assert _config_module.get_section("media", MediaConfig).reset_minutes == 42
        finally:
            _config_module.set_config(None)

    def test_undeclared_section_fails(self, config_root) -> None:
        """申告していない名前は、名前を含む ValueError で落とす。"""
        config_root()
        cfg = compose_config({}, {})

        with pytest.raises(ValueError, match="Config section 'extensions.google' is not declared"):
            _config_module.get_section("google", MediaConfig, config=cfg)

    def test_wrong_model_fails(self, config_root) -> None:
        """実際の値が渡したモデルのインスタンスでなければ落とす。"""
        config_root("extensions:\n  media:\n    default: idle.png\n")
        cfg = compose_config({"media": MediaConfig}, {})

        with pytest.raises(ValueError, match="is a MediaConfig, not HabitsConfig"):
            _config_module.get_section("media", HabitsConfig, config=cfg)


class TestExtensionSectionName:
    """`extension_section_name()` による節名の導出。"""

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("google-oauth", "google_oauth"),
            ("lilla-agent", "lilla_agent"),
            ("habits", "habits"),
            ("a-b-c", "a_b_c"),
            ("google_oauth", "google_oauth"),
        ],
    )
    def test_replaces_hyphens_with_underscores(self, name: str, expected: str) -> None:
        """ハイフンだけをアンダースコアに置き換え、それ以外はそのまま返す。"""
        assert _config_module.extension_section_name(name) == expected
