"""Discord 向け UI 文言のカタログ参照。

`lilla.yaml` の `ui.locale`（既定 `ja`）で言語を切り替える。OS の `LANG` や
Discord 側の言語設定は一切見ない（同じ Bot が常に同じ言語で話すため）。

カタログはパッケージ同梱の `lilla_core/locales/{locale}.yaml` に置く
（`CONFIG_ROOT` 側には置かない）。キーは `selftest.summary` のような安定した
英語のドット区切りで、YAML 上は同じ形のネストで表現する::

    selftest:
      summary: "🩺 自己診断結果（{mode}）: {ok}/{total} 成功"

拡張も `Extension.locale_dirs()` で同じ命名（`{locale}.yaml`）のカタログを
同梱でき、コアのカタログへロード順に重ねた 1 つの辞書として解決される。
拡張のカタログはトップレベルのキーがその拡張の `name` と一致していなければ
ならず（例: `name = "lilla-habits"` なら `t("lilla-habits.notify.title")`）、
違反していればカタログの読み込み時に `ValueError` で落とす。

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
from pathlib import Path
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


def _extension_locale_dirs() -> list[tuple[str, Path]]:
    """登録済み拡張のカタログディレクトリを `(拡張名, ディレクトリ)` でロード順に返す。

    `core/extension.py` は `set_extensions()` の中でこのモジュールの
    `clear_cache()` を呼ぶため、import は関数内に置いて循環 import を避ける。

    Returns:
        拡張名とカタログディレクトリの組のリスト。
    """
    from lilla_core.core.extension import get_locale_dirs

    return get_locale_dirs()


def _core_locales() -> list[str]:
    """コア同梱カタログとして存在するロケール名を昇順で返す。

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


def available_locales() -> list[str]:
    """コアと全拡張のカタログとして存在するロケール名を昇順で返す。

    Returns:
        コア同梱分と拡張の `locale_dirs()` 配下の `*.yaml` のファイル名
        （拡張子なし）を重複なく集めたリスト。
    """
    locales = set(_core_locales())
    for name, directory in _extension_locale_dirs():
        if not directory.is_dir():
            continue
        locales.update(path.stem for path in directory.glob("*.yaml"))
    return sorted(locales)


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


def _read_catalog(resource, origin: str) -> dict[str, Any]:
    """1 つのカタログ YAML を読む（壊れていても例外は投げない）。

    Args:
        resource: `is_file()` と `read_text()` を持つ `Path` または Traversable。
        origin: ログに出す読み込み元の説明（`locale=ja` など）。

    Returns:
        カタログの辞書。存在しない・壊れている場合は空の辞書。
    """
    if not resource.is_file():
        return {}
    try:
        data = yaml.safe_load(resource.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error("Failed to load the message catalog (%s): %s", origin, e, exc_info=True)
        return {}
    if data is None:
        return {}
    if not isinstance(data, dict):
        logger.error("Message catalog is not a mapping (%s)", origin)
        return {}
    return data


@lru_cache(maxsize=None)
def _load_core_catalog(locale: str) -> dict[str, Any]:
    """コア同梱カタログだけを読み込む（プロセス内でキャッシュする）。

    Args:
        locale: ロケール名。

    Returns:
        カタログの辞書。存在しない・壊れている場合は空の辞書。
    """
    return _read_catalog(_locales_dir().joinpath(f"{locale}.yaml"), f"locale={locale}")


@lru_cache(maxsize=1)
def _core_top_level_keys() -> frozenset[str]:
    """コア同梱カタログが使っているトップレベルのキー名を全ロケール分集める。

    拡張名がこれらと衝突すると名前空間が混ざるため、ロード時に fail-fast する。

    Returns:
        トップレベルキー名の集合。
    """
    keys: set[str] = set()
    for locale in _core_locales():
        keys.update(_load_core_catalog(locale))
    return frozenset(keys)


@lru_cache(maxsize=None)
def _load_catalog(locale: str) -> dict[str, Any]:
    """指定ロケールのカタログを読み込む（プロセス内でキャッシュする）。

    コアの `{locale}.yaml` に、各拡張の `{locale}.yaml` をロード順で重ねた
    1 つの辞書を返す。拡張のカタログは「トップレベルのキーがその拡張の `name`
    ただ 1 つ」という名前空間の規約を持つため、単純な辞書の合成で足りる。

    Args:
        locale: ロケール名。

    Returns:
        合成したカタログの辞書。存在しない・壊れている場合は空の辞書。

    Raises:
        ValueError: 拡張のカタログのトップレベルキーがその拡張の `name` と
            異なる場合、または拡張名がコアのカタログのトップレベルキーと
            衝突している場合。
    """
    if not _LOCALE_RE.match(locale):
        logger.warning("Ignoring invalid locale name: %r", locale)
        return {}

    catalog = dict(_load_core_catalog(locale))
    core_keys = _core_top_level_keys()
    for name, directory in _extension_locale_dirs():
        if name in core_keys:
            raise ValueError(
                "Extension name collides with a top-level key of the core message catalog "
                f"(extension={name})"
            )
        if not directory.is_dir():
            logger.warning(
                "Extension message catalog directory does not exist (extension=%s, path=%s)",
                name,
                directory,
            )
            continue
        data = _read_catalog(
            directory / f"{locale}.yaml", f"extension={name}, locale={locale}"
        )
        if not data:
            continue
        unexpected = sorted(set(data) - {name})
        if unexpected:
            raise ValueError(
                "Extension message catalog must only have the extension name as its top-level "
                f"key (extension={name}, locale={locale}, unexpected={unexpected})"
            )
        catalog[name] = data[name]
    return catalog

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
    """カタログのキャッシュと警告の記録を捨てる。

    合成結果は登録済み拡張に依存するため、`set_extensions()` /
    `reset_extensions()` もこの関数を呼ぶ。
    """
    _load_catalog.cache_clear()
    _load_core_catalog.cache_clear()
    _core_top_level_keys.cache_clear()
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
    """コアと拡張の全カタログから、そのキーの文言を重複なく集めて返す。

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
