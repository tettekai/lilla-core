"""テキストリソース読み込みの統一ユーティリティ。

`file:` / `dir:` のプレフィックス付き source spec を受け取り、
単一ファイル・ディレクトリ一括読み込みの両方を扱う。

- `file:path` : 単一ファイルを読み込む
- `dir:path`  : `.md` / `.txt` をファイル名昇順で全件読み込む
- プレフィックスなし : `ValueError` を送出（明示必須）

`${config_root}` プレースホルダは `load_text_resources` 内部で展開する。
"""
from __future__ import annotations

from pathlib import Path

SourceSpec = str | list[str]


def load_text_resources(
    sources: SourceSpec,
    config_root: str | Path | None = None,
) -> str:
    """source spec からテキストを読み込み、結合した文字列を返す。

    Parameters
    ----------
    sources : str | list[str]
        `file:` / `dir:` プレフィックス付きの source spec。
        文字列の場合は1要素リストと同じ挙動とする。
    config_root : str | Path | None
        `${config_root}` 展開に使うルートパス。未指定の場合は
        `get_config().env.config_root` を使用する。

    Returns
    -------
    str
        各リソースの内容を改行1つで連結した文字列。
        対象ファイルが存在しない場合はスキップする。
    """
    from lilla_core.core.config import get_config

    cfg_root = str(config_root or get_config().env.config_root)

    if isinstance(sources, str):
        sources = [sources]

    paths = _resolve_specs(sources, cfg_root)
    parts = [p.read_text(encoding="utf-8") for p in paths if p.exists()]
    return "\n".join(part.rstrip("\n") for part in parts)


def _resolve_specs(specs: list[str], config_root: str) -> list[Path]:
    """source spec のリストを実ファイルパスのリストに解決する。

    Parameters
    ----------
    specs : list[str]
        `file:` / `dir:` プレフィックス付きの source spec のリスト。
    config_root : str
        `${config_root}` 展開に使うルートパス。

    Returns
    -------
    list[Path]
        解決された読み込み対象パスのリスト。

    Raises
    ------
    ValueError
        プレフィックスのない spec が含まれていた場合。
    """
    paths: list[Path] = []
    for spec in specs:
        expanded = spec.replace("${config_root}", config_root)
        if expanded.startswith("file:"):
            paths.append(Path(expanded[5:]))
        elif expanded.startswith("dir:"):
            paths.extend(_resolve_dir(Path(expanded[4:])))
        else:
            raise ValueError(
                f"Unknown source prefix (file: or dir: is required): {spec!r}"
            )
    return paths


def _resolve_dir(dir_path: Path) -> list[Path]:
    """ディレクトリ配下の `.md` / `.txt` をファイル名昇順で返す。

    対象ディレクトリが存在しない場合は空リストを返す。
    """
    if not dir_path.exists() or not dir_path.is_dir():
        return []
    return sorted(
        f for f in dir_path.iterdir()
        if f.is_file() and f.suffix in (".md", ".txt")
    )
