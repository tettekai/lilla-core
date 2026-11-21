"""テキストリソース読み込みの統一ユーティリティ。

`file:` / `dir:` のプレフィックス付き source spec を受け取り、
単一ファイル・ディレクトリ一括読み込みの両方を扱う。

- `file:path` : 単一ファイルを読み込む
- `dir:path`  : 直下の `.md` / `.txt` をファイル名昇順で全件読み込む（再帰しない）
- プレフィックスなし : `ValueError` を送出（明示必須）

`${config_root}` プレースホルダは `load_text_resources` 内部で展開する。

パスが `$notes` か `$notes/` で始まる（`file:$notes/memory/core.md` など。プレフィックスの
直後の空白は無視する）ときは、ディスクではなく保存先 `notes`（`get_file_storage("notes")`）の
論理パスとして読む。コアが知るのは「`$notes` が保存先 `notes` である」ことだけで、
その配下のパスの形には触れない。読み込みは保存先の非同期の経路（`read` / `list`）を通す
ため、この関数自体も非同期にしている。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

SourceSpec = str | list[str]

#: notes の保存先を指すプレースホルダ。
NOTES_PLACEHOLDER = "$notes"
#: プレースホルダが指す保存先の名前（`Extension.file_storages()` の名前）。
NOTES_STORAGE_NAME = "notes"
#: `dir:` で結合するファイルの拡張子。
_DIR_SUFFIXES = (".md", ".txt")


@dataclass(frozen=True)
class _NotesSpec:
    """`$notes` を指す 1 要素（`kind` は `file` / `dir`、`path` は notes の論理パス）。"""

    kind: str
    path: str


async def load_text_resources(
    sources: SourceSpec,
    config_root: str | Path | None = None,
) -> str:
    """source spec からテキストを読み込み、結合した文字列を返す。

    呼び出すたびに読み直す（キャッシュしない）。ディスクと `$notes` の指定は混在でき、
    書いた順に結合する。

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
        対象ファイルが存在しない場合はスキップする（`$notes` も同じ）。

    Raises
    ------
    ValueError
        プレフィックスのない spec が含まれていた場合。
    lilla_core.core.file_storage.FileStoragePathError
        `$notes` の後ろのパスが notes のパス規則に合わない場合（`..`・先頭の `/`・空の要素など）。
        欠落として黙ってスキップはしない。
    """
    from lilla_core.core.config import get_config

    cfg_root = str(config_root or get_config().env.config_root)

    if isinstance(sources, str):
        sources = [sources]

    parts: list[str] = []
    for item in _resolve_specs(sources, cfg_root):
        if isinstance(item, _NotesSpec):
            parts.extend(await _read_notes(item))
        elif item.exists():
            parts.append(item.read_text(encoding="utf-8"))
    return "\n".join(part.rstrip("\n") for part in parts)


def _resolve_specs(specs: list[str], config_root: str) -> list[Path | _NotesSpec]:
    """source spec のリストを、読み込む対象（実ファイルパスか notes の指定）のリストに解決する。

    Parameters
    ----------
    specs : list[str]
        `file:` / `dir:` プレフィックス付きの source spec のリスト。
    config_root : str
        `${config_root}` 展開に使うルートパス。

    Returns
    -------
    list[Path | _NotesSpec]
        書いた順の読み込み対象。ディスクの `dir:` はここで直下のファイルへ展開する。

    Raises
    ------
    ValueError
        プレフィックスのない spec が含まれていた場合。
    """
    items: list[Path | _NotesSpec] = []
    for spec in specs:
        expanded = spec.replace("${config_root}", config_root)
        if expanded.startswith("file:"):
            kind, rest = "file", expanded[5:]
        elif expanded.startswith("dir:"):
            kind, rest = "dir", expanded[4:]
        else:
            raise ValueError(
                f"Unknown source prefix (file: or dir: is required): {spec!r}"
            )
        notes_path = notes_logical_path(rest)
        if notes_path is not None:
            items.append(_NotesSpec(kind, notes_path))
        elif kind == "file":
            items.append(Path(rest))
        else:
            items.extend(_resolve_dir(Path(rest)))
    return items


def notes_logical_path(path: str) -> str | None:
    """プレフィックスの後ろが `$notes` を指していれば notes の論理パスを、違えば None を返す。

    `$notes` と `$notes/` は最上位（空文字）、`$notes/a/b.md` は `a/b.md`。前の空白は
    無視する。`$notesx` のように続くものは `$notes` とみなさない（ディスクのパスとして扱う）。
    """
    stripped = path.lstrip()
    if stripped == NOTES_PLACEHOLDER:
        return ""
    if stripped.startswith(NOTES_PLACEHOLDER + "/"):
        return stripped[len(NOTES_PLACEHOLDER) + 1:]
    return None


def _resolve_dir(dir_path: Path) -> list[Path]:
    """ディレクトリ直下の `.md` / `.txt` をファイル名昇順で返す。

    対象ディレクトリが存在しない場合は空リストを返す。
    """
    if not dir_path.exists() or not dir_path.is_dir():
        return []
    return sorted(
        f for f in dir_path.iterdir()
        if f.is_file() and f.suffix in _DIR_SUFFIXES
    )


async def _read_notes(spec: _NotesSpec) -> list[str]:
    """`$notes` の 1 要素を読み、本文のリストを返す（ディスクの `file:` / `dir:` と同じ規則）。

    無いファイル・ディレクトリは空。保存先が登録されていない・リポジトリを開けないなど
    読み取りそのものができないときも、WARNING を出して空にする（起動や会話は止めない）。
    パスの形式が正しくないときだけ例外を送出する。
    """
    from lilla_core.core.extension import get_file_storage
    from lilla_core.core.file_storage import FileStorageNotFoundError, FileStoragePathError

    storage = get_file_storage(NOTES_STORAGE_NAME)
    if storage is None:
        logger.warning(
            "File storage '%s' is not registered; skipping prompt source %s:%s",
            NOTES_STORAGE_NAME, spec.kind, _display(spec.path),
        )
        return []
    try:
        if spec.kind == "file":
            return [await storage.read(spec.path)]
        # 先に `exists` でパスの形式を確かめる（形式の誤りはここで例外になる）。そのあとの
        # `list` のパスエラーは「ファイルを指している」で、ディスクの `dir:` と同じく空にする
        if not await storage.exists(spec.path):
            return []
        try:
            entries = await storage.list(spec.path)
        except FileStoragePathError:
            return []
        names = sorted(
            (e.name, e.path) for e in entries
            if e.type == "file" and e.name.endswith(_DIR_SUFFIXES)
        )
        texts: list[str] = []
        for _, path in names:
            try:
                texts.append(await storage.read(path))
            except FileStorageNotFoundError:
                # 一覧のあとに消えたものは、無いファイルと同じく飛ばす
                continue
        return texts
    except FileStorageNotFoundError:
        return []
    except FileStoragePathError:
        raise
    except Exception as e:
        logger.warning(
            "Failed to read prompt source %s:%s from '%s'; treating it as empty: %s",
            spec.kind, _display(spec.path), NOTES_STORAGE_NAME, type(e).__name__,
        )
        return []


def _display(path: str) -> str:
    """ログに出す `$notes` のパスを返す。"""
    return f"{NOTES_PLACEHOLDER}/{path}" if path else NOTES_PLACEHOLDER
