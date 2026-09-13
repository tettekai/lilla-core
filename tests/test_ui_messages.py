"""`lilla_core.ui.messages` の文言解決テスト。

`ui.locale` で指定したロケール → `ja` → キー名そのもの、という解決順と、
同梱カタログ（`ja.yaml` / `en.yaml`）が同じキー・同じプレースホルダを持つこと
（片方だけに文言を足していないこと）を検証する。
"""

import logging
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from lilla_core.core.extension import Extension
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


class SampleLocaleExtension(Extension):
    """テスト用に UI 文言カタログを同梱する拡張。"""

    name = "sample-locales"

    def __init__(self, *directories: Path) -> None:
        """カタログのディレクトリを受け取る。

        Args:
            directories: `{locale}.yaml` を置いたディレクトリ（ロード順）。
        """
        self._directories = list(directories)

    def locale_dirs(self) -> list[Path]:
        """同梱カタログのディレクトリを返す。"""
        return list(self._directories)


@pytest.fixture
def register_locale_extension():
    """カタログ付きの拡張を登録し、後片付けで登録とキャッシュを元へ戻す。"""
    from lilla_core.core.extension import reset_extensions, set_extensions

    def _register(*extensions: Extension) -> None:
        set_extensions(list(extensions))

    yield _register
    reset_extensions()
    messages.clear_cache()


def _write_catalog(directory: Path, locale: str, body: str) -> None:
    """テスト用のカタログ YAML を書き出す。

    Args:
        directory: 書き出し先ディレクトリ。
        locale: ロケール名（ファイル名になる）。
        body: YAML の中身。
    """
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{locale}.yaml").write_text(body, encoding="utf-8")


class TestExtensionCatalogs:
    """拡張が同梱したカタログの合成・名前空間・フォールバックの検証。"""

    def test_resolves_extension_key(
        self, tmp_path: Path, register_locale_extension, set_locale
    ) -> None:
        """拡張のカタログのキーを `t()` で解決できる。"""
        _write_catalog(tmp_path, "ja", "sample-locales:\n  notify:\n    title: 'お知らせ: {name}'\n")
        register_locale_extension(SampleLocaleExtension(tmp_path))
        set_locale("ja")
        assert messages.t("sample-locales.notify.title", name="散歩") == "お知らせ: 散歩"

    def test_keeps_core_messages(
        self, tmp_path: Path, register_locale_extension, set_locale
    ) -> None:
        """拡張のカタログを重ねてもコアの文言はそのまま解決できる。"""
        _write_catalog(tmp_path, "ja", "sample-locales:\n  hello: 'こんにちは'\n")
        register_locale_extension(SampleLocaleExtension(tmp_path))
        set_locale("ja")
        assert messages.t("command.enable_tools.done") == "通常会話のツールを再び有効にしました。"

    def test_falls_back_to_ja_when_locale_is_missing(
        self, tmp_path: Path, register_locale_extension, set_locale
    ) -> None:
        """拡張が `en.yaml` を持たない場合は `ja` へフォールバックする。"""
        _write_catalog(tmp_path, "ja", "sample-locales:\n  hello: 'こんにちは'\n")
        register_locale_extension(SampleLocaleExtension(tmp_path))
        set_locale("en")
        assert messages.t("sample-locales.hello") == "こんにちは"

    def test_uses_key_itself_when_missing_everywhere(
        self, tmp_path: Path, register_locale_extension, set_locale
    ) -> None:
        """どのカタログにも無いキーは例外を投げずキー名を返す。"""
        _write_catalog(tmp_path, "ja", "sample-locales:\n  hello: 'こんにちは'\n")
        register_locale_extension(SampleLocaleExtension(tmp_path))
        set_locale("en")
        assert messages.t("sample-locales.missing") == "sample-locales.missing"

    def test_prefers_requested_locale(
        self, tmp_path: Path, register_locale_extension, set_locale
    ) -> None:
        """`en.yaml` があれば `ui.locale` のカタログを優先する。"""
        _write_catalog(tmp_path, "ja", "sample-locales:\n  hello: 'こんにちは'\n")
        _write_catalog(tmp_path, "en", "sample-locales:\n  hello: 'hello'\n")
        register_locale_extension(SampleLocaleExtension(tmp_path))
        set_locale("en")
        assert messages.t("sample-locales.hello") == "hello"

    def test_merges_multiple_directories_of_one_extension(
        self, tmp_path: Path, register_locale_extension, set_locale
    ) -> None:
        """1 つの拡張が複数ディレクトリを返したら、後勝ちで消さず浅くマージする。"""
        first = tmp_path / "first"
        second = tmp_path / "second"
        _write_catalog(first, "ja", "sample-locales:\n  one: 'いち'\n  both: 'A'\n")
        _write_catalog(second, "ja", "sample-locales:\n  two: 'に'\n  both: 'B'\n")
        register_locale_extension(SampleLocaleExtension(first, second))
        set_locale("ja")

        assert messages.t("sample-locales.one") == "いち"
        assert messages.t("sample-locales.two") == "に"
        # 同じキーは後のディレクトリが勝つ
        assert messages.t("sample-locales.both") == "B"

    def test_ignores_a_non_mapping_extension_node(
        self,
        tmp_path: Path,
        register_locale_extension,
        set_locale,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """拡張名の下が辞書でないカタログは ERROR ログを出して読み飛ばす。"""
        _write_catalog(tmp_path, "ja", "sample-locales: 'ただの文字列'\n")
        with caplog.at_level(logging.ERROR, logger=messages.__name__):
            register_locale_extension(SampleLocaleExtension(tmp_path))
        set_locale("ja")

        assert messages.t("sample-locales.hello") == "sample-locales.hello"
        assert "not a mapping" in caplog.text

    def test_rejects_top_level_key_other_than_the_extension_name(
        self, tmp_path: Path, register_locale_extension
    ) -> None:
        """トップレベルキーが拡張名と異なるカタログは登録時に `ValueError` で落ちる。"""
        _write_catalog(tmp_path, "ja", "habits:\n  hello: 'こんにちは'\n")
        with pytest.raises(ValueError, match="top-level key"):
            register_locale_extension(SampleLocaleExtension(tmp_path))

    def test_rejects_extension_name_colliding_with_a_core_key(
        self, tmp_path: Path, register_locale_extension
    ) -> None:
        """拡張名がコアのトップレベルキーと同じ場合は登録時に `ValueError` で落ちる。"""

        class CollidingExtension(SampleLocaleExtension):
            """コアのカタログのトップレベルキーと同じ名前を持つ拡張。"""

            name = "selftest"

        _write_catalog(tmp_path, "ja", "selftest:\n  hello: 'こんにちは'\n")
        with pytest.raises(ValueError, match="collides"):
            register_locale_extension(CollidingExtension(tmp_path))

    def test_warns_once_per_locale_for_a_missing_directory(
        self,
        tmp_path: Path,
        register_locale_extension,
        set_locale,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """存在しないディレクトリは WARNING を出して読み飛ばす（ロケールごとに 1 回）。"""
        register_locale_extension(SampleLocaleExtension(tmp_path / "missing"))
        set_locale("ja")
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger=messages.__name__):
            assert messages.t("command.enable_tools.done") == (
                "通常会話のツールを再び有効にしました。"
            )
            messages.t("command.enable_tools.done")
        # 同じロケールを 2 回引いてもキャッシュにより警告は 1 回だけ
        assert caplog.text.count("does not exist") == 1

    def test_ignores_a_broken_catalog(
        self,
        tmp_path: Path,
        register_locale_extension,
        set_locale,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """YAML が壊れていても登録・起動は落とさず、そのロケール分だけ空として扱う。"""
        _write_catalog(tmp_path, "ja", "sample-locales:\n  - not: a mapping\n   bad indent\n")
        register_locale_extension(SampleLocaleExtension(tmp_path))
        set_locale("ja")
        with caplog.at_level(logging.ERROR, logger=messages.__name__):
            assert messages.t("sample-locales.hello") == "sample-locales.hello"
        assert "Failed to load the message catalog" in caplog.text

    def test_available_locales_include_extension_catalogs(
        self, tmp_path: Path, register_locale_extension
    ) -> None:
        """`available_locales()` はコアと拡張のロケールの和集合を返す。"""
        _write_catalog(tmp_path, "fr", "sample-locales:\n  hello: 'bonjour'\n")
        register_locale_extension(SampleLocaleExtension(tmp_path))
        assert {"ja", "en", "fr"} <= set(messages.available_locales())

    def test_translations_cover_extension_catalogs(
        self, tmp_path: Path, register_locale_extension, set_locale
    ) -> None:
        """`translations()` は拡張のカタログも横断して集める。"""
        _write_catalog(tmp_path, "ja", "sample-locales:\n  hello: 'こんにちは'\n")
        _write_catalog(tmp_path, "en", "sample-locales:\n  hello: 'hello'\n")
        register_locale_extension(SampleLocaleExtension(tmp_path))
        set_locale("en")
        assert messages.translations("sample-locales.hello") == ["hello", "こんにちは"]

    def test_registration_invalidates_the_cached_catalog(
        self, tmp_path: Path, register_locale_extension, set_locale
    ) -> None:
        """登録の前後でキャッシュが捨てられ、新しいキーがそのまま解決できる。"""
        _write_catalog(tmp_path, "ja", "sample-locales:\n  hello: 'こんにちは'\n")
        set_locale("ja")
        # 登録前にカタログを引いてキャッシュを作る
        assert messages.t("sample-locales.hello") == "sample-locales.hello"

        register_locale_extension(SampleLocaleExtension(tmp_path))

        # `set_locale()`（= clear_cache）を挟まずに解決できる
        assert messages.t("sample-locales.hello") == "こんにちは"

    def test_deregistration_invalidates_the_cached_catalog(
        self, tmp_path: Path, register_locale_extension, set_locale
    ) -> None:
        """登録を外すとキャッシュも捨てられ、拡張のキーは解決できなくなる。"""
        from lilla_core.core.extension import set_extensions

        _write_catalog(tmp_path, "ja", "sample-locales:\n  hello: 'こんにちは'\n")
        register_locale_extension(SampleLocaleExtension(tmp_path))
        set_locale("ja")
        assert messages.t("sample-locales.hello") == "こんにちは"

        set_extensions([])

        assert messages.t("sample-locales.hello") == "sample-locales.hello"
