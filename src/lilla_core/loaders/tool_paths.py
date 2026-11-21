"""ツール探索ディレクトリの解決と、ツールファイルの検索を共通化するモジュール。

LLM ツール（`llm_tool_loader`）と task ツール（`task_tool_loader`）は同じ
ルート集合を探索する。ルートは `paths.tool_root` を先頭に、拡張の
`tool_roots()` をロード順で足し、最後にコア組み込みツール
（`lilla_core/builtin_tools`。`BUILTIN_TOOLS_ROOT`）を足したもの。組み込みツールも
拡張と同じ仕組みでファイル名から引けるようにするためで、探索先に入っていても
YAML が無いツールはロードしない（opt-in は変わらない）。

同名のツールファイルが複数のルートにあると、どちらが使われるかが暗黙になる
ため fail-fast する（先頭マッチで静かに採らない）。ただし `builtin_tools` は
常に負ける側で、ホストや拡張に同名ファイルがあればそちらを使い、起動は止めない
（利用者が同名で差し替えられるようにするため）。1 つのルートの中に同名
ファイルが複数ある場合は従来どおり先頭マッチを使う。

追加ルートは **ファイル探索だけ** に使い、`sys.path` へは挿入しない。
拡張のツールが Python パッケージとして import される必要があるなら、
インストール済みパッケージにすること。

YAML の `type` が `.` を含む場合は import パスとみなし、ファイル探索ではなく
`importlib.import_module` で解決する（`import_tool_module`）。インストール済みの
パッケージ（PyPI 配布の拡張など）がツールを同梱するための経路で、モジュールは通常の
import と同じく `sys.modules` に登録され、相対 import も使える。import に失敗した場合は
ファイルが見つからないときと同じく WARNING を出してそのツールだけスキップする。
import できるパッケージは `LILLA_EXTENSIONS` のモジュールと同じ信頼レベルとみなし、
ディレクトリの検査は行わない。

ツール YAML（`llm_*.yaml` / `task_*.yaml`）は `resolve_tool_configs()` が集める。
拡張が `tool_config_roots()` で同梱した既定 YAML には、
`${CONFIG_ROOT}/extensions/<拡張名>/tools/` の同じ stem の YAML を差分として
トップレベルのキー単位で重ねる。`${CONFIG_ROOT}/tools` は利用者自身のツール専用で、
同梱と同じ stem があれば fail-fast する（拡張どうしの同じ stem も fail-fast）。

ツールの `.py` をロードしてよいディレクトリは `resolve_tool_dirs()` が返す
「探索ルート + `config_root/tools`（`type: self` の置き場所）」で、これ以外の設定は
持たない。`find_tool_file` は YAML の `type` を `rglob` のパターンとして使うため、
`..` を含む `type` や、ルート内のシンボリックリンクが外を指す場合は解決後のパスが
ルートの外へ出うる。ローダーは読み込む直前に `is_within_tool_dirs()` で解決後の
パスを検査し、外へ出ていればロードしない。これは信頼境界ではなく不変条件の検査で、
`CONFIG_ROOT` と各探索ルートへ書き込める者はコードを実行できる（`SECURITY.md`）。
"""
from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

import yaml

from lilla_core.core.config import get_config
from lilla_core.core.extension import get_extensions, get_tool_config_roots, get_tool_roots

logger = logging.getLogger(__name__)

# コア組み込みツール（`lilla_core/builtin_tools`）のディレクトリ。常に最後の探索ルート。
BUILTIN_TOOLS_ROOT = Path(__file__).resolve().parent.parent / "builtin_tools"


def resolve_tool_roots() -> list[Path]:
    """探索対象のツールルートを順序付きで返す。

    `paths.tool_root` が先頭で、そのあとに拡張の `tool_roots()` がロード順で
    続き、最後にコア組み込みツールの `BUILTIN_TOOLS_ROOT` が付く。同じ
    ディレクトリを指す重複は取り除く（解決後のパスで比較する）。

    Returns:
        重複を除いたツールルートのリスト。
    """
    roots: list[Path] = [
        get_config().paths.tool_root,
        *get_tool_roots(),
        BUILTIN_TOOLS_ROOT,
    ]
    unique: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        resolved = Path(root).resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(Path(root))
    return unique


def resolve_tool_dirs(
    tool_roots: list[Path] | None = None, config_root: Path | None = None
) -> list[Path]:
    """ツールの `.py` をロードしてよいディレクトリを解決済みのパスで返す。

    探索ルート（`tool_roots`。省略時は `resolve_tool_roots()`。末尾に
    `BUILTIN_TOOLS_ROOT` を含む）に、`type: self` の `.py` が置かれる
    `config_root/tools`（省略時は設定の `env.config_root`）を足す。

    Args:
        tool_roots: 探索に使ったツールルート。`None` なら設定と拡張から解決する。
        config_root: 設定ルート。`None` なら設定から取る。

    Returns:
        `resolve()` 済みのディレクトリのリスト（順序は探索ルート → `config_root/tools`）。
    """
    if tool_roots is None:
        tool_roots = resolve_tool_roots()
    if config_root is None:
        config_root = get_config().env.config_root
    return (
        [Path(root).resolve() for root in tool_roots]
        + [root.resolve() for _name, root in get_tool_config_roots()]
        + [(Path(config_root) / "tools").resolve()]
    )


#: `${CONFIG_ROOT}` の下で、拡張の同梱ツールの設定を上書きする YAML を置くディレクトリ。
#: `${CONFIG_ROOT}/extensions/<拡張名>/tools/{llm_*,task_*}.yaml` の形になる。
EXTENSION_OVERRIDE_DIR = "extensions"

#: 拡張が同梱するツール YAML のファイル名プレフィックス。
TOOL_CONFIG_PREFIXES = ("llm_", "task_")


@dataclass(frozen=True)
class ToolConfigEntry:
    """ロード対象のツール YAML 1 つ分（上書きを重ねたあと）。

    Attributes:
        name: YAML のファイル名 stem（ツール名）。
        path: 元の YAML のパス（拡張の同梱分か `${CONFIG_ROOT}/tools` のもの）。
            `type: self` の `.py` はこのパスの隣を探す。
        config: 元の YAML に上書きの YAML をトップレベルのキー単位で重ねた dict。
        extension: この YAML を `tool_config_roots()` で同梱した拡張の名前。
            利用者が `${CONFIG_ROOT}/tools` に置いたものは `None`。
        override_path: 上書きの YAML のパス。無ければ `None`。
        override_keys: 上書きの YAML に書かれていたキー。
    """

    name: str
    path: Path
    config: dict
    extension: str | None = None
    override_path: Path | None = None
    override_keys: frozenset[str] = field(default_factory=frozenset)


def _read_yaml_mapping(path: Path, *, strict: bool) -> dict:
    """YAML を読み、トップレベルの dict を返す（空のファイルは空の dict）。

    `strict` が真なら、構文エラーとトップレベルが dict でないことをロード時の
    エラーにする（上書きの YAML 用）。偽なら従来どおり例外をそのまま伝え、
    dict でない値は空の dict として扱う。

    Raises:
        ValueError: `strict` で、構文エラーかトップレベルが dict でないとき。
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        if not strict:
            raise
        raise ValueError(f"Failed to parse tool config override {path}: {e}") from e
    if data is None:
        return {}
    if not isinstance(data, dict):
        if strict:
            raise ValueError(
                f"Tool config override {path} must be a mapping, got {type(data).__name__}"
            )
        return {}
    return data


def _collect_bundled() -> tuple[dict[str, tuple[str, Path]], dict[str, set[str]]]:
    """拡張が同梱したツール YAML を集める。

    Returns:
        `(stem -> (拡張名, YAML パス), 拡張名 -> 同梱した stem の集合)` の組。

    Raises:
        ValueError: 複数の拡張が同じ stem の YAML を同梱している場合。
    """
    bundled: dict[str, tuple[str, Path]] = {}
    by_extension: dict[str, set[str]] = {}
    for ext_name, root in get_tool_config_roots():
        if not root.is_dir():
            continue
        for prefix in TOOL_CONFIG_PREFIXES:
            for path in sorted(root.glob(f"{prefix}*.yaml")):
                stem = path.stem
                if stem in bundled:
                    raise ValueError(
                        f"Tool config '{stem}.yaml' is bundled by both extensions "
                        f"'{bundled[stem][0]}' and '{ext_name}'"
                    )
                bundled[stem] = (ext_name, path)
                by_extension.setdefault(ext_name, set()).add(stem)
    return bundled, by_extension


def _collect_overrides(
    config_root: Path, by_extension: dict[str, set[str]]
) -> dict[str, Path]:
    """`${CONFIG_ROOT}/extensions/<拡張名>/tools/` の上書きの YAML を集めて検証する。

    `extensions/` の下のディレクトリはロード済みの拡張の名前でなければならず、
    その `tools/` の YAML はその拡張が同梱したツールの stem でなければならない。
    `extensions/` の直下のファイルと、`tools/` の YAML 以外のファイルは見ない。

    Returns:
        stem -> 上書きの YAML のパス。

    Raises:
        ValueError: 知らない拡張名のディレクトリ、またはその拡張が同梱していない
            stem の YAML があるとき。
    """
    base = Path(config_root) / EXTENSION_OVERRIDE_DIR
    if not base.is_dir():
        return {}
    loaded = {ext.name for ext in get_extensions()}
    overrides: dict[str, Path] = {}
    for ext_dir in sorted(base.iterdir()):
        if not ext_dir.is_dir():
            continue
        if ext_dir.name not in loaded:
            raise ValueError(
                f"Tool config override directory {ext_dir} does not match any loaded extension"
            )
        tools_dir = ext_dir / "tools"
        if not tools_dir.is_dir():
            continue
        bundled = by_extension.get(ext_dir.name, set())
        for path in sorted(tools_dir.glob("*.yaml")):
            if path.stem not in bundled:
                raise ValueError(
                    f"Tool config override {path} does not match any tool bundled by "
                    f"extension '{ext_dir.name}'"
                )
            overrides[path.stem] = path
    return overrides


def resolve_tool_configs(prefix: str, config_root: Path | None = None) -> list[ToolConfigEntry]:
    """ロード対象のツール YAML を、上書きを重ねたうえで stem 順に返す。

    集めるものは次の 2 つで、ツール名（stem）の空間は 1 つ。

    - 拡張が `tool_config_roots()` で同梱した YAML。`${CONFIG_ROOT}/extensions/<拡張名>/tools/`
      に同じ stem の YAML があれば、その中身をトップレベルのキー単位で重ねる（書いた
      キーだけを置き換え、値がリストでも要素ではなく丸ごと置き換える）
    - 利用者自身のツール（`${CONFIG_ROOT}/tools`）。拡張が同梱した stem と同じ名前は
      置けない

    `enabled` の判定はしない（重ねたあとの dict を見て呼び出し側が決める）。

    Args:
        prefix: 集める YAML のファイル名プレフィックス（`"llm_"` または `"task_"`）。
        config_root: 設定ルート。`None` なら設定から取る。

    Returns:
        `ToolConfigEntry` のリスト（stem の昇順）。

    Raises:
        ValueError: 拡張どうしで同じ stem を同梱している・`${CONFIG_ROOT}/tools` に
            同梱と同じ stem がある・上書きのディレクトリや YAML が同梱と合わない・
            上書きの YAML が読めない場合。
    """
    if config_root is None:
        config_root = get_config().env.config_root

    bundled, by_extension = _collect_bundled()
    overrides = _collect_overrides(Path(config_root), by_extension)

    entries: dict[str, ToolConfigEntry] = {}
    for stem, (ext_name, path) in bundled.items():
        if not stem.startswith(prefix):
            continue
        config = dict(_read_yaml_mapping(path, strict=False))
        override_path = overrides.get(stem)
        override_keys: frozenset[str] = frozenset()
        if override_path is not None:
            override = _read_yaml_mapping(override_path, strict=True)
            config.update(override)
            override_keys = frozenset(override)
            logger.info(
                "Tool config '%s.yaml' of extension '%s' is overridden by %s (keys: %s)",
                stem, ext_name, override_path, ", ".join(sorted(override_keys)) or "none",
            )
        entries[stem] = ToolConfigEntry(
            name=stem,
            path=path,
            config=config,
            extension=ext_name,
            override_path=override_path,
            override_keys=override_keys,
        )

    user_dir = Path(config_root) / "tools"
    if user_dir.is_dir():
        for path in sorted(user_dir.glob(f"{prefix}*.yaml")):
            if path.stem in bundled:
                ext_name = bundled[path.stem][0]
                raise ValueError(
                    f"Tool config {path} has the same name as a tool bundled by extension "
                    f"'{ext_name}'. Put overrides in "
                    f"{Path(config_root) / EXTENSION_OVERRIDE_DIR / ext_name / 'tools'} instead"
                )
            entries[path.stem] = ToolConfigEntry(
                name=path.stem,
                path=path,
                config=_read_yaml_mapping(path, strict=False),
            )

    return [entries[stem] for stem in sorted(entries)]


def is_tool_enabled(config: dict) -> bool:
    """ツール YAML の `enabled` キーを見て、そのツールをロードするかを返す。

    `enabled: false` と明示したときだけ無効。キーが無い、または `false` 以外の値なら
    有効とみなす（真偽値以外の値で静かに無効化されないようにするため）。
    `${CONFIG_ROOT}/extensions/<拡張名>/tools/` に同じ stem の YAML を置いて
    `enabled: false` と書けば、拡張が同梱したツールを利用者側で止められる。

    Args:
        config: YAML から読んだ dict。

    Returns:
        ロードするなら `True`。
    """
    return config.get("enabled") is not False


def is_within_tool_dirs(path: Path, tool_dirs: list[Path]) -> bool:
    """`path` の解決後の位置が `tool_dirs` のいずれかの配下にあるかを返す。

    `path` と各ディレクトリの両方を `resolve()` してから比較するため、`..` や
    シンボリックリンクで探索ルートの外へ出たパスは `False` になる。

    Args:
        path: 検査するファイルパス。
        tool_dirs: ロードを許すディレクトリ。

    Returns:
        いずれかの配下にあれば `True`。
    """
    resolved = Path(path).resolve()
    return any(resolved.is_relative_to(Path(d).resolve()) for d in tool_dirs)


def is_import_path(tool_type: str) -> bool:
    """YAML の `type` が import パス（ドット区切りのモジュール名）かどうかを返す。

    `.` を 1 つでも含めば import パスとみなす。ファイル名 stem による探索は
    `{type}.py` を探すため、stem に `.` が含まれることは実用上ない。

    Args:
        tool_type: YAML の `type` 値。

    Returns:
        import パスなら `True`。
    """
    return "." in tool_type


def import_tool_module(tool_type: str) -> ModuleType | None:
    """import パス形式の `type` を `importlib.import_module` で解決して返す。

    モジュールが無い、または import 中に例外が出た場合は、ファイルが見つからない
    ときと同じ扱いで WARNING を出して `None` を返す（そのツールだけスキップし、
    起動は止めない）。

    Args:
        tool_type: ドット区切りの import パス（例: `"lilla_google_calendar.tools.calendar_get"`）。

    Returns:
        import したモジュール。失敗時は `None`。
    """
    try:
        return importlib.import_module(tool_type)
    except Exception as e:
        logger.warning("Failed to import tool module '%s': %s", tool_type, e, exc_info=True)
        return None


def _is_builtin_tools_root(root: Path) -> bool:
    """`root` がコア組み込みツールのディレクトリ（またはその配下）かを返す。"""
    resolved = Path(root).resolve()
    return resolved.is_relative_to(BUILTIN_TOOLS_ROOT)


def find_tool_file(tool_type: str, tool_roots: list[Path]) -> Path | None:
    """各ルートを再帰的に検索し、`{tool_type}.py` にマッチするファイルを返す。

    コア組み込みツールのルート（`BUILTIN_TOOLS_ROOT`）は常に負ける側として扱い、
    ホストや拡張のルートにマッチがあればそちらを返す（組み込みツールは同名で
    差し替えられる）。組み込み以外のルートどうしで同名の場合は従来どおり
    fail-fast する。

    Args:
        tool_type: YAML の `type` 値（例: `"llm_web_search"`）。
        tool_roots: 探索するルートのリスト（先頭が優先）。

    Returns:
        マッチしたファイルパス。見つからない場合は `None`。

    Raises:
        ValueError: 組み込み以外の複数のルートに同名のツールファイルがある場合。
    """
    matches: list[Path] = []
    builtin_matches: list[Path] = []
    for root in tool_roots:
        if not root.is_dir():
            continue
        for match in root.rglob(f"{tool_type}.py"):
            (builtin_matches if _is_builtin_tools_root(root) else matches).append(match)
            break

    if len(matches) > 1:
        raise ValueError(
            f"Tool file '{tool_type}.py' found in multiple tool roots: "
            f"{[str(m) for m in matches]}"
        )
    if matches:
        if builtin_matches:
            logger.info(
                "Tool file '%s.py' from %s overrides the built-in one at %s",
                tool_type, matches[0].parent, builtin_matches[0],
            )
        return matches[0]
    return builtin_matches[0] if builtin_matches else None
