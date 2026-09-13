"""`lilla_core.core.extension` の `Extension` とロード・参照 API のテスト。"""
from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel

from lilla_core.core import extension as ext_module
from lilla_core.core.extension import Extension


def config_module():
    """`load_extensions()` が実際に触る `lilla_core.core.config` を都度解決する。

    他のテストモジュールが `sys.modules` から `lilla_core.core.config` を
    取り除くことがあり、その後の再 import では別のモジュールオブジェクトに
    なる。import 時に束縛した参照では `set_config()` の相手とズレるため、
    参照のたびに `sys.modules` から引き直す。
    """
    import lilla_core.core.config as module

    return module


@pytest.fixture(autouse=True)
def _isolate_registry():
    """登録内容はプロセス全体で共有されるため、テストごとに前後で復元する。

    `load_extensions()` は設定の合成まで行い `set_config()` するため、拡張の
    登録内容だけでなくプロセスの設定インスタンスと OS 変数名のレジストリも
    元へ戻す。
    """
    cfg = config_module()
    saved = ext_module.get_extensions()
    saved_config = cfg._config_instance
    saved_var_names = dict(cfg._extra_env_var_names)
    ext_module.reset_extensions()
    yield
    ext_module.set_extensions(saved)
    cfg._config_instance = saved_config
    cfg._extra_env_var_names.clear()
    cfg._extra_env_var_names.update(saved_var_names)
    cfg._default_config.cache_clear()


class SampleSectionConfig(BaseModel):
    """テスト用の YAML セクションモデル（全フィールドにデフォルトあり）。"""

    value: str = "default"


def _make_module(name: str, attrs: dict) -> ModuleType:
    """`sys.modules` へ差し込むためのダミーモジュールを組み立てる。"""
    module = ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    return module


@pytest.fixture
def register_module(monkeypatch: pytest.MonkeyPatch):
    """import パスに対応するダミーモジュールを `sys.modules` へ差し込むヘルパー。"""
    def _register(name: str, **attrs) -> ModuleType:
        module = _make_module(name, attrs)
        monkeypatch.setitem(sys.modules, name, module)
        return module

    return _register


# ---------------------------------------------------------------------------
# TestExtensionDefaults
# ---------------------------------------------------------------------------


class TestExtensionDefaults:
    """貢献・フックのデフォルトが「空・何もしない」であること。"""

    def test_contribution_defaults_are_empty(self) -> None:
        """オーバーライドしなければ何も貢献しない。"""
        ext = Extension()
        assert ext.config_models() == {}
        assert ext.env_fields() == {}
        assert ext.tool_roots() == []
        assert ext.command_packages() == []
        assert ext.startup_repos() == []
        assert ext.tool_context_providers() == {}
        assert ext.result_deliveries() == {}
        assert ext.client_prompt_providers() == {}
        assert ext.conversation_start_hooks() == {}
        assert ext.requires == ()
        assert ext.api_version == ext_module.EXTENSION_API_VERSION
        assert ext.required_env_fields() == []
        assert ext.required_tool_context_keys() == []

    async def test_on_message_default_is_false(self) -> None:
        """デフォルトの `on_message` は「処理しなかった」を返す。"""
        assert await Extension().on_message(MagicMock()) is False

    async def test_setup_default_does_nothing(self) -> None:
        """デフォルトの `setup` は何もせず None を返す。"""
        ctx = ext_module.SetupContext(tools={}, llm_tools={}, bot=MagicMock(), config=MagicMock())
        assert await Extension().setup(ctx) is None


# ---------------------------------------------------------------------------
# TestLoadExtensions
# ---------------------------------------------------------------------------


class TestLoadExtensions:
    def test_empty_spec_loads_nothing(self) -> None:
        """未設定・空なら 0 個（コア単体起動）。"""
        assert ext_module.load_extensions("") == []
        assert ext_module.get_extensions() == []

    def test_reads_env_var_when_spec_is_none(
        self, register_module, make_extension, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`spec` 省略時は `LILLA_EXTENSIONS` を読む。"""
        register_module("dummy_pack", extension=make_extension("pack"))
        monkeypatch.setenv(ext_module.EXTENSIONS_ENV_VAR, "dummy_pack")

        loaded = ext_module.load_extensions()

        assert [e.name for e in loaded] == ["pack"]

    def test_loads_multiple_modules_in_order(
        self, register_module, make_extension
    ) -> None:
        """カンマ区切りで複数のモジュールを順に読む。"""
        register_module("pack_a", extension=make_extension("a"))
        register_module("pack_b", extension=make_extension("b"))

        loaded = ext_module.load_extensions("pack_a, pack_b")

        assert [e.name for e in loaded] == ["a", "b"]

    def test_missing_module_raises(self) -> None:
        """存在しないモジュールを指定すると起動が失敗する。"""
        with pytest.raises(ImportError):
            ext_module.load_extensions("no_such_extension_module")

    def test_module_without_extension_attr_raises(self, register_module) -> None:
        """`extension` 属性が無いモジュールは fail-fast。"""
        register_module("bad_pack", something_else=object())

        with pytest.raises(AttributeError, match="must export"):
            ext_module.load_extensions("bad_pack")

    def test_module_with_wrong_type_raises(self, register_module) -> None:
        """`extension` が `Extension` インスタンスでなければ fail-fast。"""
        register_module("bad_pack", extension=object())

        with pytest.raises(TypeError, match="Extension instance"):
            ext_module.load_extensions("bad_pack")

    def test_same_module_twice_raises_on_duplicate_name(
        self, register_module, make_extension
    ) -> None:
        """同じモジュールを 2 度並べると name 重複で落ちる。"""
        register_module("pack_a", extension=make_extension("a"))

        with pytest.raises(ValueError, match="Duplicate extension name"):
            ext_module.load_extensions("pack_a,pack_a")


# ---------------------------------------------------------------------------
# TestValidation
# ---------------------------------------------------------------------------


class TestValidation:
    def test_missing_name_raises(self) -> None:
        """`name` 未設定は型注釈では防げないのでロード時に落とす。"""
        with pytest.raises(ValueError, match="non-empty 'name'"):
            ext_module.set_extensions([Extension()])

    def test_blank_name_raises(self, make_extension) -> None:
        """空白だけの `name` も落とす。"""
        with pytest.raises(ValueError, match="non-empty 'name'"):
            ext_module.set_extensions([make_extension("   ")])

    def test_duplicate_name_raises(self, make_extension) -> None:
        """`name` の重複は fail-fast。"""
        with pytest.raises(ValueError, match="Duplicate extension name"):
            ext_module.set_extensions([make_extension("same"), make_extension("same")])

    @pytest.mark.parametrize("key", ["llm_tools", "client_type", "now", "discord_client", "params", "call_tool"])
    def test_core_tool_context_key_is_reserved(self, make_extension, key: str) -> None:
        """コアが注入する context キーは拡張から提供できない（静かな上書きを防ぐ）。"""
        ext = make_extension("a", tool_context_providers={key: lambda: 1})

        with pytest.raises(ValueError, match=f"reserved key '{key}'"):
            ext_module.set_extensions([ext])

    def test_duplicate_tool_context_key_raises(self, make_extension) -> None:
        """ツール context プロバイダのキー重複は fail-fast。"""
        first = make_extension("a", tool_context_providers={"client": lambda: 1})
        second = make_extension("b", tool_context_providers={"client": lambda: 2})

        with pytest.raises(ValueError, match="Duplicate tool context provider key"):
            ext_module.set_extensions([first, second])

    def test_client_prompt_providers_are_concatenated_in_load_order(
        self, make_extension
    ) -> None:
        """同じ client_type のプロンプトプロバイダは排他にせず、ロード順に連結する。"""
        p_a, p_b = (lambda: "a"), (lambda: "b")
        first = make_extension("a", client_prompt_providers={"web": [p_a]})
        second = make_extension("b", client_prompt_providers={"web": [p_b]})

        ext_module.set_extensions([first, second])

        assert ext_module.get_client_prompt_providers("web") == [p_a, p_b]

    def test_conversation_start_hooks_are_concatenated_in_load_order(
        self, make_extension
    ) -> None:
        """同じ client_type の会話開始フックは排他にせず、ロード順に連結する。"""
        h_a, h_b = AsyncMock(), AsyncMock()
        first = make_extension("a", conversation_start_hooks={"web": [h_a]})
        second = make_extension("b", conversation_start_hooks={"web": [h_b, h_a]})

        ext_module.set_extensions([first, second])

        assert ext_module.get_conversation_start_hooks("web") == [h_a, h_b, h_a]

    def test_client_prompt_provider_must_be_a_list(self, make_extension) -> None:
        """旧契約（キー -> 関数 1 つ）のまま返した拡張はロード時に落とす。"""
        ext = make_extension("a", client_prompt_providers={"web": lambda: "x"})

        with pytest.raises(ValueError, match="must return a list for client prompt provider"):
            ext_module.set_extensions([ext])

    def test_conversation_start_hook_must_be_a_list(self, make_extension) -> None:
        """旧契約（キー -> 関数 1 つ）のまま返した拡張はロード時に落とす。"""
        ext = make_extension("a", conversation_start_hooks={"web": AsyncMock()})

        with pytest.raises(ValueError, match="must return a list for conversation start hook"):
            ext_module.set_extensions([ext])

    def test_duplicate_result_delivery_raises(self, make_extension) -> None:
        """result delivery の client_type 重複は fail-fast。"""
        first = make_extension("a", result_deliveries={"web": AsyncMock()})
        second = make_extension("b", result_deliveries={"web": AsyncMock()})

        with pytest.raises(ValueError, match="Duplicate result delivery key"):
            ext_module.set_extensions([first, second])

    def test_discord_result_delivery_is_reserved(self, make_extension) -> None:
        """"discord" はコアが配送を持つ予約キーで、拡張は登録できない。"""
        ext = make_extension("a", result_deliveries={"discord": AsyncMock()})

        with pytest.raises(ValueError, match="reserved key"):
            ext_module.set_extensions([ext])

    def test_discord_client_prompt_is_not_reserved(self, make_extension) -> None:
        """"discord" のプロンプトは内蔵デフォルトがあるだけで、上書きは許す。"""
        provider = lambda: "custom"
        ext_module.set_extensions(
            [make_extension("a", client_prompt_providers={"discord": [provider]})]
        )

        assert ext_module.get_client_prompt_providers("discord") == [provider]

    def test_nothing_is_registered_when_validation_fails(self, make_extension) -> None:
        """検証に失敗したら登録内容を一切残さない。"""
        ext_module.set_extensions([make_extension("ok", tool_context_providers={"a": lambda: 1})])

        with pytest.raises(ValueError):
            ext_module.set_extensions([make_extension("dup"), make_extension("dup")])

        assert [e.name for e in ext_module.get_extensions()] == ["ok"]
        assert "a" in ext_module.get_tool_context_providers()


# ---------------------------------------------------------------------------
# TestConfigContributions
# ---------------------------------------------------------------------------


class TestConfigContributions:
    """`config_models()` / `env_fields()` のマージと衝突検査。"""

    def test_contributions_are_merged_in_load_order(
        self, make_extension, use_extensions
    ) -> None:
        """複数の拡張の申告が 1 つの dict へまとまる。"""
        use_extensions(
            make_extension("a", config_models={"alpha": SampleSectionConfig}),
            make_extension(
                "b",
                config_models={"beta": SampleSectionConfig},
                env_fields={"beta_secret": "BETA_SECRET"},
            ),
        )

        assert ext_module.get_config_models() == {
            "alpha": SampleSectionConfig,
            "beta": SampleSectionConfig,
        }
        assert ext_module.get_env_fields() == {"beta_secret": "BETA_SECRET"}

    def test_duplicate_section_between_extensions_raises(self, make_extension) -> None:
        """同じセクション名を 2 つの拡張が提供したら fail-fast する。"""
        with pytest.raises(ValueError, match="Duplicate config model key 'alpha'"):
            ext_module.set_extensions([
                make_extension("a", config_models={"alpha": SampleSectionConfig}),
                make_extension("b", config_models={"alpha": SampleSectionConfig}),
            ])

    def test_duplicate_env_field_between_extensions_raises(self, make_extension) -> None:
        """同じ env フィールド名を 2 つの拡張が提供したら fail-fast する。"""
        with pytest.raises(ValueError, match="Duplicate env field key 'shared_secret'"):
            ext_module.set_extensions([
                make_extension("a", env_fields={"shared_secret": "SHARED_SECRET"}),
                make_extension("b", env_fields={"shared_secret": "OTHER_SECRET"}),
            ])

    def test_core_section_name_is_reserved(self, make_extension) -> None:
        """コア確定のセクション名は拡張から提供できない。"""
        with pytest.raises(ValueError, match="reserved key 'discord'"):
            ext_module.set_extensions([
                make_extension("a", config_models={"discord": SampleSectionConfig}),
            ])

    def test_core_env_field_name_is_reserved(self, make_extension) -> None:
        """コア確定の `EnvConfig` フィールド名は拡張から提供できない。"""
        with pytest.raises(ValueError, match="reserved key 'discord_token'"):
            ext_module.set_extensions([
                make_extension("a", env_fields={"discord_token": "DISCORD_TOKEN"}),
            ])

    def test_set_extensions_does_not_replace_process_config(
        self, make_extension, use_extensions
    ) -> None:
        """`set_extensions()` は登録と検証だけで、プロセスの設定を差し替えない。"""
        sentinel = config_module().get_config()

        use_extensions(make_extension("a", config_models={"alpha": SampleSectionConfig}))

        assert config_module().get_config() is sentinel


# ---------------------------------------------------------------------------
# TestApiVersion
# ---------------------------------------------------------------------------


class TestApiVersion:
    """`api_version`（拡張が書かれた契約バージョン）の検証。"""

    def test_current_version_is_supported(self) -> None:
        """現在の契約バージョンは受け付ける集合に含まれる。"""
        assert ext_module.EXTENSION_API_VERSION in ext_module.SUPPORTED_EXTENSION_API_VERSIONS

    def test_default_passes(self, make_extension, use_extensions) -> None:
        """宣言しない拡張は現在のバージョン扱いで通る。"""
        use_extensions(make_extension("a"))

        assert [e.name for e in ext_module.get_extensions()] == ["a"]

    def test_explicit_current_version_passes(self, make_extension, use_extensions) -> None:
        """現在のバージョンを明示しても通る。"""
        ext = make_extension("a")
        ext.api_version = ext_module.EXTENSION_API_VERSION

        use_extensions(ext)

        assert [e.name for e in ext_module.get_extensions()] == ["a"]

    def test_unsupported_version_fails_fast(self, make_extension) -> None:
        """受け付けないバージョンは、拡張名と両方のバージョンを含むエラーで落とす。"""
        ext = make_extension("future-pack")
        ext.api_version = ext_module.EXTENSION_API_VERSION + 1

        with pytest.raises(
            ValueError,
            match=f"Extension 'future-pack' declares api_version "
                  f"{ext_module.EXTENSION_API_VERSION + 1}, but this lilla-core supports",
        ):
            ext_module.set_extensions([ext])

    @pytest.mark.parametrize("bad", ["1", 1.0, None, True])
    def test_non_int_version_fails_fast(self, make_extension, bad) -> None:
        """整数以外（文字列・浮動小数・None・bool）は落とす。"""
        ext = make_extension("a")
        ext.api_version = bad

        with pytest.raises(ValueError, match="must define 'api_version' as an int"):
            ext_module.set_extensions([ext])

    def test_nothing_is_registered_when_version_check_fails(self, make_extension) -> None:
        """バージョン検証に失敗しても、先に登録済みの内容は壊さない。"""
        ext_module.set_extensions([make_extension("ok")])
        ext = make_extension("b")
        ext.api_version = 999

        with pytest.raises(ValueError):
            ext_module.set_extensions([ext])

        assert [e.name for e in ext_module.get_extensions()] == ["ok"]


# ---------------------------------------------------------------------------
# TestRequires
# ---------------------------------------------------------------------------


class TestRequires:
    """`requires`（依存する拡張名）の検証。"""

    def test_dependency_listed_before_is_accepted(self, make_extension, use_extensions) -> None:
        """依存先が自分より前に並んでいれば通る。"""
        oauth = make_extension("lilla-google-oauth")
        calendar = make_extension("lilla-google-calendar", requires=("lilla-google-oauth",))
        calendar.requires = ("lilla-google-oauth",)

        use_extensions(oauth, calendar)

        assert [e.name for e in ext_module.get_extensions()] == [
            "lilla-google-oauth", "lilla-google-calendar",
        ]

    def test_missing_dependency_fails_fast(self, make_extension) -> None:
        """依存先がロードされていなければ、要求元と依存先の名前つきで落とす。"""
        calendar = make_extension("lilla-google-calendar")
        calendar.requires = ("lilla-google-oauth",)

        with pytest.raises(
            ValueError,
            match="Extension 'lilla-google-calendar' requires extension 'lilla-google-oauth', "
                  "but it is not listed in LILLA_EXTENSIONS",
        ):
            ext_module.set_extensions([calendar])

    def test_dependency_listed_after_fails_fast(self, make_extension) -> None:
        """依存先が自分より後ろに並んでいれば落とす（自動並べ替えはしない）。"""
        oauth = make_extension("lilla-google-oauth")
        calendar = make_extension("lilla-google-calendar")
        calendar.requires = ("lilla-google-oauth",)

        with pytest.raises(ValueError, match="which must be listed before it"):
            ext_module.set_extensions([calendar, oauth])

    def test_self_dependency_fails_fast(self, make_extension) -> None:
        """自分自身への依存は「前に並んでいない」として落とす。"""
        ext = make_extension("a")
        ext.requires = ("a",)

        with pytest.raises(ValueError, match="which must be listed before it"):
            ext_module.set_extensions([ext])

    def test_requires_must_be_a_tuple_of_names(self, make_extension) -> None:
        """文字列 1 つをそのまま書いた場合（タプルにし忘れ）は落とす。"""
        ext = make_extension("a")
        ext.requires = "lilla-google-oauth"

        with pytest.raises(ValueError, match="must define 'requires' as a tuple"):
            ext_module.set_extensions([ext])

    def test_list_is_accepted_as_requires(self, make_extension, use_extensions) -> None:
        """リストで書いても通る。"""
        base = make_extension("base")
        ext = make_extension("b")
        ext.requires = ["base"]

        use_extensions(base, ext)

        assert [e.name for e in ext_module.get_extensions()] == ["base", "b"]

    def test_nothing_is_registered_when_requires_fails(self, make_extension) -> None:
        """依存の検証に失敗したら登録内容を一切残さない。"""
        ext_module.set_extensions([make_extension("ok", tool_context_providers={"a": lambda: 1})])
        broken = make_extension("b")
        broken.requires = ("missing",)

        with pytest.raises(ValueError):
            ext_module.set_extensions([broken])

        assert [e.name for e in ext_module.get_extensions()] == ["ok"]
        assert set(ext_module.get_tool_context_providers()) == {"a"}


# ---------------------------------------------------------------------------
# TestRequiredEnvFields / TestRequiredToolContextKeys
# ---------------------------------------------------------------------------


class TestRequiredEnvFields:
    """`required_env_fields()` の存在検査。"""

    def test_field_provided_by_another_extension_is_accepted(
        self, make_extension, use_extensions
    ) -> None:
        """他の拡張が `env_fields()` で提供していれば要求できる。"""
        use_extensions(
            make_extension("consumer", required_env_fields=["google_client_secret"]),
            make_extension("provider", env_fields={"google_client_secret": "GOOGLE_CLIENT_SECRET"}),
        )

        assert ext_module.get_env_fields() == {"google_client_secret": "GOOGLE_CLIENT_SECRET"}

    def test_core_field_is_always_available(self, make_extension, use_extensions) -> None:
        """コア確定の `EnvConfig` フィールドは誰も提供しなくても要求できる。"""
        use_extensions(make_extension("consumer", required_env_fields=["discord_token"]))

        assert ext_module.get_env_fields() == {}

    def test_unprovided_field_fails_fast(self, make_extension) -> None:
        """誰も提供していないフィールドを要求したら、要求元の名前つきで落とす。"""
        with pytest.raises(
            ValueError,
            match="Extension 'consumer' requires env field 'google_client_secret'",
        ):
            ext_module.set_extensions([
                make_extension("consumer", required_env_fields=["google_client_secret"]),
            ])


class TestRequiredToolContextKeys:
    """`required_tool_context_keys()` の存在検査。"""

    def test_key_provided_by_another_extension_is_accepted(
        self, make_extension, use_extensions
    ) -> None:
        """他の拡張が `tool_context_providers()` で提供していれば要求できる。"""
        provider = lambda: "client"
        use_extensions(
            make_extension("consumer", required_tool_context_keys=["google_client"]),
            make_extension("provider", tool_context_providers={"google_client": provider}),
        )

        assert ext_module.get_tool_context_providers() == {"google_client": provider}

    @pytest.mark.parametrize("key", ["client_type", "llm_tools", "call_tool", "client_state"])
    def test_core_runtime_key_is_always_available(
        self, make_extension, use_extensions, key: str
    ) -> None:
        """コアが実行時に注入する共通キーは誰も提供しなくても要求できる。"""
        use_extensions(make_extension("consumer", required_tool_context_keys=[key]))

        assert ext_module.get_tool_context_providers() == {}

    def test_unprovided_key_fails_fast(self, make_extension) -> None:
        """誰も提供していないキーを要求したら、要求元の名前つきで落とす。"""
        with pytest.raises(
            ValueError,
            match="Extension 'consumer' requires tool context key 'google_client'",
        ):
            ext_module.set_extensions([
                make_extension("consumer", required_tool_context_keys=["google_client"]),
            ])


# ---------------------------------------------------------------------------
# TestRequiredConfigSections
# ---------------------------------------------------------------------------


class TestRequiredConfigSections:
    """`required_config_sections()` の存在検査。"""

    def test_section_provided_by_another_extension_is_accepted(
        self, make_extension, use_extensions
    ) -> None:
        """他の拡張が提供していれば要求できる（提供側の並び順は問わない）。"""
        use_extensions(
            make_extension("consumer", required_config_sections=["alpha"]),
            make_extension("provider", config_models={"alpha": SampleSectionConfig}),
        )

        assert ext_module.get_config_models() == {"alpha": SampleSectionConfig}

    def test_core_section_is_always_available(self, make_extension, use_extensions) -> None:
        """コア確定のセクションは誰も提供しなくても要求できる。"""
        use_extensions(make_extension("consumer", required_config_sections=["prompt"]))

        assert ext_module.get_config_models() == {}

    def test_unprovided_section_fails_fast(self, make_extension) -> None:
        """誰も提供していないセクションを要求したら、要求元の名前つきで落とす。"""
        with pytest.raises(
            ValueError,
            match="Extension 'consumer' requires config section 'google'",
        ):
            ext_module.set_extensions([
                make_extension("consumer", required_config_sections=["google"]),
            ])


# ---------------------------------------------------------------------------
# TestLoadExtensionsComposesConfig
# ---------------------------------------------------------------------------


class TestLoadExtensionsComposesConfig:
    """`load_extensions()` が申告を合成してプロセスの設定に据えること。"""

    def test_declared_section_is_readable_after_load(
        self, register_module, make_extension
    ) -> None:
        """ロード後の `get_config()` で拡張のセクションが読める。"""
        register_module(
            "pack_cfg",
            extension=make_extension("cfg", config_models={"alpha": SampleSectionConfig}),
        )

        ext_module.load_extensions("pack_cfg")

        assert config_module().get_config().alpha.value == "default"

    def test_declared_env_field_is_readable_after_load(
        self, register_module, make_extension, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ロード後の `get_config().env` で拡張の秘匿フィールドが読める。"""
        monkeypatch.setenv("SAMPLE_SECRET", "s3cret")
        register_module(
            "pack_env",
            extension=make_extension("env", env_fields={"sample_secret": "SAMPLE_SECRET"}),
        )

        ext_module.load_extensions("pack_env")

        assert config_module().get_config().env.sample_secret == "s3cret"

    def test_composition_overrides_import_side_effect_set_config(
        self, register_module, make_extension
    ) -> None:
        """拡張が import 副作用で差し込んだ設定は、合成結果で上書きされる。"""
        host_instance = config_module().AppConfig()
        config_module().set_config(host_instance)
        register_module("pack_cfg", extension=make_extension("cfg"))

        ext_module.load_extensions("pack_cfg")

        assert config_module().get_config() is not host_instance

    def test_zero_extensions_still_set_a_plain_config(self) -> None:
        """拡張 0 個でも素の `AppConfig` が据えられる（コア単体起動）。"""
        ext_module.load_extensions("")

        assert type(config_module().get_config()) is config_module().AppConfig


# ---------------------------------------------------------------------------
# TestAggregatedContributions
# ---------------------------------------------------------------------------


class TestAggregatedContributions:
    def test_startup_repos_are_concatenated_in_load_order(self, make_extension) -> None:
        """起動時リポジトリはロード順に連結される。"""
        first, second, third = MagicMock(), MagicMock(), MagicMock()
        ext_module.set_extensions([
            make_extension("a", startup_repos=[first, second]),
            make_extension("b", startup_repos=[third]),
        ])

        assert ext_module.get_startup_repos() == [first, second, third]

    def test_tool_roots_are_concatenated_in_load_order(self, make_extension) -> None:
        """ツール探索ルートはロード順に連結される。"""
        ext_module.set_extensions([
            make_extension("a", tool_roots=[Path("/one")]),
            make_extension("b", tool_roots=[Path("/two")]),
        ])

        assert ext_module.get_tool_roots() == [Path("/one"), Path("/two")]

    def test_command_packages_drop_duplicates(self, make_extension) -> None:
        """同じパッケージを複数の拡張が指定しても 1 度だけ返す。"""
        ext_module.set_extensions([
            make_extension("a", command_packages=["pkg.one", "pkg.two"]),
            make_extension("b", command_packages=["pkg.two", "pkg.three"]),
        ])

        assert ext_module.get_command_packages() == ["pkg.one", "pkg.two", "pkg.three"]

    def test_lookups_return_empty_when_unregistered(self) -> None:
        """未登録の client_type は、排他の参照では None、加算式の参照では空リスト。"""
        assert ext_module.get_result_delivery("web") is None
        assert ext_module.get_client_prompt_providers("web") == []
        assert ext_module.get_conversation_start_hooks("web") == []

    def test_hook_lists_are_copies(self, make_extension) -> None:
        """加算式の参照の戻り値を書き換えても登録内容には影響しない。"""
        hook = AsyncMock()
        ext_module.set_extensions(
            [make_extension("a", conversation_start_hooks={"web": [hook]})]
        )

        ext_module.get_conversation_start_hooks("web").clear()

        assert ext_module.get_conversation_start_hooks("web") == [hook]

    def test_conversation_context_is_immutable(self) -> None:
        """`ConversationContext` は frozen で、フックが中身を差し替えられない。"""
        ctx = ext_module.ConversationContext(client_type="web")

        assert ctx.client_state is None
        with pytest.raises(Exception):
            ctx.client_state = {"x"}  # type: ignore[misc]

    def test_tool_context_providers_returns_a_copy(self, make_extension) -> None:
        """戻り値を書き換えても登録内容には影響しない。"""
        ext_module.set_extensions([make_extension("a", tool_context_providers={"x": lambda: 1})])

        providers = ext_module.get_tool_context_providers()
        providers["y"] = lambda: 2

        assert "y" not in ext_module.get_tool_context_providers()


# ---------------------------------------------------------------------------
# TestRunSetupHooks
# ---------------------------------------------------------------------------


class TestRunSetupHooks:
    async def test_awaits_each_setup_in_load_order(self, make_extension) -> None:
        """`setup()` はロード順に await される。"""
        order = []
        first = AsyncMock(side_effect=lambda *_a: order.append("a"))
        second = AsyncMock(side_effect=lambda *_a: order.append("b"))
        ext_module.set_extensions([
            make_extension("a", setup=first),
            make_extension("b", setup=second),
        ])

        tools, llm_tools, bot = {}, {}, MagicMock()
        await ext_module.run_setup_hooks(tools, llm_tools, bot)

        assert order == ["a", "b"]
        first.assert_awaited_once()
        (ctx,), _ = first.await_args
        assert isinstance(ctx, ext_module.SetupContext)
        assert ctx.tools is tools
        assert ctx.llm_tools is llm_tools
        assert ctx.bot is bot

    async def test_context_carries_process_config(self, make_extension) -> None:
        """`SetupContext.config` は呼び出し時点の `get_config()` と同じインスタンス。"""
        from lilla_core.core.config import get_config

        setup = AsyncMock()
        ext_module.set_extensions([make_extension("a", setup=setup)])

        await ext_module.run_setup_hooks({}, {}, MagicMock())

        (ctx,), _ = setup.await_args
        assert ctx.config is get_config()

    async def test_same_context_is_shared_by_all_extensions(self, make_extension) -> None:
        """全拡張へ同じ `SetupContext` インスタンスを渡す。"""
        first, second = AsyncMock(), AsyncMock()
        ext_module.set_extensions([
            make_extension("a", setup=first),
            make_extension("b", setup=second),
        ])

        await ext_module.run_setup_hooks({}, {}, MagicMock())

        assert first.await_args.args[0] is second.await_args.args[0]

    async def test_context_is_immutable(self) -> None:
        """`SetupContext` は frozen で、拡張が中身を差し替えられない。"""
        ctx = ext_module.SetupContext(tools={}, llm_tools={}, bot=MagicMock(), config=MagicMock())

        with pytest.raises(Exception):
            ctx.tools = {"x": 1}  # type: ignore[misc]

    async def test_exception_propagates(self, make_extension) -> None:
        """`setup()` の例外は握りつぶさず起動を止める。"""
        ext_module.set_extensions([
            make_extension("a", setup=AsyncMock(side_effect=RuntimeError("boom")))
        ])

        with pytest.raises(RuntimeError, match="boom"):
            await ext_module.run_setup_hooks({}, {}, MagicMock())


# ---------------------------------------------------------------------------
# TestDispatchOnMessage
# ---------------------------------------------------------------------------


class TestDispatchOnMessage:
    @pytest.fixture
    def mock_notify_error(self, monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
        """エラー通知をモックに差し替える（遅延 import 先を直接差し替える）。"""
        from lilla_core.core import error_notify

        mock = AsyncMock()
        monkeypatch.setattr(error_notify, "notify_error", mock)
        return mock

    async def test_returns_false_without_extensions(self) -> None:
        """拡張が 0 個なら常に False で、通常フローへ進む。"""
        assert await ext_module.dispatch_on_message(MagicMock()) is False

    async def test_chains_until_one_returns_true(self, make_extension) -> None:
        """先の拡張が True を返したら後続は呼ばれない。"""
        first = AsyncMock(return_value=True)
        second = AsyncMock(return_value=False)
        ext_module.set_extensions([
            make_extension("a", on_message=first),
            make_extension("b", on_message=second),
        ])

        assert await ext_module.dispatch_on_message(MagicMock()) is True
        first.assert_awaited_once()
        second.assert_not_awaited()

    async def test_falls_through_when_all_return_false(self, make_extension) -> None:
        """全員が False なら False（通常フローへ進む）。"""
        first = AsyncMock(return_value=False)
        second = AsyncMock(return_value=False)
        ext_module.set_extensions([
            make_extension("a", on_message=first),
            make_extension("b", on_message=second),
        ])

        assert await ext_module.dispatch_on_message(MagicMock()) is False
        first.assert_awaited_once()
        second.assert_awaited_once()

    async def test_exception_stops_chain_and_normal_flow(
        self, make_extension, mock_notify_error: AsyncMock
    ) -> None:
        """例外時は後続も通常フローも進まない（True を返す）。"""
        failing = AsyncMock(side_effect=RuntimeError("boom"))
        later = AsyncMock(return_value=False)
        ext_module.set_extensions([
            make_extension("a", on_message=failing),
            make_extension("b", on_message=later),
        ])

        assert await ext_module.dispatch_on_message(MagicMock()) is True
        later.assert_not_awaited()

    async def test_exception_notifies_error(
        self, make_extension, mock_notify_error: AsyncMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """例外は ERROR ログとエラー通知チャンネルの両方へ出す。"""
        error = RuntimeError("boom")
        ext_module.set_extensions([
            make_extension("broken-pack", on_message=AsyncMock(side_effect=error))
        ])
        bot = MagicMock()

        with caplog.at_level("ERROR"):
            await ext_module.dispatch_on_message(MagicMock(), bot)

        assert "broken-pack" in caplog.text
        mock_notify_error.assert_awaited_once()
        assert mock_notify_error.await_args[0][0] is bot
        assert "broken-pack" in mock_notify_error.await_args[0][1]
        assert mock_notify_error.await_args[0][2] is error
