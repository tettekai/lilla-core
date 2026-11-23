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
tools:
  sets:
    main: []
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

    @pytest.mark.parametrize(
        ("data", "expected"),
        [
            ({"type": "openai_compat", "url": "u", "model": "m"}, 300.0),
            ({"type": "ollama", "url": "u", "model": "m"}, 300.0),
            ({"type": "resolver", "script": "x.py", "fallback": "high"}, 10.0),
            ({"type": "openai_compat", "url": "u", "model": "m", "timeout_seconds": 600}, 600.0),
            ({"type": "resolver", "script": "x.py", "fallback": "high", "timeout_seconds": 3}, 3.0),
        ],
    )
    def test_timeout_seconds_default_depends_on_type(self, modules, data, expected):
        """`timeout_seconds` は未指定なら具体プロバイダー 300 秒・resolver 10 秒になる。"""
        config_mod, _ = modules
        assert config_mod.LlmProviderConfig(**data).timeout_seconds == expected

    @pytest.mark.parametrize("provider_type", ["openai_compat", "ollama", "resolver"])
    @pytest.mark.parametrize("value", [0, -1])
    def test_timeout_seconds_must_be_positive(self, modules, provider_type, value):
        """`timeout_seconds` に 0 以下を書くと、どの型でも起動時に落ちる。"""
        config_mod, _ = modules
        data = (
            {"type": "resolver", "script": "x.py", "fallback": "high"}
            if provider_type == "resolver"
            else {"type": provider_type, "url": "u", "model": "m"}
        )
        with pytest.raises(ValidationError, match="timeout_seconds must be positive"):
            config_mod.LlmProviderConfig(**data, timeout_seconds=value)

    def test_resolver_rejects_url(self, modules):
        """resolver は HTTP を出さないので `url` を持てない。"""
        config_mod, _ = modules
        with pytest.raises(ValidationError, match="url"):
            config_mod.LlmProviderConfig(type="resolver", script="x.py", fallback="high", url="u")

    @pytest.mark.parametrize(
        "data",
        [
            {"type": "ollama", "url": "u", "model": "m"},
            {"type": "resolver", "script": "x.py", "fallback": "high"},
        ],
    )
    def test_removed_wol_fields_are_ignored(self, modules, data):
        """廃止した `wakeup_file` / `wol_sleep_seconds` が残っていても落とさず無視する。"""
        config_mod, _ = modules
        provider = config_mod.LlmProviderConfig(
            **data, wakeup_file="/tmp/wake", wol_sleep_seconds=30
        )
        assert not hasattr(provider, "wakeup_file")
        assert not hasattr(provider, "wol_sleep_seconds")

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


class TestFinalReplyProviderConfig:
    """具体プロバイダーの `final_reply_provider`（最終応答の書き直し先）の設定検証。"""

    @staticmethod
    def _concrete(**kwargs) -> dict:
        """具体プロバイダーのエントリを返す。"""
        return {"type": "openai_compat", "url": "https://example.invalid", "model": "m", **kwargs}

    def test_concrete_target_is_accepted(self, modules):
        """台帳にある具体プロバイダーを指せば通る。"""
        config_mod, _ = modules
        llm = config_mod.LlmConfig(
            default="local",
            providers={"local": self._concrete(final_reply_provider="main"), "main": self._concrete()},
        )
        assert llm.providers["local"].final_reply_provider == "main"
        assert llm.providers["main"].final_reply_provider is None

    def test_resolver_target_fails(self, modules):
        """resolver を指すと起動時に落ちる。"""
        config_mod, _ = modules
        with pytest.raises(ValidationError, match="final_reply_provider 'router' must be a concrete"):
            config_mod.LlmConfig(
                default="local",
                providers={
                    "local": self._concrete(final_reply_provider="router"),
                    "main": self._concrete(),
                    "router": {"type": "resolver", "script": "x.py", "fallback": "main"},
                },
            )

    def test_unknown_target_fails(self, modules):
        """台帳に無い名前を指すと起動時に落ちる。"""
        config_mod, _ = modules
        with pytest.raises(ValidationError, match="final_reply_provider 'missing' is not defined"):
            config_mod.LlmConfig(
                default="local",
                providers={"local": self._concrete(final_reply_provider="missing")},
            )

    def test_resolver_must_not_set(self, modules):
        """resolver 自身には書けない。"""
        config_mod, _ = modules
        with pytest.raises(ValidationError, match="must not set: final_reply_provider"):
            config_mod.LlmProviderConfig(
                type="resolver", script="x.py", fallback="main", final_reply_provider="main"
            )


class TestProviderPromptConfig:
    """具体プロバイダーの `prompt`（モデルごとの追記）の設定検証と spec 解決。"""

    @pytest.mark.parametrize(
        "spec", ["file:prompts/mimo.md", "dir:${config_root}/prompts", ["file:a.md", "dir:b"]]
    )
    def test_concrete_provider_accepts_source_spec(self, modules, spec):
        config_mod, _ = modules
        provider = config_mod.LlmProviderConfig(type="openai_compat", url="u", model="m", prompt=spec)
        assert provider.prompt == spec

    @pytest.mark.parametrize(
        "spec", ["prompts/mimo.md", "あなたは簡潔に答える。", "file:", "dir:  ", [], ["file:a.md", "b.md"]]
    )
    def test_concrete_provider_rejects_invalid_spec(self, modules, spec):
        """接頭辞の無いパス・本文の直書き・空の指定は起動時に落ちる。"""
        config_mod, _ = modules
        with pytest.raises(ValidationError, match="prompt"):
            config_mod.LlmProviderConfig(type="ollama", url="u", model="m", prompt=spec)

    def test_resolver_prompt_is_ignored_without_failing(self, modules, config_root):
        """resolver に `prompt` を書いても（形が不正でも）起動は落ちない。"""
        config_mod, _ = modules
        _write_yaml(config_root, more="      prompt: not a source spec\n")
        _write_script(config_root, "def resolve(ctx):\n    return None\n")
        cfg = config_mod.AppConfig()
        assert cfg.llm.providers["router"].type == "resolver"

    @pytest.mark.parametrize(
        "prompt_yaml",
        ["      prompt: 123\n", "      prompt:\n        a: b\n", "      prompt: [1, {x: y}]\n"],
    )
    def test_resolver_prompt_of_invalid_type_is_ignored(self, modules, config_root, prompt_yaml):
        """resolver に文字列・文字列の列以外の `prompt` を書いても起動は落ちず、値は捨てる。"""
        config_mod, _ = modules
        _write_yaml(config_root, more=prompt_yaml)
        _write_script(config_root, "def resolve(ctx):\n    return None\n")
        cfg = config_mod.AppConfig()
        assert cfg.llm.providers["router"].type == "resolver"
        assert cfg.llm.providers["router"].prompt is None

    def test_resolver_prompt_of_invalid_type_is_ignored_in_model(self, modules):
        """モデルを直接組み立てた場合も、resolver の不正な型の `prompt` は無視される。"""
        config_mod, _ = modules
        provider = config_mod.LlmProviderConfig(
            type="resolver", script="r.py", fallback="low", prompt={"a": 1}
        )
        assert provider.prompt is None

    def test_concrete_provider_rejects_invalid_prompt_type(self, modules):
        """具体プロバイダーでは文字列・文字列の列以外の `prompt` は従来どおり落ちる。"""
        config_mod, _ = modules
        with pytest.raises(ValidationError, match="prompt"):
            config_mod.LlmProviderConfig(type="ollama", url="u", model="m", prompt=123)

    def test_prompt_in_yaml_is_loaded(self, modules, config_root):
        config_mod, _ = modules
        (config_root / "lilla.yaml").write_text(
            _BASE_YAML.format(
                default="low",
                extra="    mimo:\n      type: openai_compat\n      url: u\n      model: m\n"
                "      prompt: file:prompts/mimo.md\n",
            ),
            encoding="utf-8",
        )
        cfg = config_mod.AppConfig()
        assert cfg.llm.providers["mimo"].prompt == "file:prompts/mimo.md"
        assert cfg.llm.providers["low"].prompt is None

    def test_resolve_spec_uses_config_root_for_relative_paths(self, modules, tmp_path):
        config_mod, _ = modules
        resolved = config_mod.resolve_llm_provider_prompt_spec(
            ["file:prompts/a.md", "dir:${config_root}/b", f"file:{tmp_path}/abs.md"], tmp_path
        )
        assert resolved == [
            f"file:{tmp_path / 'prompts' / 'a.md'}",
            f"dir:{tmp_path / 'b'}",
            f"file:{tmp_path / 'abs.md'}",
        ]


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

    async def test_tool_loop_fields_default(self, modules, config_root):
        """最初の呼び出しでは呼んだツール名は空、前回のプロバイダー名は `None`。"""
        _, resolver_mod = modules
        cfg = self._cfg(
            modules,
            config_root,
            "captured = []\ndef resolve(ctx):\n    captured.append(ctx)\n    return None\n",
        )
        await resolver_mod.resolve_llm_name(None, client_type="discord", config=cfg)
        func = next(iter(resolver_mod._resolve_functions.values()))
        ctx = func.__globals__["captured"][0]
        assert ctx.called_tool_names == ()
        assert ctx.previous_provider is None

    async def test_tool_loop_fields_passed(self, modules, config_root):
        """呼んだツール名と前回のプロバイダー名が文脈に載り、前回の名前を返せば続く。"""
        _, resolver_mod = modules
        cfg = self._cfg(
            modules,
            config_root,
            "captured = []\n"
            "def resolve(ctx):\n"
            "    captured.append(ctx)\n"
            "    return 'high' if 'write' in ctx.called_tool_names else ctx.previous_provider\n",
        )
        same = await resolver_mod.resolve_llm_name(
            None, client_type="discord", called_tool_names=["read", "read"],
            previous_provider="low", config=cfg,
        )
        switched = await resolver_mod.resolve_llm_name(
            None, client_type="discord", called_tool_names=("read", "write"),
            previous_provider="low", config=cfg,
        )
        assert same == "low"
        assert switched == "high"
        func = next(iter(resolver_mod._resolve_functions.values()))
        ctx = func.__globals__["captured"][0]
        assert ctx.called_tool_names == ("read", "read")
        assert ctx.previous_provider == "low"

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


    async def test_template_failover_example(self, modules, config_root):
        """`FAILOVER_PROVIDERS` を埋めると、失敗後と記憶中のプロバイダーで代わりへ回す（#393）。"""
        from lilla_core.services import llm_provider_cooldown

        config_mod, resolver_mod = modules
        template = Path(importlib.util.find_spec("lilla_core").origin).parent / "templates" / "llm_resolver.py"
        body = template.read_text(encoding="utf-8").replace(
            "FAILOVER_PROVIDERS: list[str] = []", "FAILOVER_PROVIDERS: list[str] = ['high', 'low']"
        )
        _write_script(config_root, body)
        _write_yaml(config_root)
        cfg = config_mod.AppConfig()
        # 失敗した直後は失敗したもの以外の最初へ
        assert await resolver_mod.reselect_llm_name(
            None, _llm_error("high"), failed_provider="high", reselection=1,
            client_type="discord", config=cfg,
        ) == "low"
        # 記憶中なら最初から代わりへ
        llm_provider_cooldown.record_failure(_llm_error("high"), 300.0)
        assert await resolver_mod.resolve_llm_name(None, client_type="discord", config=cfg) == "low"
        # 代わりも尽きたら選び直しはせず元のエラーで止まる
        llm_provider_cooldown.record_failure(_llm_error("low"), 300.0)
        assert await resolver_mod.reselect_llm_name(
            None, _llm_error("low"), failed_provider="low", reselection=1,
            client_type="discord", config=cfg,
        ) is None


class TestReselectionConfig:
    """`cooldown_seconds` / `max_reselections` の起動時検証（#393）。"""

    def test_defaults(self, modules):
        """具体プロバイダーは `cooldown_seconds` 300 秒、resolver は `max_reselections` 2 回。"""
        config_mod, _ = modules
        concrete = config_mod.LlmProviderConfig(type="openai_compat", url="u", model="m")
        resolver = config_mod.LlmProviderConfig(type="resolver", script="s.py", fallback="x")
        assert concrete.cooldown_seconds == 300.0
        assert concrete.max_reselections is None
        assert resolver.max_reselections == 2
        assert resolver.cooldown_seconds is None

    def test_values_are_kept(self, modules):
        """書いた値をそのまま使う（0 も可）。"""
        config_mod, _ = modules
        concrete = config_mod.LlmProviderConfig(
            type="ollama", url="u", model="m", cooldown_seconds=0
        )
        resolver = config_mod.LlmProviderConfig(
            type="resolver", script="s.py", fallback="x", max_reselections=0
        )
        assert concrete.cooldown_seconds == 0
        assert resolver.max_reselections == 0

    def test_resolver_rejects_cooldown_seconds(self, modules):
        """resolver に `cooldown_seconds` は書けない。"""
        config_mod, _ = modules
        with pytest.raises(ValidationError, match="cooldown_seconds"):
            config_mod.LlmProviderConfig(
                type="resolver", script="s.py", fallback="x", cooldown_seconds=60
            )

    def test_concrete_rejects_max_reselections(self, modules):
        """具体プロバイダーに `max_reselections` は書けない。"""
        config_mod, _ = modules
        with pytest.raises(ValidationError, match="max_reselections"):
            config_mod.LlmProviderConfig(
                type="openai_compat", url="u", model="m", max_reselections=1
            )

    @pytest.mark.parametrize(
        "data",
        [
            {"type": "openai_compat", "url": "u", "model": "m", "cooldown_seconds": -1},
            {"type": "resolver", "script": "s.py", "fallback": "x", "max_reselections": -1},
            {"type": "resolver", "script": "s.py", "fallback": "x", "max_reselections": 1.5},
            {"type": "resolver", "script": "s.py", "fallback": "x", "max_reselections": True},
        ],
        ids=["negative-cooldown", "negative-reselections", "float-reselections", "bool-reselections"],
    )
    def test_invalid_values_fail(self, modules, data):
        """負の値・整数以外は起動時に落ちる。"""
        config_mod, _ = modules
        with pytest.raises(ValidationError):
            config_mod.LlmProviderConfig(**data)


def _llm_error(provider: str = "low", status: int | None = 429, kind: str = "http", retry_after=None):
    """送信の失敗を表す `LLMError` を作る。"""
    from lilla_core.core.exceptions import LLMError

    return LLMError(
        "boom", provider_name=provider, error_kind=kind, status=status, retry_after=retry_after
    )


class TestReselectLlmName:
    """`reselect_llm_name()` による失敗後の呼び直し（#393）。"""

    def _cfg(self, modules, config_root, body: str, **yaml_kwargs):
        """スクリプトと設定を書いて `AppConfig` を返す。"""
        config_mod, _ = modules
        _write_yaml(config_root, **yaml_kwargs)
        _write_script(config_root, body)
        return config_mod.AppConfig()

    @staticmethod
    def _captured(resolver_mod):
        """スクリプトが `captured` に積んだ文脈を返す。"""
        func = next(iter(resolver_mod._resolve_functions.values()))
        return func.__globals__["captured"]

    async def _reselect(self, resolver_mod, cfg, error=None, **kwargs):
        """既定の引数で `reselect_llm_name` を呼ぶ。"""
        params = {"failed_provider": "low", "reselection": 1, "client_type": "discord"}
        params.update(kwargs)
        return await resolver_mod.reselect_llm_name(
            None, error or _llm_error(), config=cfg, **params
        )

    async def test_returns_other_provider(self, modules, config_root):
        """スクリプトが別の具体プロバイダー名を返せばそれを返す。"""
        _, resolver_mod = modules
        cfg = self._cfg(modules, config_root, "def resolve(ctx):\n    return 'local'\n")
        assert await self._reselect(resolver_mod, cfg) == "local"

    @pytest.mark.parametrize(
        "body",
        [
            "def resolve(ctx):\n    return None\n",
            "def resolve(ctx):\n    return 'low'\n",
            "def resolve(ctx):\n    return 'nope'\n",
            "def resolve(ctx):\n    return 'router'\n",
            "def resolve(ctx):\n    return 42\n",
            "def resolve(ctx):\n    raise RuntimeError('boom')\n",
        ],
        ids=["none", "same", "unknown", "resolver", "non-string", "exception"],
    )
    async def test_gives_up_without_fallback(self, modules, config_root, body):
        """`None`・同じ名前・不正な名前・例外では `fallback` へ送らず `None` を返す。"""
        _, resolver_mod = modules
        cfg = self._cfg(modules, config_root, body)
        assert await self._reselect(resolver_mod, cfg) is None

    async def test_timeout_gives_up(self, modules, config_root):
        """呼び直しのタイムアウトも `fallback` に落とさない。"""
        _, resolver_mod = modules
        cfg = self._cfg(
            modules,
            config_root,
            "import asyncio\nasync def resolve(ctx):\n    await asyncio.sleep(5)\n    return 'local'\n",
            more="      timeout_seconds: 0.05\n",
        )
        assert await self._reselect(resolver_mod, cfg) is None

    async def test_limit(self, modules, config_root):
        """`max_reselections` を超えたらスクリプトを呼ばずに `None`。"""
        _, resolver_mod = modules
        cfg = self._cfg(
            modules,
            config_root,
            "captured = []\ndef resolve(ctx):\n    captured.append(ctx)\n    return 'local'\n",
            more="      max_reselections: 1\n",
        )
        assert await self._reselect(resolver_mod, cfg, reselection=1) == "local"
        assert await self._reselect(resolver_mod, cfg, reselection=2) is None
        assert len(self._captured(resolver_mod)) == 1

    async def test_zero_disables(self, modules, config_root):
        """`max_reselections: 0` なら選び直さない。"""
        _, resolver_mod = modules
        cfg = self._cfg(
            modules,
            config_root,
            "def resolve(ctx):\n    raise AssertionError('called')\n",
            more="      max_reselections: 0\n",
        )
        assert await self._reselect(resolver_mod, cfg) is None

    async def test_concrete_key_is_not_reselected(self, modules, config_root):
        """具体プロバイダーを直接指定した会話では選び直さない。"""
        _, resolver_mod = modules
        cfg = self._cfg(modules, config_root, "def resolve(ctx):\n    raise AssertionError('called')\n")
        result = await resolver_mod.reselect_llm_name(
            "low", _llm_error(), failed_provider="low", reselection=1,
            client_type="discord", config=cfg,
        )
        assert result is None

    @pytest.mark.parametrize(
        "error",
        [
            pytest.param(lambda: _llm_error(status=400), id="400"),
            pytest.param(lambda: _llm_error(status=401), id="401"),
            pytest.param(lambda: _llm_error(status=None, kind="invalid_response"), id="parse"),
            pytest.param(
                lambda: __import__(
                    "lilla_core.core.exceptions", fromlist=["x"]
                ).LlmSendBlockedError("blocked"),
                id="send-guard",
            ),
        ],
    )
    async def test_non_reselectable_failure(self, modules, config_root, error):
        """対象外の失敗（送信前検査・400 などの 4xx・解釈の失敗）では呼び直さない。"""
        _, resolver_mod = modules
        cfg = self._cfg(modules, config_root, "def resolve(ctx):\n    raise AssertionError('called')\n")
        assert await self._reselect(resolver_mod, cfg, error=error()) is None

    @pytest.mark.parametrize(
        ("status", "kind"),
        [(404, "http"), (408, "http"), (429, "http"), (500, "http"), (503, "http"),
         (None, "connection"), (None, "timeout")],
    )
    async def test_reselectable_failures(self, modules, config_root, status, kind):
        """404 / 408 / 429 / 5xx・接続エラー・タイムアウトでは呼び直す。"""
        _, resolver_mod = modules
        cfg = self._cfg(modules, config_root, "def resolve(ctx):\n    return 'local'\n")
        error = _llm_error(status=status, kind=kind)
        assert await self._reselect(resolver_mod, cfg, error=error) == "local"

    async def test_context_carries_failure(self, modules, config_root):
        """文脈に失敗の情報・回数・失敗したプロバイダー（前回の選択）・記憶中の一覧が載る。"""
        from lilla_core.services import llm_provider_cooldown

        _, resolver_mod = modules
        cfg = self._cfg(
            modules,
            config_root,
            "captured = []\n"
            "def resolve(ctx):\n"
            "    captured.append(ctx)\n"
            "    avoid = {u.provider for u in ctx.unavailable_providers}\n"
            "    return next(n for n in ('low', 'high', 'local') if n not in avoid)\n",
        )
        error = _llm_error(provider="low", status=429, retry_after=12.0)
        llm_provider_cooldown.record_failure(error, 300)

        chosen = await self._reselect(
            resolver_mod, cfg, error=error, reselection=2,
            called_tool_names=("read",), discord_channel_id=555, user_content="hi",
        )

        assert chosen == "high"
        ctx = self._captured(resolver_mod)[0]
        assert ctx.failure.provider == "low"
        assert ctx.failure.status == 429
        assert ctx.failure.error_kind == "http"
        assert ctx.failure.retry_after == 12.0
        assert ctx.reselection == 2
        assert ctx.previous_provider == "low"
        assert ctx.called_tool_names == ("read",)
        assert ctx.channel_name == "study"
        assert ctx.user_text == "hi"
        assert [u.provider for u in ctx.unavailable_providers] == ["low"]
        assert ctx.unavailable_providers[0].status == 429


class TestUnavailableProvidersInResolve:
    """通常の展開でも記憶中のプロバイダーが文脈に載ること（#393）。"""

    async def test_normal_resolve_sees_unavailable(self, modules, config_root):
        """記憶中のものを避けるかはスクリプトが決め、記憶が無ければ空・失敗情報は `None`。"""
        from lilla_core.services import llm_provider_cooldown

        config_mod, resolver_mod = modules
        _write_yaml(config_root)
        _write_script(
            config_root,
            "captured = []\n"
            "def resolve(ctx):\n"
            "    captured.append(ctx)\n"
            "    avoid = {u.provider for u in ctx.unavailable_providers}\n"
            "    return 'local' if 'low' in avoid else 'low'\n",
        )
        cfg = config_mod.AppConfig()

        assert await resolver_mod.resolve_llm_name(None, client_type="discord", config=cfg) == "low"
        llm_provider_cooldown.record_failure(_llm_error(provider="low", status=503), 300)
        assert await resolver_mod.resolve_llm_name(None, client_type="discord", config=cfg) == "local"

        func = next(iter(resolver_mod._resolve_functions.values()))
        first, second = func.__globals__["captured"]
        assert first.unavailable_providers == ()
        assert first.failure is None
        assert first.reselection == 0
        assert [u.provider for u in second.unavailable_providers] == ["low"]
