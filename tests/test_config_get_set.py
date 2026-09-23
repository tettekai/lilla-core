"""`get_config()` / `set_config()` / `_default_config()` の挙動テスト。

`get_config()` は登録型のシングルトンに置き換えられており、以下 2 種類の
経路がある:

- `set_config(instance)` を呼んだ後は、`get_config()` はその同じインスタンスを返す
- `set_config()` が一度も呼ばれていなければ、`AppConfig()` のデフォルトを返す

`_default_config` は `@lru_cache(maxsize=1)` を持つため、`_config_instance` の
リセットだけでは `CONFIG_ROOT` 等の環境変数変更が反映されない（一度呼ばれると
古い結果を返し続ける）。テスト用フィクスチャは必ず `_config_instance = None` と
`_default_config.cache_clear()` の両方を行う。

`lilla_core.core.config` は他テスト（test_bot など）が sys.modules へ MagicMock を
注入するため、実クラスを確実に得る目的でファイルから直接ロードする。
"""

import importlib.util
import sys
from pathlib import Path

import pytest

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "src" / "lilla_core" / "core" / "config.py"
_REAL_CONFIG_MODULE_NAME = "_lilla_real_config_for_get_set_test"


def _load_real_config_module():
    """`src/lilla_core/core/config.py` を実クラスとして独立ロードする。"""
    spec = importlib.util.spec_from_file_location(_REAL_CONFIG_MODULE_NAME, _CONFIG_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[_REAL_CONFIG_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


_config_module = _load_real_config_module()


@pytest.fixture(autouse=True)
def reset_config_singleton():
    """`_config_instance` と `_default_config` のキャッシュを両方リセットする。

    `_default_config` は `@lru_cache(maxsize=1)` を持つため、`_config_instance`
    のリセットだけでは以降のテストで古い結果を返し続けてしまう。両方を
    セットで初期化する。
    """
    _config_module._config_instance = None
    _config_module._default_config.cache_clear()
    yield
    _config_module._config_instance = None
    _config_module._default_config.cache_clear()


@pytest.fixture
def isolated_config_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """必須項目だけを書いた lilla.yaml を持つ config_root を用意する。"""
    monkeypatch.setenv("CONFIG_ROOT", str(tmp_path))
    monkeypatch.setenv("DISCORD_TOKEN", "dummy")
    (tmp_path / "lilla.yaml").write_text(
        'discord:\n'
        '  my_user_id: "1"\n'
        "llm:\n"
        "  default: dummy\n"
        "  providers:\n"
        "    dummy:\n"
        "      type: ollama\n"
        "      url: http://localhost:11434\n"
        "      model: dummy\n",
        encoding="utf-8",
    )
    return tmp_path


class TestGetConfigWithoutSetConfig:
    """`set_config()` を呼ばずに `get_config()` を呼ぶ経路。"""

    def test_returns_appconfig_default_when_not_set(
        self, isolated_config_root: Path
    ) -> None:
        """`set_config()` 未呼び出しでも `AppConfig()` の既定が返る（例外にならない）。"""
        cfg = _config_module.get_config()
        assert isinstance(cfg, _config_module.AppConfig)
        # 拡張フィールドは AppConfig に無い
        assert not hasattr(cfg, "withings_client_id")

    def test_repeated_calls_return_same_default_instance(
        self, isolated_config_root: Path
    ) -> None:
        """`set_config()` 未呼び出し時、`get_config()` は同じインスタンスを返す（lru_cache）。"""
        assert _config_module.get_config() is _config_module.get_config()

    def test_extensions_content_is_ignored_when_not_composed(
        self, isolated_config_root: Path
    ) -> None:
        """合成を経ていなければ `extensions:` の中身は検証せず、コアの節だけ読める。

        拡張をロードしない運用スクリプトなどが、拡張の節を書いた `lilla.yaml` で
        落ちないようにするため（未知キーの検査は `compose_config()` の結果にだけ掛かる）。
        """
        yaml_file = isolated_config_root / "lilla.yaml"
        yaml_file.write_text(
            yaml_file.read_text(encoding="utf-8")
            + "extensions:\n  habits:\n    channel_id: x\n",
            encoding="utf-8",
        )

        cfg = _config_module.get_config()

        assert cfg.discord.my_user_id == "1"
        assert cfg.extensions.model_dump() == {}

    def test_plain_appconfig_still_rejects_unknown_extensions(
        self, isolated_config_root: Path
    ) -> None:
        """素の `AppConfig()`（拡張 0 個で合成した場合と同じ）は未知キーで落ちる。"""
        from pydantic import ValidationError

        yaml_file = isolated_config_root / "lilla.yaml"
        yaml_file.write_text(
            yaml_file.read_text(encoding="utf-8")
            + "extensions:\n  habits:\n    channel_id: x\n",
            encoding="utf-8",
        )

        with pytest.raises(ValidationError, match="extensions.habits"):
            _config_module.AppConfig()


class TestSetConfig:
    """`set_config()` 経由でインスタンスを差し込む経路。"""

    def test_get_config_returns_the_set_instance(
        self, isolated_config_root: Path
    ) -> None:
        """`set_config(x)` 後、`get_config()` は `x` を返す。"""
        instance = _config_module.AppConfig()
        _config_module.set_config(instance)
        assert _config_module.get_config() is instance

    def test_get_config_returns_same_instance_on_repeated_calls(
        self, isolated_config_root: Path
    ) -> None:
        """`set_config(x)` 後、`get_config()` を何回呼んでも同じ `x` を返す。"""
        instance = _config_module.AppConfig()
        _config_module.set_config(instance)
        assert _config_module.get_config() is _config_module.get_config()
        assert _config_module.get_config() is instance

    def test_set_config_overrides_previous(self, isolated_config_root: Path) -> None:
        """`set_config()` を複数回呼ぶと最後のインスタンスに切り替わる。"""
        first = _config_module.AppConfig()
        second = _config_module.AppConfig()
        _config_module.set_config(first)
        _config_module.set_config(second)
        assert _config_module.get_config() is second

    def test_set_config_accepts_subclass(self, isolated_config_root: Path) -> None:
        """`set_config()` は AppConfig のサブクラスも受け付ける（AppConfigEx 用途）。"""

        class SubConfig(_config_module.AppConfig):
            extra_field: str = "hello"

        instance = SubConfig()
        _config_module.set_config(instance)
        cfg = _config_module.get_config()
        assert cfg is instance
        assert cfg.extra_field == "hello"
