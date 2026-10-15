"""Google Drive のホワイトリスト判定。

ファイルの親を辿り、`extensions.google_drive` の `read_only_folders` /
`writable_folders` に書かれたフォルダへたどり着くかどうかで、そのファイルに
許す操作を決める。最も近い（親を辿る回数が少ない）ホワイトリストのフォルダで
判定するため、書き込み可能フォルダの中に読み取り専用フォルダを置けば、その配下は
読み取り専用になる（逆も同じ）。
"""
from __future__ import annotations

import logging
import re
from enum import IntEnum
from typing import Any, Iterable

import aiohttp

logger = logging.getLogger(__name__)

#: Drive のファイル ID に使われる文字（URL のパスへそのまま入れるため、これ以外は拒否する）。
_DRIVE_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,256}")

#: 親を辿る回数の上限（循環や異常に深い階層で止まらなくなるのを防ぐ）。
MAX_ANCESTOR_DEPTH = 64


def is_valid_drive_id(value: Any) -> bool:
    """Drive のファイル ID（フォルダ・スプレッドシートを含む）として妥当な文字列かを返す。

    LLM から受け取った ID を API の URL のパスへ入れる前に、`/` や `..` などで
    別のエンドポイントを指せないことを確かめる。
    """
    return isinstance(value, str) and _DRIVE_ID_PATTERN.fullmatch(value) is not None


class Access(IntEnum):
    """ファイルに許す操作（大きいほど強い）。"""

    NONE = 0
    READ = 1
    WRITE = 2


class AccessResolver:
    """ホワイトリストの判定器（1 回のツール実行の間だけ使う）。

    フォルダの親の取得結果をインスタンス内にキャッシュし、検索結果の絞り込みのように
    同じ祖先を何度も辿る場合の API 呼び出しを減らす。フォルダの移動に追従できるよう、
    ツール実行をまたいでは使い回さない。
    """

    def __init__(
        self,
        client: Any,
        read_only_folders: Iterable[str],
        writable_folders: Iterable[str],
    ) -> None:
        """判定に使うクライアントとホワイトリストを保持する。

        Args:
            client: `get_metadata()` を持つ Drive クライアント。
            read_only_folders: 読み取りだけを許すフォルダの ID。
            writable_folders: 読み書きを許すフォルダの ID。
        """
        self._client = client
        self._read_only = frozenset(read_only_folders)
        self._writable = frozenset(writable_folders)
        self._parents_cache: dict[str, list[str]] = {}

    @property
    def is_empty(self) -> bool:
        """ホワイトリストが両方とも空（すべて拒否）なら True。"""
        return not (self._read_only or self._writable)

    def _listed_access(self, file_id: str) -> Access | None:
        """ID そのものがホワイトリストにあればその操作を、無ければ None を返す。"""
        if file_id in self._writable:
            return Access.WRITE
        if file_id in self._read_only:
            return Access.READ
        return None

    async def _parents_of(self, file_id: str) -> list[str]:
        """親フォルダの ID を返す（見えない・存在しないフォルダは親なしとして扱う）。"""
        if file_id in self._parents_cache:
            return self._parents_cache[file_id]
        try:
            metadata = await self._client.get_metadata(file_id)
        except aiohttp.ClientResponseError as e:
            # 共有されたファイルの親が自分からは見えない場合など。そこで辿るのをやめる。
            if e.status not in (403, 404):
                raise
            parents: list[str] = []
        else:
            parents = list(metadata.get("parents") or [])
        self._parents_cache[file_id] = parents
        return parents

    async def resolve(self, file_id: str, parents: list[str] | None = None) -> Access:
        """ファイルに許す操作を判定する。

        ファイル自身の ID から始めて親を 1 段ずつ辿り、最初にホワイトリストへ
        当たった段で決める。同じ段で読み取り専用と書き込み可能の両方に当たったら
        安全側（読み取り専用）に倒す。どこにも当たらなければ `Access.NONE`。

        Args:
            file_id: 判定するファイル（フォルダを含む）の ID。
            parents: 取得済みならそのファイルの親の ID（API 呼び出しを 1 回省く）。

        Returns:
            許す操作。
        """
        if self.is_empty:
            return Access.NONE
        if parents is not None:
            self._parents_cache.setdefault(file_id, list(parents))

        frontier = [file_id]
        seen = {file_id}
        for _ in range(MAX_ANCESTOR_DEPTH):
            hits = [access for f in frontier if (access := self._listed_access(f)) is not None]
            if hits:
                return min(hits)
            next_frontier: list[str] = []
            for f in frontier:
                for parent in await self._parents_of(f):
                    if parent not in seen:
                        seen.add(parent)
                        next_frontier.append(parent)
            if not next_frontier:
                return Access.NONE
            frontier = next_frontier
        logger.warning("Gave up resolving Drive ancestors after %d levels", MAX_ANCESTOR_DEPTH)
        return Access.NONE
