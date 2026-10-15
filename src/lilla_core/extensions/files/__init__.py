"""ローカルのファイルシステム上のテキストファイルを読み書きする拡張（公式拡張パックの 1 つ）。

`LILLA_EXTENSIONS` には `lilla_core.extensions.files`（短縮記法では `files`）として並べる。
他の拡張には依存しない。

この拡張が持つもの:
- `extensions.files` セクションのモデル（`FilesConfig`）とその申告
- ホワイトリストのディレクトリへのパスの解決とアクセス判定（`access.py`）
- ツール共通の処理（`tool_support.py`）
- 一覧・読み取り・書き込み・検索の LLM ツール（`tools/`。`tool_roots()` でコアの探索ルートへ足す）
- 想定外のエラーをエラー通知チャンネルへ出すときの文言カタログ（`locales/`）

扱うのは設定でホワイトリストに入れたディレクトリの配下だけで、LLM とやりとりする
パスは「ディレクトリの名前/その中の相対パス」の形にする（`access.py` 参照）。
ツール YAML は利用者側の `${CONFIG_ROOT}/tools` にあるものをそのまま使うため、
`tool_config_roots()` での同梱はしない。

モジュールの import 時に `extension` インスタンスを生成するため、ここでは
サブモジュール（`access` など）を import しない（`google-drive` と同じ切り方）。
"""
from __future__ import annotations

import ntpath
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from lilla_core.core.extension import Extension

#: `llm_file_read` で一度に返す本文の既定の上限（文字数）。google-drive と同じ値。
DEFAULT_MAX_READ_CHARS = 100_000


def _default_directory_name(path: str) -> str:
    """ディレクトリの名前を省略したときの既定（パスの末尾の要素）を返す。"""
    normalized = os.path.normpath(path)
    name = os.path.basename(normalized)
    if not name and os.name == "nt":
        # `\\server\share` のような UNC のルートは basename が空になるため、共有名を使う。
        name = ntpath.splitdrive(normalized)[0].rstrip("\\/").rsplit("\\", 1)[-1]
    return name


class FilesDirectory(BaseModel):
    """ホワイトリストのディレクトリ 1 つ（名前を明示したいときの書き方）。

    文字列だけで書いた場合は `path` だけを持ち、`name` はパスの末尾の要素になる。
    LLM はツールのパスの先頭に `name` を書いてこのディレクトリを指す。
    """

    model_config = ConfigDict(extra="forbid")

    #: ディレクトリの絶対パス（OS がそのまま開けるもの。UNC パスやマウント先でもよい）。
    path: str
    #: LLM から指すときの名前。省略したらパスの末尾の要素。
    name: str | None = None

    @field_validator("path")
    @classmethod
    def _validate_path(cls, value: str) -> str:
        """空白だけ・相対パスを拒否し、前後の空白を取り除く。"""
        value = value.strip()
        if not value:
            raise ValueError("directory paths must not be blank")
        if "://" in value:
            raise ValueError(f"URL-style paths are not supported: {value}")
        if not os.path.isabs(value):
            raise ValueError(f"directory paths must be absolute: {value}")
        return value

    @property
    def label(self) -> str:
        """LLM から指すときの名前（`name`、省略時はパスの末尾の要素）。"""
        return self.name.strip() if self.name is not None else _default_directory_name(self.path)


class FilesConfig(BaseModel):
    """lilla.yaml の `extensions.files` セクション。

    `read_only_dirs` / `writable_dirs` はホワイトリストで、指定したディレクトリと
    その配下すべてが対象になる。両方とも空ならすべてのアクセスを拒否する。
    各要素はパスの文字列か、`{path, name}` の辞書で書ける。
    """

    # 安全のためのホワイトリストなので、綴り間違いの項目名を黙って無視しない。
    model_config = ConfigDict(extra="forbid")

    #: 読み取りだけを許すディレクトリ。
    read_only_dirs: list[FilesDirectory] = []
    #: 読み取りと書き込みを許すディレクトリ。
    writable_dirs: list[FilesDirectory] = []
    #: `llm_file_read` で一度に返す本文の上限（文字数）。超えた分は `offset` で続きを読む。
    max_read_chars: int = Field(default=DEFAULT_MAX_READ_CHARS, gt=0)

    @field_validator("read_only_dirs", "writable_dirs", mode="before")
    @classmethod
    def _accept_plain_paths(cls, value):
        """文字列だけの要素を `{"path": ...}` として受け付ける。"""
        if isinstance(value, list):
            return [{"path": item} if isinstance(item, str) else item for item in value]
        return value

    @model_validator(mode="after")
    def _validate_directories(self) -> "FilesConfig":
        """名前の形式・名前の重複・同じパスの両方への記載を拒否する。"""
        labels: set[str] = set()
        for directory in [*self.read_only_dirs, *self.writable_dirs]:
            label = directory.label
            if (
                not label
                or label in (".", "..")
                or any(ch in label for ch in "/\\\0")
            ):
                raise ValueError(
                    f"invalid directory name {label!r} for {directory.path}; "
                    "set 'name' explicitly"
                )
            if label in labels:
                raise ValueError(
                    f"duplicate directory name {label!r}; set 'name' explicitly to tell them apart"
                )
            labels.add(label)

        def _keys(dirs: list[FilesDirectory]) -> set[str]:
            return {os.path.normcase(os.path.normpath(d.path)) for d in dirs}

        if _keys(self.read_only_dirs) & _keys(self.writable_dirs):
            raise ValueError(
                "a directory must not appear in both read_only_dirs and writable_dirs"
            )
        return self


class FilesExtension(Extension):
    """ローカルのテキストファイルを読み書きする設定とツールを申告する拡張。"""

    name = "files"

    def config_model(self) -> type[BaseModel]:
        """YAML セクションのモデルを返す（節名は `extensions.files`）。"""
        return FilesConfig

    def tool_roots(self) -> list[Path]:
        """LLM ツール（このパッケージの `tools/`）の探索先を返す。

        ツール YAML そのものは利用者の `${CONFIG_ROOT}/tools` にあるものを使う（同梱しない）。
        """
        return [Path(__file__).resolve().parent / "tools"]

    def locale_dirs(self) -> list[Path]:
        """エラー通知に出す文言のカタログ（このパッケージの `locales/`）を返す。"""
        return [Path(__file__).resolve().parent / "locales"]


extension = FilesExtension()
