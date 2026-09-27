"""`type: resolver` の LLM プロバイダー（設定の検証・`services/llm_resolver.py`）のテスト。"""
from __future__ import annotations

import importlib
import importlib.util
import logging
import shutil
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

_BASE_YAML = """\
discord:
  my_user_id: "1"
  channels:
    - name: study
      channel_id: "555"
llm:
  default: {default}
  providers:
    low:
      type: openai_compat
      url: https://example.invalid
      model: flash
      extra_params:
        reasoning_effort: low
    high:
      type: openai_compat
      url: https://example.invalid
      model: flash
      extra_params:
        reasoning_effort: high
    local:
      type: ollama
      url: http://localhost:11434
      model: gemma
{extra}
"""

_ROUTER_YAML = """\
    router:
      type: resolver
      script: {script}
      fallback: {fallback}
{more}"""


@pytest.fixture
def modules():
    """config と、読み直した llm_resolver を返す（他テストの差し替えの影響を受けない）。"""
    sys.modules.pop("lilla_core.services.llm_resolver", None)
    config_mod = importlib.import_module("lilla_core.core.config")
    resolver_mod = importlib.import_module("lilla_core.services.llm_resolver")
    resolver_mod.clear_cache()
    yield config_mod, resolver_mod
    resolver_mod.clear_cache()
    sys.modules.pop("lilla_core.services.llm_resolver", None)


@pytest.fixture
def config_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """`CONFIG_ROOT` を tmp_path へ向ける。"""
    root = tmp_path / "config"
    root.mkdir()
    monkeypatch.setenv("CONFIG_ROOT", str(root))
    monkeypatch.setenv("DISCORD_TOKEN", "dummy")
    return root


def _write_yaml(
    root: Path,
    *,
    default: str = "router",
    script: str = "${config_root}/llm_resolver.py",
    fallback: str = "high",
    more: str = "",
    with_router: bool = True,
) -> None:
    """テスト用の lilla.yaml を書く。"""
    extra = _ROUTER_YAML.format(script=script, fallback=fallback, more=more) if with_router else ""
    (root / "lilla.yaml").write_text(
        _BASE_YAML.format(default=default, extra=extra), encoding="utf-8"
    )


def _write_script(root: Path, body: str, name: str = "llm_resolver.py") -> Path:
    """resolver スクリプトを書く。"""
    path = root / name
    path.write_text(body, encoding="utf-8")
    return path


class TestProviderConfigValidation:
    """`LlmProviderConfig` / `LlmConfig` / `AppConfig` の起動時検証。"""

    def test_no_resolver_keeps_current_behavior(self, modules, config_root):
        """resolver が無い設定は従来どおり読める。"""
        config_mod, _ = modules
        _write_yaml(config_root, default="low", with_router=False)
        cfg = config_mod.AppConfig()
        assert cfg.get_llm_provider().model == "flash"

    def test_resolver_entry_is_accepted(self, modules, config_root):
        """`script` / `fallback` を持つ resolver は `url` / `model` なしで通る。"""
        config_mod, _ = modules
        _write_yaml(config_root)
        _write_script(config_root, "def resolve(ctx):\n    return None\n")
        cfg = config_mod.AppConfig()
        assert cfg.llm.providers["router"].type == "resolver"
        assert cfg.llm.providers["router"].url is None

    @pytest.mark.parametrize("field", ["url", "model"])
    def test_concrete_provider_requires_url_and_model(self, modules, field):
        """具体プロバイダーは `url` / `model` が必須。"""
        config_mod, _ = modules
        data = {"type": "openai_compat", "url": "u", "model": "m"}
        del data[field]
        with pytest.raises(ValidationError, match=field):
            config_mod.LlmProviderConfig(**data)

    def test_concrete_provider_rejects_script(self, modules):
        """具体プロバイダーに `script` を書くと落ちる。"""
        config_mod, _ = modules
        with pytest.raises(ValidationError, match="script"):
            config_mod.LlmProviderConfig(type="ollama", url="u", model="m", script="x.py")

    @pytest.mark.parametrize("field", ["script", "fallback"])
    def test_resolver_requires_script_and_fallback(self, modules, field):
        """resolver は `script` / `fallback` が必須。"""
        config_mod, _ = modules
        data = {"type": "resolver", "script": "x.py", "fallback": "high"}
        del data[field]
        with pytest.raises(ValidationError, match=field):
            config_mod.LlmProviderConfig(**data)

    def test_resolver_rejects_url(self, modules):
        """resolver は HTTP を出さないので `url` を持てない。"""
        config_mod, _ = modules
        with pytest.raises(ValidationError, match="url"):
            config_mod.LlmProviderConfig(type="resolver", script="x.py", fallback="high", url="u")

    def test_fallback_must_exist(self, modules, config_root):
        """`fallback` が台帳に無ければ落ちる。"""
        config_mod, _ = modules
        _write_yaml(config_root, fallback="missing")
        _write_script(config_root, "def resolve(ctx):\n    return None\n")
        with pytest.raises(ValidationError, match="fallback 'missing'"):
            config_mod.AppConfig()

    def test_fallback_must_not_be_resolver(self, modules, config_root):
        """`fallback` が resolver 型なら落ちる。"""
        config_mod, _ = modules
        more = (
            "    router2:\n"
            "      type: resolver\n"
            "      script: ${config_root}/llm_resolver.py\n"
            "      fallback: router\n"
        )
        _write_yaml(config_root, more=more)
        _write_script(config_root, "def resolve(ctx):\n    return None\n")
        with pytest.raises(ValidationError, match="must be a concrete provider"):
            config_mod.AppConfig()

    def test_script_outside_config_root_fails(self, modules, config_root):
        """`script` が `CONFIG_ROOT` の外を指すと落ちる。"""
        config_mod, _ = modules
        outside = config_root.parent / "outside.py"
        outside.write_text("def resolve(ctx):\n    return None\n", encoding="utf-8")
        _write_yaml(config_root, script=str(outside))
        with pytest.raises(ValidationError, match="under CONFIG_ROOT"):
            config_mod.AppConfig()

    def test_script_dotdot_escape_fails(self, modules, config_root):
        """`..` で `CONFIG_ROOT` の外へ出るパスも落ちる。"""
        config_mod, _ = modules
        (config_root.parent / "outside.py").write_text("def resolve(ctx):\n    return None\n")
        _write_yaml(config_root, script="${config_root}/../outside.py")
        with pytest.raises(ValidationError, match="under CONFIG_ROOT"):
            config_mod.AppConfig()

    def test_missing_script_fails(self, modules, config_root):
        """`script` のファイルが無ければ落ちる。"""
        config_mod, _ = modules
        _write_yaml(config_root)
        with pytest.raises(ValidationError, match="does not exist"):
            config_mod.AppConfig()

    def test_dir_prefix_is_rejected(self, modules, config_root):
        """単一ファイルなので `dir:` 指定は落ちる。"""
        config_mod, _ = modules
        _write_yaml(config_root, script="dir:${config_root}")
        with pytest.raises(ValidationError, match="single file"):
            config_mod.AppConfig()

    def test_file_prefix_and_relative_path(self, modules, config_root):
        """`file:` 接頭と `CONFIG_ROOT` 基準の相対パスを受け付ける。"""
        config_mod, _ = modules
        (config_root / "sub").mkdir()
        path = _write_script(config_root, "def resolve(ctx):\n    return None\n", "sub/r.py")
        assert config_mod.resolve_llm_resolver_script_path("file:sub/r.py", config_root) == path.resolve()
        assert (
            config_mod.resolve_llm_resolver_script_path("${config_root}/sub/r.py", config_root)
            == path.resolve()
        )


class TestValidateLlmResolvers:
    """起動時の `validate_llm_resolvers()`。"""

    def test_passes_when_resolve_exists(self, modules, config_root):
        """`resolve` があれば通る。"""
        config_mod, resolver_mod = modules
        _write_yaml(config_root)
        _write_script(config_root, "def resolve(ctx):\n    return None\n")
        resolver_mod.validate_llm_resolvers(config_mod.AppConfig())

    def test_fails_when_resolve_missing(self, modules, config_root):
        """`resolve` が無ければ落ちる。"""
        config_mod, resolver_mod = modules
        _write_yaml(config_root)
        _write_script(config_root, "def choose(ctx):\n    return None\n")
        with pytest.raises(ValueError, match="resolve"):
            resolver_mod.validate_llm_resolvers(config_mod.AppConfig())

    def test_fails_when_script_raises_on_import(self, modules, config_root):
        """import 時に落ちるスクリプトも起動時に落とす。"""
        config_mod, resolver_mod = modules
        _write_yaml(config_root)
        _write_script(config_root, "raise RuntimeError('boom')\n")
        with pytest.raises(ValueError, match="Failed to load"):
            resolver_mod.validate_llm_resolvers(config_mod.AppConfig())

    def test_noop_without_resolver(self, modules, config_root):
        """resolver が無ければ何もしない。"""
        config_mod, resolver_mod = modules
        _write_yaml(config_root, default="low", with_router=False)
        resolver_mod.validate_llm_resolvers(config_mod.AppConfig())


class TestResolveLlmName:
    """`resolve_llm_name()` の展開と落とし先。"""

    def _cfg(self, modules, config_root, body: str, **yaml_kwargs):
        """スクリプトと設定を書いて `AppConfig` を返す。"""
        config_mod, _ = modules
        _write_yaml(config_root, **yaml_kwargs)
        _write_script(config_root, body)
        return config_mod.AppConfig()

    async def test_concrete_name_does_not_call_script(self, modules, config_root):
        """具体プロバイダー名ならスクリプトを呼ばずにそのまま返す。"""
        _, resolver_mod = modules
        cfg = self._cfg(modules, config_root, "def resolve(ctx):\n    raise AssertionError('called')\n")
        assert await resolver_mod.resolve_llm_name("low", client_type="discord", config=cfg) == "low"

    async def test_no_resolver_uses_default(self, modules, config_root):
        """resolver が無い設定では `llm.default` がそのまま使われる。"""
        config_mod, resolver_mod = modules
        _write_yaml(config_root, default="local", with_router=False)
        cfg = config_mod.AppConfig()
        assert await resolver_mod.resolve_llm_name(None, client_type="discord", config=cfg) == "local"

    async def test_default_resolver_returns_script_choice(self, modules, config_root):
        """`llm.default` が resolver なら `resolve` の返した具体名を使う。"""
        _, resolver_mod = modules
        cfg = self._cfg(modules, config_root, "def resolve(ctx):\n    return 'low'\n")
        assert await resolver_mod.resolve_llm_name(None, client_type="discord", config=cfg) == "low"

    async def test_explicit_resolver_name_calls_script(self, modules, config_root):
        """`!model router` / task の `llm_name: router` でもスクリプトが呼ばれる。"""
        _, resolver_mod = modules
        cfg = self._cfg(
            modules, config_root, "def resolve(ctx):\n    return 'local'\n", default="low"
        )
        assert await resolver_mod.resolve_llm_name("router", client_type="task", config=cfg) == "local"

    async def test_async_resolve_is_awaited(self, modules, config_root):
        """`async def resolve` も使える。"""
        _, resolver_mod = modules
        cfg = self._cfg(modules, config_root, "async def resolve(ctx):\n    return 'low'\n")
        assert await resolver_mod.resolve_llm_name(None, client_type="discord", config=cfg) == "low"

    @pytest.mark.parametrize(
        "body",
        [
            "def resolve(ctx):\n    return None\n",
            "def resolve(ctx):\n    return 'nope'\n",
            "def resolve(ctx):\n    return 'router'\n",
            "def resolve(ctx):\n    return 42\n",
            "def resolve(ctx):\n    raise RuntimeError('boom')\n",
        ],
        ids=["none", "unknown", "resolver", "non-string", "exception"],
    )
    async def test_falls_back(self, modules, config_root, body):
        """`None`・未知名・resolver 名・非文字列・例外は `fallback` に落ちる。"""
        _, resolver_mod = modules
        cfg = self._cfg(modules, config_root, body)
        assert await resolver_mod.resolve_llm_name(None, client_type="discord", config=cfg) == "high"

    async def test_other_resolver_name_logs_warning(self, modules, config_root, caplog):
        """別の resolver を返したときは警告ログを出す（深さ 1）。"""
        _, resolver_mod = modules
        cfg = self._cfg(modules, config_root, "def resolve(ctx):\n    return 'router'\n")
        with caplog.at_level(logging.WARNING, logger="lilla_core.services.llm_resolver"):
            await resolver_mod.resolve_llm_name(None, client_type="discord", config=cfg)
        assert "another resolver" in caplog.text

    async def test_timeout_falls_back(self, modules, config_root):
        """`timeout_seconds` を超えた `async def resolve` は `fallback` に落ちる。"""
        _, resolver_mod = modules
        cfg = self._cfg(
            modules,
            config_root,
            "import asyncio\nasync def resolve(ctx):\n    await asyncio.sleep(5)\n    return 'low'\n",
            more="      timeout_seconds: 0.05\n",
        )
        assert await resolver_mod.resolve_llm_name(None, client_type="discord", config=cfg) == "high"

    async def test_context_fields(self, modules, config_root):
        """文脈に `client_type` / チャンネル / 発話 / 画像フラグ / 台帳の名前などが載る。"""
        _, resolver_mod = modules
        cfg = self._cfg(
            modules,
            config_root,
            "captured = []\ndef resolve(ctx):\n    captured.append(ctx)\n    return None\n",
        )
        await resolver_mod.resolve_llm_name(
            None,
            client_type="discord",
            discord_channel_id=555,
            user_content=[
                {"type": "text", "text": "[Apr 28 11:22] hello"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
            ],
            has_image=True,
            config=cfg,
        )
        func = next(iter(resolver_mod._resolve_functions.values()))
        ctx = func.__globals__["captured"][0]
        assert ctx.client_type == "discord"
        assert ctx.discord_channel_id == 555
        assert ctx.channel_name == "study"
        assert ctx.user_text == "hello"
        assert ctx.has_image is True
        assert ctx.resolver_name == "router"
        assert ctx.fallback == "high"
        assert set(ctx.provider_names) == {"low", "high", "local", "router"}
        # 画像本体は文脈に載らない
        assert "base64" not in repr(ctx)

    async def test_unregistered_channel_has_no_name(self, modules, config_root):
        """未登録チャンネルでは `channel_name` は `None`。"""
        _, resolver_mod = modules
        cfg = self._cfg(
            modules,
            config_root,
            "captured = []\ndef resolve(ctx):\n    captured.append(ctx)\n    return None\n",
        )
        await resolver_mod.resolve_llm_name(
            None, client_type="discord", discord_channel_id=999, user_content="hi", config=cfg
        )
        func = next(iter(resolver_mod._resolve_functions.values()))
        ctx = func.__globals__["captured"][0]
        assert ctx.channel_name is None
        assert ctx.has_image is False


class TestHelpers:
    """発話の要約と画像判定。"""

    def test_summarize_truncates(self, modules):
        """長い発話は切り詰める。"""
        _, resolver_mod = modules
        text = resolver_mod.summarize_user_text("a" * 1000, max_chars=10)
        assert text == "a" * 10 + "…"

    def test_summarize_non_text(self, modules):
        """テキストでない content は空文字。"""
        _, resolver_mod = modules
        assert resolver_mod.summarize_user_text(None) == ""

    def test_content_has_image(self, modules):
        """`image_url` パートがあるときだけ True。"""
        _, resolver_mod = modules
        assert resolver_mod.content_has_image([{"type": "image_url", "image_url": {}}]) is True
        assert resolver_mod.content_has_image([{"type": "text", "text": "x"}]) is False
        assert resolver_mod.content_has_image("text") is False
        assert resolver_mod.content_has_image(None) is False


class TestTemplate:
    """同梱テンプレ（`lilla_core/templates/llm_resolver.py`）。"""

    async def test_template_works_when_copied(self, modules, config_root):
        """テンプレをコピーして `script` に指せば動き、既定では `fallback` に任せる。"""
        config_mod, resolver_mod = modules
        template = Path(importlib.util.find_spec("lilla_core").origin).parent / "templates" / "llm_resolver.py"
        shutil.copy(template, config_root / "llm_resolver.py")
        _write_yaml(config_root)
        cfg = config_mod.AppConfig()
        resolver_mod.validate_llm_resolvers(cfg)
        assert await resolver_mod.resolve_llm_name(None, client_type="discord", config=cfg) == "high"

    async def test_template_channel_and_vision_examples(self, modules, config_root):
        """テンプレの表を埋めるとチャンネル固定・画像ありの分岐が効く。"""
        config_mod, resolver_mod = modules
        template = Path(importlib.util.find_spec("lilla_core").origin).parent / "templates" / "llm_resolver.py"
        body = template.read_text(encoding="utf-8")
        body = body.replace(
            "CHANNEL_PROVIDERS: dict[str, str] = {}", "CHANNEL_PROVIDERS: dict[str, str] = {'study': 'low'}"
        ).replace("VISION_PROVIDER: str | None = None", "VISION_PROVIDER: str | None = 'local'")
        _write_script(config_root, body)
        _write_yaml(config_root)
        cfg = config_mod.AppConfig()
        assert await resolver_mod.resolve_llm_name(
            None, client_type="discord", discord_channel_id=555, config=cfg
        ) == "low"
        assert await resolver_mod.resolve_llm_name(
            None, client_type="discord", discord_channel_id=555, has_image=True, config=cfg
        ) == "local"
