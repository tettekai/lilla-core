"""`lilla_core.testing`（拡張リポジトリ向けテストヘルパー）のテスト。"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from pydantic import BaseModel

from lilla_core.core.extension import Extension
from lilla_core.testing import use_extensions as use_extensions_cm
from lilla_core.testing import write_minimal_lilla_yaml
from lilla_core.testing.pytest_plugin import lilla_config_root, lilla_extensions


def config_module():
    """`use_extensions()` が実際に触る `lilla_core.core.config` を都度解決する。

    他のテストモジュールが `sys.modules` からコアのモジュールを取り除くことが
    あるため、import 時に束縛せず参照のたびに引き直す。
    """
    import lilla_core.core.config as module

    return module


def extension_module():
    """`use_extensions()` が実際に触る `lilla_core.core.extension` を都度解決する。"""
    import lilla_core.core.extension as module

    return module


def fixture_func(fixture):
    """`@pytest.fixture` が包んだ素の関数を取り出す。

    fixture 本体（teardown を含む）を直接呼んで検証するために使う。
    """
    return fixture.__wrapped__


class SampleSectionConfig(BaseModel):
    """テスト用の YAML セクションモデル（全フィールドにデフォルトあり）。"""

    value: str = "default"


def make_extension(name: str, **contributions) -> Extension:
    """指定した貢献だけを返す `Extension` インスタンスを組み立てる。"""
    ext = Extension()
    ext.name = name
    for method_name, value in contributions.items():
        setattr(ext, method_name, (lambda v: lambda *a, **k: v)(value))
    return ext


@pytest.fixture(autouse=True)
def _isolate_process_state():
    """登録・設定・OS 変数名レジストリはプロセス共有のため前後で復元する。"""
    cfg = config_module()
    ext = extension_module()
    saved = ext.get_extensions()
    saved_config = cfg._config_instance
    saved_var_names = dict(cfg._extra_env_var_names)
    yield
    ext.set_extensions(saved)
    cfg._config_instance = saved_config
    cfg._extra_env_var_names.clear()
    cfg._extra_env_var_names.update(saved_var_names)
    cfg._default_config.cache_clear()


@pytest.fixture
def config_root(tmp_path: Path) -> Path:
    """最小構成の `lilla.yaml` を置いたディレクトリを返す。"""
    write_minimal_lilla_yaml(tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# TestWriteMinimalLillaYaml
# ---------------------------------------------------------------------------


class TestWriteMinimalLillaYaml:
    """`write_minimal_lilla_yaml()` の出力。"""

    def test_writes_required_sections(self, tmp_path: Path) -> None:
        """コアが必須にしている項目とタイムゾーンを書き出す。"""
        path = write_minimal_lilla_yaml(tmp_path)

        assert path == tmp_path / "lilla.yaml"
        written = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert written["discord"]["my_user_id"] == "123456789"
        assert written["llm"]["default"] == "dummy"
        assert written["llm"]["providers"]["dummy"] == {
            "type": "ollama",
            "url": "http://localhost:11434",
            "model": "dummy",
        }
        assert written["ui"]["timezone"] == "Asia/Tokyo"

    def test_overrides_are_applied(self, tmp_path: Path) -> None:
        """`my_user_id` / `llm_name` を差し替えられる。"""
        path = write_minimal_lilla_yaml(tmp_path, my_user_id="42", llm_name="local")

        written = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert written["discord"]["my_user_id"] == "42"
        assert written["llm"]["default"] == "local"
        assert set(written["llm"]["providers"]) == {"local"}

    def test_extra_is_deep_merged(self, tmp_path: Path) -> None:
        """`extra` は同じネストで深いマージをする（既定の項目は消えない）。"""
        path = write_minimal_lilla_yaml(
            tmp_path,
            extra={
                "extensions": {"habits": {"channel": "habits-test"}},
                "discord": {"error_channel_id": 1},
                "ui": {"locale": "en"},
            },
        )

        written = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert written["extensions"] == {"habits": {"channel": "habits-test"}}
        assert written["discord"] == {"my_user_id": "123456789", "error_channel_id": 1}
        assert written["ui"] == {"timezone": "Asia/Tokyo", "locale": "en"}

    def test_written_yaml_loads_as_config(self, tmp_path: Path) -> None:
        """書き出した YAML だけで `AppConfig` が組み立てられる。"""
        write_minimal_lilla_yaml(tmp_path)

        with use_extensions_cm(config_root=tmp_path) as cfg:
            assert cfg.discord.my_user_id == "123456789"
            assert cfg.llm.default == "dummy"


# ---------------------------------------------------------------------------
# TestUseExtensions
# ---------------------------------------------------------------------------


class TestUseExtensions:
    """`use_extensions()` の入退場。"""

    def test_registers_extensions_and_composes_config(self, config_root: Path) -> None:
        """登録した拡張のセクションが合成され、`get_config()` から読める。"""
        ext = make_extension("pack", config_models={"alpha": SampleSectionConfig})

        with use_extensions_cm(ext, config_root=config_root) as cfg:
            assert [e.name for e in extension_module().get_extensions()] == ["pack"]
            assert cfg.extensions.alpha.value == "default"
            assert config_module().get_config() is cfg

    def test_env_field_is_composed(
        self, config_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`env_fields()` で申告した秘匿フィールドが `cfg.env` に乗る。"""
        monkeypatch.setenv("SAMPLE_SECRET", "s3cret")
        ext = make_extension("pack", env_fields={"sample_secret": "SAMPLE_SECRET"})

        with use_extensions_cm(ext, config_root=config_root) as cfg:
            assert cfg.env.sample_secret == "s3cret"

    def test_config_root_is_pointed_and_restored(self, config_root: Path) -> None:
        """`CONFIG_ROOT` は入っている間だけ差し替わり、抜けると元へ戻る。"""
        original = os.environ.get("CONFIG_ROOT")

        with use_extensions_cm(config_root=config_root):
            assert os.environ["CONFIG_ROOT"] == str(config_root)

        assert os.environ.get("CONFIG_ROOT") == original

    def test_config_root_is_removed_when_originally_unset(
        self, config_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """元々 `CONFIG_ROOT` が無ければ、抜けたあとも無い状態へ戻る。"""
        monkeypatch.delenv("CONFIG_ROOT", raising=False)

        with use_extensions_cm(config_root=config_root):
            assert os.environ["CONFIG_ROOT"] == str(config_root)

        assert "CONFIG_ROOT" not in os.environ

    def test_config_root_is_left_alone_when_omitted(self) -> None:
        """`config_root` を渡さなければ `CONFIG_ROOT` には触らない。"""
        current = os.environ.get("CONFIG_ROOT")

        with use_extensions_cm():
            assert os.environ.get("CONFIG_ROOT") == current

    def test_previous_registration_is_restored(self, config_root: Path) -> None:
        """抜けると入る前の登録内容に戻る。"""
        before = make_extension("before")
        extension_module().set_extensions([before])

        with use_extensions_cm(make_extension("inner"), config_root=config_root):
            assert [e.name for e in extension_module().get_extensions()] == ["inner"]

        assert extension_module().get_extensions() == [before]

    def test_empty_registration_is_restored(self, config_root: Path) -> None:
        """入る前が空なら、抜けたあとも空に戻る。"""
        extension_module().reset_extensions()

        with use_extensions_cm(make_extension("inner"), config_root=config_root):
            pass

        assert extension_module().get_extensions() == []

    def test_unset_config_instance_is_restored_to_none(self, config_root: Path) -> None:
        """入る前に設定が据えられていなければ、抜けたあとも未設定へ戻る。"""
        config_module()._config_instance = None

        with use_extensions_cm(config_root=config_root):
            assert config_module()._config_instance is not None

        assert config_module()._config_instance is None

    def test_existing_config_instance_is_restored(self, config_root: Path) -> None:
        """入る前の設定インスタンスをそのまま戻す。"""
        sentinel = config_module().AppConfig()
        config_module().set_config(sentinel)

        with use_extensions_cm(config_root=config_root):
            assert config_module().get_config() is not sentinel

        assert config_module().get_config() is sentinel

    def test_extra_env_var_names_are_restored(self, config_root: Path) -> None:
        """合成が書き換える OS 変数名のレジストリも元へ戻す。"""
        before = dict(config_module()._extra_env_var_names)
        ext = make_extension("pack", env_fields={"sample_secret": "SAMPLE_SECRET"})

        with use_extensions_cm(ext, config_root=config_root):
            assert config_module()._extra_env_var_names["sample_secret"] == "SAMPLE_SECRET"

        assert config_module()._extra_env_var_names == before

    def test_state_is_restored_on_exception(self, config_root: Path) -> None:
        """本体で例外が出ても登録・設定・`CONFIG_ROOT` は元へ戻る。"""
        before_extensions = extension_module().get_extensions()
        before_config = config_module()._config_instance
        before_root = os.environ.get("CONFIG_ROOT")

        with pytest.raises(RuntimeError, match="boom"):
            with use_extensions_cm(make_extension("pack"), config_root=config_root):
                raise RuntimeError("boom")

        assert extension_module().get_extensions() == before_extensions
        assert config_module()._config_instance is before_config
        assert os.environ.get("CONFIG_ROOT") == before_root

    def test_registration_failure_still_restores(self, config_root: Path) -> None:
        """登録の衝突検査で落ちても `CONFIG_ROOT` を残さない。"""
        before_root = os.environ.get("CONFIG_ROOT")

        with pytest.raises(ValueError, match="Duplicate"):
            with use_extensions_cm(
                make_extension("a", config_models={"alpha": SampleSectionConfig}),
                make_extension("b", config_models={"alpha": SampleSectionConfig}),
                config_root=config_root,
            ):
                pass

        assert os.environ.get("CONFIG_ROOT") == before_root

    def test_nested_use_restores_in_reverse_order(self, config_root: Path) -> None:
        """入れ子にしたら後入れ先出しで戻る。"""
        with use_extensions_cm(make_extension("outer"), config_root=config_root):
            with use_extensions_cm(make_extension("inner"), config_root=config_root):
                assert [e.name for e in extension_module().get_extensions()] == ["inner"]
            assert [e.name for e in extension_module().get_extensions()] == ["outer"]


# ---------------------------------------------------------------------------
# TestNoPytestDependency
# ---------------------------------------------------------------------------


class TestNoPytestDependency:
    """`lilla_core.testing` が pytest 無しの環境でも import できること。"""

    def test_import_succeeds_without_pytest(self) -> None:
        """`pytest` を import できない状態で reload しても失敗しない。"""
        import lilla_core.testing as testing_module

        # `None` を入れると `import pytest` が ImportError になる（本番依存に
        # pytest が混ざっていないことを固定するため）。
        with patch.dict(sys.modules, {"pytest": None}):
            with pytest.raises(ImportError):
                importlib.import_module("pytest")
            importlib.reload(testing_module)

        assert callable(testing_module.use_extensions)
        assert callable(testing_module.write_minimal_lilla_yaml)


# ---------------------------------------------------------------------------
# TestPytestPluginFixtures
# ---------------------------------------------------------------------------


class TestPytestPluginFixtures:
    """`lilla_core.testing.pytest_plugin` の fixture。"""

    def test_lilla_config_root_writes_yaml_and_sets_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`lilla.yaml` を書き、`CONFIG_ROOT` をそこへ向ける。"""
        result = fixture_func(lilla_config_root)(tmp_path, monkeypatch)

        assert result == tmp_path
        assert (tmp_path / "lilla.yaml").exists()
        assert os.environ["CONFIG_ROOT"] == str(tmp_path)
        assert os.environ["DISCORD_TOKEN"]

    def test_lilla_config_root_keeps_existing_discord_token(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """既に `DISCORD_TOKEN` があればダミー値で上書きしない。"""
        monkeypatch.setenv("DISCORD_TOKEN", "real-token")

        fixture_func(lilla_config_root)(tmp_path, monkeypatch)

        assert os.environ["DISCORD_TOKEN"] == "real-token"

    def test_lilla_extensions_registers_and_composes(self, config_root: Path) -> None:
        """`register(*extensions)` が合成済みの設定を返す。"""
        generator = fixture_func(lilla_extensions)(config_root)
        register = next(generator)

        cfg = register(make_extension("pack", config_models={"alpha": SampleSectionConfig}))

        assert cfg.extensions.alpha.value == "default"
        assert config_module().get_config() is cfg
        assert [e.name for e in extension_module().get_extensions()] == ["pack"]

        with pytest.raises(StopIteration):
            next(generator)

    def test_lilla_extensions_restores_on_teardown(self, config_root: Path) -> None:
        """teardown で登録も設定も元へ戻る（複数回呼んでも全部戻る）。"""
        extension_module().reset_extensions()
        config_module()._config_instance = None

        generator = fixture_func(lilla_extensions)(config_root)
        register = next(generator)
        register(make_extension("first"))
        register(make_extension("second", config_models={"alpha": SampleSectionConfig}))
        assert [e.name for e in extension_module().get_extensions()] == ["second"]

        with pytest.raises(StopIteration):
            next(generator)

        assert extension_module().get_extensions() == []
        assert config_module()._config_instance is None
