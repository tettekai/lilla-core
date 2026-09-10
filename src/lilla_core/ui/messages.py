"""Discord 向け UI 文言のカタログ参照。

`lilla.yaml` の `ui.locale`（既定 `ja`）で言語を切り替える。OS の `LANG` や
Discord 側の言語設定は一切見ない（同じ Bot が常に同じ言語で話すため）。

カタログはパッケージ同梱の `lilla_core/locales/{locale}.yaml` に置く
（`CONFIG_ROOT` 側には置かない）。キーは `selftest.summary` のような安定した
英語のドット区切りで、YAML 上は同じ形のネストで表現する::

    selftest:
      summary: "🩺 自己診断結果（{mode}）: {ok}/{total} 成功"

解決順は「指定ロケール → `ja` → キー名そのもの」で、いずれの段階へ落ちても
例外は投げない（文言の欠落で Bot の応答自体が失われないようにするため）。
フォールバックが起きたことは英語の WARNING ログで知らせる。

UI 文言を足すときは、同じ変更で `ja.yaml` と `en.yaml` の両方に入れること。
"""
from __future__ import annotations

import logging
import re
from functools import lru_cache
from importlib import resources
from typing import Any

import yaml

logger = logging.getLogger(__name__)

#: 指定ロケールで解決できなかったときに最後に使う既定ロケール。
DEFAULT_LOCALE = "ja"

#: カタログ YAML を置くパッケージ内ディレクトリ名。
LOCALES_DIRNAME = "locales"

#: ロケール名として受け付ける形式（設定値をファイル名に使うため厳しく絞る）。
_LOCALE_RE = re.compile(r"^[A-Za-z0-9_-]+$")

#: 同じ警告を出し続けないための記録（`(locale, key)` 単位で 1 回だけ出す）。
_warned: set[tuple[str, str]] = set()


def _locales_dir():
    """同梱カタログのディレクトリを Traversable として返す。

    Returns:
        `lilla_core/locales` を指す Traversable。
    """
    return resources.files("lilla_core").joinpath(LOCALES_DIRNAME)


def available_locales() -> list[str]:
    """同梱カタログとして存在するロケール名を昇順で返す。

    Returns:
        `lilla_core/locales/*.yaml` のファイル名（拡張子なし）のリスト。
    """
    try:
        entries = _locales_dir().iterdir()
    except (ModuleNotFoundError, FileNotFoundError, NotADirectoryError) as e:
        logger.warning("Failed to list bundled message catalogs: %s", e)
        return []
    return sorted(
        entry.name[: -len(".yaml")]
        for entry in entries
        if entry.name.endswith(".yaml")
    )


def current_locale() -> str:
    """設定（`ui.locale`）から現在のロケール名を返す。

    設定を読めない場合や、`ui.locale` が文字列でない場合（起動前・テストの
    モック設定など）は既定ロケールを返す。

    Returns:
        ロケール名。
    """
    from lilla_core.core.config import get_config

    try:
        locale = get_config().ui.locale
    except Exception as e:  # 設定不備で文言が出せなくなるのを避ける
        logger.warning(
            "Falling back to the default locale because the configuration is unavailable: %s", e
        )
        return DEFAULT_LOCALE
    if not isinstance(locale, str) or not locale:
        logger.debug("Falling back to the default locale because ui.locale is not usable: %r", locale)
        return DEFAULT_LOCALE
    return locale


@lru_cache(maxsize=None)
def _load_catalog(locale: str) -> dict[str, Any]:
    """指定ロケールのカタログを読み込む（プロセス内でキャッシュする）。

    Args:
        locale: ロケール名。

    Returns:
        カタログの辞書。存在しない・壊れている場合は空の辞書。
    """
    if not _LOCALE_RE.match(locale):
        logger.warning("Ignoring invalid locale name: %r", locale)
        return {}

    resource = _locales_dir().joinpath(f"{locale}.yaml")
    if not resource.is_file():
        return {}
    try:
        data = yaml.safe_load(resource.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error("Failed to load the message catalog (locale=%s): %s", locale, e, exc_info=True)
        return {}
    if not isinstance(data, dict):
        logger.error("Message catalog is not a mapping (locale=%s)", locale)
        return {}
    return data


def _lookup(catalog: dict[str, Any], key: str) -> str | None:
    """ドット区切りのキーでカタログを辿り、文字列を取り出す。

    Args:
        catalog: カタログの辞書。
        key: `selftest.summary` のようなドット区切りのキー。

    Returns:
        見つかった文言。見つからない・文字列でない場合は None。
    """
    node: Any = catalog
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node if isinstance(node, str) else None


def _warn_once(locale: str, key: str, message: str) -> None:
    """同じロケール・キーの組み合わせにつき 1 回だけ WARNING を出す。

    Args:
        locale: 解決に使ったロケール名。
        key: 対象のキー。
        message: ログに出す英語のメッセージ。
    """
    if (locale, key) in _warned:
        return
    _warned.add((locale, key))
    logger.warning("%s (locale=%s, key=%s)", message, locale, key)


def clear_cache() -> None:
    """カタログのキャッシュと警告の記録を捨てる（主にテスト用）。"""
    _load_catalog.cache_clear()
    _warned.clear()


def t(key: str, **params: Any) -> str:
    """カタログから UI 文言を取り出し、`{名前}` のプレースホルダを埋めて返す。

    Args:
        key: `selftest.summary` のようなドット区切りのキー。
        **params: 文言中の `{ok}` などに埋める値。

    Returns:
        解決した文言。指定ロケールに無ければ `ja`、それにも無ければキー名そのもの。
    """
    locale = current_locale()

    template = _lookup(_load_catalog(locale), key)
    if template is None and locale != DEFAULT_LOCALE:
        template = _lookup(_load_catalog(DEFAULT_LOCALE), key)
        if template is not None:
            _warn_once(locale, key, "Message is missing; falling back to the default locale")

    if template is None:
        _warn_once(locale, key, "Message is missing from every catalog; using the key itself")
        return key

    try:
        return template.format(**params)
    except (KeyError, IndexError) as e:
        _warn_once(locale, key, f"Message has an unfilled placeholder ({e}); using the raw template")
        return template


def translations(key: str) -> list[str]:
    """同梱カタログ全体から、そのキーの文言を重複なく集めて返す。

    ロケールを切り替えた前後で同じ文字列を突き合わせる必要がある箇所
    （承認依頼メッセージの区切り行など）で使う。現在のロケールの文言を先頭に置く。

    Args:
        key: `approval.command_marker` のようなドット区切りのキー。

    Returns:
        文言のリスト。どのカタログにも無ければ空のリスト。
    """
    ordered = [current_locale(), DEFAULT_LOCALE, *available_locales()]
    found: list[str] = []
    for locale in ordered:
        value = _lookup(_load_catalog(locale), key)
        if value is not None and value not in found:
            found.append(value)
    return found
