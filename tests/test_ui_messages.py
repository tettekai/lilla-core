"""`lilla_core.ui.messages` の文言解決テスト。

`ui.locale` で指定したロケール → `ja` → キー名そのもの、という解決順と、
同梱カタログ（`ja.yaml` / `en.yaml`）が同じキー・同じプレースホルダを持つこと
（片方だけに文言を足していないこと）を検証する。
"""

import logging
import re
from types import SimpleNamespace

import pytest

from lilla_core.ui import messages

#: カタログの値に含まれる `{name}` 形式のプレースホルダ。
_PLACEHOLDER_RE = re.compile(r"{(\w+)}")


@pytest.fixture
def set_locale(monkeypatch: pytest.MonkeyPatch):
    """`ui.locale` を差し替える関数を返す（カタログのキャッシュも都度捨てる）。

    `messages.current_locale()` は呼び出しのたびに
    `lilla_core.core.config.get_config` を引くため、その属性を差し替える。
    """
    from lilla_core.core import config as config_module

    def _set(locale: object) -> None:
        fake_config = SimpleNamespace(ui=SimpleNamespace(locale=locale))
        monkeypatch.setattr(config_module, "get_config", lambda: fake_config)
        messages.clear_cache()

    yield _set
    messages.clear_cache()


def _flatten(catalog: dict, prefix: str = "") -> dict[str, str]:
    """ネストしたカタログを `{"a.b": "文言"}` の平坦な辞書にする。"""
    flat: dict[str, str] = {}
    for key, value in catalog.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(_flatten(value, f"{path}."))
        else:
            flat[path] = value
    return flat


class TestT:
    """`t()` の解決順とプレースホルダ埋めの検証。"""

    def test_returns_japanese_for_ja(self, set_locale) -> None:
        """`ja` では日本語の文言を返す。"""
        set_locale("ja")
        assert messages.t("command.enable_tools.done") == "通常会話のツールを再び有効にしました。"

    def test_returns_english_for_en(self, set_locale) -> None:
        """`en` では英語の文言を返す。"""
        set_locale("en")
        assert messages.t("command.enable_tools.done") == "Re-enabled the tools for normal conversation."

    def test_falls_back_to_ja_for_unknown_locale(self, set_locale) -> None:
        """カタログの無いロケールでも落ちず、`ja` の文言へフォールバックする。"""
        set_locale("fr")
        assert messages.t("command.enable_tools.done") == "通常会話のツールを再び有効にしました。"

    def test_falls_back_to_ja_for_invalid_locale_name(self, set_locale) -> None:
        """ファイル名に使えない文字を含むロケール名も `ja` へフォールバックする。"""
        set_locale("../etc/passwd")
        assert messages.t("command.enable_tools.done") == "通常会話のツールを再び有効にしました。"

    def test_falls_back_to_ja_when_locale_is_not_a_string(self, set_locale) -> None:
        """`ui.locale` が文字列でない設定でも既定ロケールで解決する。"""
        set_locale(None)
        assert messages.t("command.enable_tools.done") == "通常会話のツールを再び有効にしました。"

    def test_returns_key_itself_when_missing_everywhere(self, set_locale) -> None:
        """どのカタログにも無いキーはキー名をそのまま返す。"""
        set_locale("ja")
        assert messages.t("no.such.message") == "no.such.message"

    def test_returns_key_itself_for_partial_path(self, set_locale) -> None:
        """途中までしか一致しないキー（値が辞書）もキー名をそのまま返す。"""
        set_locale("ja")
        assert messages.t("command.model") == "command.model"

    def test_fills_placeholders(self, set_locale) -> None:
        """`{name}` のプレースホルダを引数で埋める。"""
        set_locale("ja")
        assert messages.t("command.model.switched", model="ollama-x") == (
            "モデルを ollama-x に切り替えました"
        )

    def test_returns_raw_template_when_placeholder_is_missing(self, set_locale) -> None:
        """引数が足りなくても例外を投げず、素のテンプレートを返す。"""
        set_locale("ja")
        assert messages.t("command.model.switched") == "モデルを {model} に切り替えました"

    def test_ignores_extra_params(self, set_locale) -> None:
        """余分な引数は無視する。"""
        set_locale("ja")
        assert messages.t("command.enable_tools.done", unused="x") == (
            "通常会話のツールを再び有効にしました。"
        )

    def test_logs_in_english_when_falling_back_to_ja(
        self, set_locale, caplog: pytest.LogCaptureFixture
    ) -> None:
        """指定ロケールに文言が無くフォールバックしたら英語で WARNING を出す。"""
        set_locale("en")
        # `en.yaml` には無いが `ja.yaml` にはあるキーを一時的に用意する
        messages._load_catalog("ja")["_test_only"] = "日本語だけの文言"
        with caplog.at_level(logging.WARNING, logger=messages.__name__):
            assert messages.t("_test_only") == "日本語だけの文言"
        assert "falling back to the default locale" in caplog.text.lower()
        assert "locale=en" in caplog.text

    def test_logs_in_english_when_message_is_missing(
        self, set_locale, caplog: pytest.LogCaptureFixture
    ) -> None:
        """どのカタログにも無いキーは英語で WARNING を出す。"""
        set_locale("ja")
        with caplog.at_level(logging.WARNING, logger=messages.__name__):
            messages.t("no.such.message")
        assert "missing" in caplog.text.lower()
        assert "key=no.such.message" in caplog.text

    def test_warns_only_once_per_key(
        self, set_locale, caplog: pytest.LogCaptureFixture
    ) -> None:
        """同じキーの警告は 1 回だけ出す（返信のたびにログを埋めない）。"""
        set_locale("ja")
        with caplog.at_level(logging.WARNING, logger=messages.__name__):
            messages.t("no.such.message")
            messages.t("no.such.message")
        assert caplog.text.count("key=no.such.message") == 1


class TestCurrentLocale:
    """`current_locale()` の検証。"""

    def test_returns_configured_locale(self, set_locale) -> None:
        """設定した `ui.locale` をそのまま返す。"""
        set_locale("en")
        assert messages.current_locale() == "en"

    def test_returns_default_when_config_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """設定を読めない場合は既定ロケールを返す。"""
        from lilla_core.core import config as config_module

        def _raise():
            raise RuntimeError("no config")

        monkeypatch.setattr(config_module, "get_config", _raise)
        assert messages.current_locale() == messages.DEFAULT_LOCALE


class TestAvailableLocales:
    """`available_locales()` の検証。"""

    def test_lists_bundled_catalogs(self) -> None:
        """同梱している ja / en を列挙する。"""
        assert {"ja", "en"} <= set(messages.available_locales())


class TestTranslations:
    """`translations()` の検証（ロケールをまたいだ文言の突き合わせ用）。"""

    def test_collects_all_locales_with_current_first(self, set_locale) -> None:
        """現在のロケールの文言を先頭に、全ロケール分を重複なく返す。"""
        set_locale("en")
        found = messages.translations("approval.command_marker")
        assert found[0] == messages.t("approval.command_marker")
        assert len(found) == len(set(found))
        assert "----- 実行内容（承認するとこの内容がそのまま実行されます） -----" in found

    def test_returns_empty_list_for_unknown_key(self, set_locale) -> None:
        """どのカタログにも無いキーでは空リストを返す。"""
        set_locale("ja")
        assert messages.translations("no.such.message") == []


class TestCatalogConsistency:
    """同梱カタログ同士の整合性（片方だけに文言を足していないこと）の検証。"""

    def test_all_locales_have_the_same_keys(self) -> None:
        """すべてのカタログが同じキー集合を持つ。"""
        messages.clear_cache()
        catalogs = {
            locale: _flatten(messages._load_catalog(locale))
            for locale in messages.available_locales()
        }
        base_locale = messages.DEFAULT_LOCALE
        base_keys = set(catalogs[base_locale])
        for locale, flat in catalogs.items():
            assert set(flat) == base_keys, f"key mismatch in {locale}.yaml"

    def test_all_locales_have_the_same_placeholders(self) -> None:
        """同じキーの文言は、どのカタログでも同じプレースホルダを持つ。"""
        messages.clear_cache()
        base = _flatten(messages._load_catalog(messages.DEFAULT_LOCALE))
        for locale in messages.available_locales():
            flat = _flatten(messages._load_catalog(locale))
            for key, text in flat.items():
                assert set(_PLACEHOLDER_RE.findall(text)) == set(
                    _PLACEHOLDER_RE.findall(base[key])
                ), f"placeholder mismatch: {locale}.yaml {key}"

    def test_all_values_are_strings(self) -> None:
        """カタログの葉はすべて文字列（辞書やリストを置かない）。"""
        messages.clear_cache()
        for locale in messages.available_locales():
            for key, value in _flatten(messages._load_catalog(locale)).items():
                assert isinstance(value, str), f"{locale}.yaml {key} is not a string"
