"""テキストファイルを OS のファイルシステムのように扱える保存先の共通インタフェース。

拡張は `FileStorage` を実装し、`Extension.file_storages()` で「名前 → 実装」を
申告する。利用側（日記の書き出しなど）は `core/extension.py` の
`get_file_storage(name)` / `list_file_storages()` で引き、実装のモジュールを
import しない（申告した拡張を `requires` しなくてよい）。

パスは保存先ごとの論理パス（`/` 区切りの相対パス）で、検証・ホワイトリストは
実装側が受け持つ。メソッドは最小限（`read` / `write` / `list` / `exists`）で、
検索・削除・移動は持たない（保存先によっては作りにくいため）。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

#: `FileStorage.write()` に渡せる `mode`。
WRITE_MODES = ("create", "overwrite", "append")


class FileStorageError(Exception):
    """保存先の操作の失敗（メッセージは利用者や LLM へそのまま見せてよい）。"""


class FileStoragePathError(FileStorageError):
    """パスの形式が正しくない（絶対パス・`..`・使えない文字など）。"""


class FileStorageNotFoundError(FileStorageError):
    """パスが存在しない、またはアクセスが許可されていない。"""


class FileStoragePermissionError(FileStorageError):
    """書き込めない保存先・書き込めない場所への書き込み。"""


class FileStorageExistsError(FileStorageError):
    """`mode="create"` で、ファイルが既にある。"""


class FileStorageNotTextError(FileStorageError):
    """テキスト（UTF-8）として読めない、または大きすぎるファイル。"""


@dataclass(frozen=True)
class FileStorageEntry:
    """`FileStorage.list()` が返すエントリ 1 つ。"""

    #: 論理パス（`read` / `write` / `list` にそのまま渡せる）。
    path: str
    #: 末尾の要素。
    name: str
    #: `"file"` か `"dir"`。
    type: str
    #: ファイルの大きさ（バイトか文字数。保存先による。不明・ディレクトリは `None`）。
    size: int | None = None
    #: 更新日時（timezone-aware。不明なら `None`）。
    modified_time: datetime | None = None


@dataclass(frozen=True)
class FileWriteResult:
    """`FileStorage.write()` の結果。"""

    #: 書いたファイルの論理パス（正規化後）。
    path: str
    #: 新しく作ったなら True。
    created: bool


class FileStorage(ABC):
    """テキストファイルの保存先。

    実装はコンストラクタで外部へ接続しないこと（`Extension.file_storages()` は
    拡張の登録時に呼ばれるため）。接続は各メソッドの中で遅延して行う。
    """

    @property
    def read_only(self) -> bool:
        """書き込めない保存先なら True（既定は False）。画面の選択肢から外すのに使う。"""
        return False

    @abstractmethod
    async def read(self, path: str) -> str:
        """テキストファイルの内容を返す。

        Raises:
            FileStoragePathError: パスの形式が正しくない場合。
            FileStorageNotFoundError: 存在しない・アクセスが許可されていない場合。
            FileStorageNotTextError: テキストとして読めない場合。
        """

    @abstractmethod
    async def write(
        self, path: str, content: str, mode: str = "overwrite"
    ) -> FileWriteResult:
        """テキストファイルを書く。

        Args:
            path: 書くファイルの論理パス。
            content: 書く内容。
            mode: `create`（既にあれば失敗）/ `overwrite`（丸ごと置き換え。無ければ作る）/
                `append`（末尾に追記。無ければ作る）。

        Raises:
            ValueError: `mode` が `WRITE_MODES` に無い場合。
            FileStoragePathError: パスの形式が正しくない場合。
            FileStoragePermissionError: 書き込めない場合。
            FileStorageExistsError: `create` で既にある場合。
        """

    @abstractmethod
    async def list(self, path: str = "") -> list[FileStorageEntry]:
        """ディレクトリの直下のエントリを名前順に返す（再帰しない）。空文字は最上位。

        Raises:
            FileStoragePathError: パスの形式が正しくない場合。
            FileStorageNotFoundError: 存在しない・アクセスが許可されていない場合。
        """

    @abstractmethod
    async def exists(self, path: str) -> bool:
        """ファイルかディレクトリがあれば True（アクセスできない場所は False）。

        Raises:
            FileStoragePathError: パスの形式が正しくない場合。
        """


def check_write_mode(mode: str) -> None:
    """`mode` が `WRITE_MODES` のどれかであることを確かめる。

    Raises:
        ValueError: どれでもない場合。
    """
    if mode not in WRITE_MODES:
        raise ValueError(f"mode must be one of {', '.join(WRITE_MODES)}: {mode!r}")
