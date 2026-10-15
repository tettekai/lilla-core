"""`lilla_core.extensions.google_drive.access`（ホワイトリスト判定）のテスト。"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from lilla_core.extensions.google_drive.access import (
    MAX_ANCESTOR_DEPTH,
    Access,
    AccessResolver,
    is_valid_drive_id,
)


def _http_error(status: int) -> aiohttp.ClientResponseError:
    """指定ステータスの ClientResponseError を作る。"""
    return aiohttp.ClientResponseError(MagicMock(), (), status=status, message="")


def _make_client(tree: dict[str, list[str]], errors: dict[str, int] | None = None) -> MagicMock:
    """ID → 親の ID のリストで Drive の階層を表すクライアントのモック。"""
    errors = errors or {}

    async def get_metadata(file_id: str) -> dict:
        if file_id in errors:
            raise _http_error(errors[file_id])
        return {"id": file_id, "parents": tree.get(file_id, [])}

    client = MagicMock()
    client.get_metadata = AsyncMock(side_effect=get_metadata)
    return client


class TestIsValidDriveId:
    """ID の形式の検査。"""

    @pytest.mark.parametrize("value", ["1AbC_d-9", "0ABcdEFgh_ijkUk9PVA"])
    def test_accepts_drive_ids(self, value: str) -> None:
        """英数字・`_`・`-` の ID は通す。"""
        assert is_valid_drive_id(value)

    @pytest.mark.parametrize(
        "value", ["", "a/b", "..", "a?alt=media", "a b", "a%2F", None, 123, "x" * 257]
    )
    def test_rejects_others(self, value) -> None:
        """URL のパスを変えうる文字・空・文字列以外・長すぎる値は拒否する。"""
        assert not is_valid_drive_id(value)


class TestAccessResolver:
    """親を辿ったホワイトリスト判定。"""

    async def test_empty_whitelist_denies_without_api_calls(self) -> None:
        """ホワイトリストが両方空なら API を呼ばずに拒否する。"""
        client = _make_client({})
        resolver = AccessResolver(client, [], [])

        assert resolver.is_empty
        assert await resolver.resolve("file") is Access.NONE
        client.get_metadata.assert_not_awaited()

    async def test_direct_child_of_read_only_folder(self) -> None:
        """読み取り専用フォルダの直下は READ。"""
        resolver = AccessResolver(_make_client({}), ["ro"], [])

        assert await resolver.resolve("file", ["ro"]) is Access.READ

    async def test_deep_descendant_of_writable_folder(self) -> None:
        """書き込み可能フォルダの深い配下も WRITE（親を辿る）。"""
        client = _make_client({"sub2": ["sub1"], "sub1": ["rw"]})
        resolver = AccessResolver(client, [], ["rw"])

        assert await resolver.resolve("file", ["sub2"]) is Access.WRITE

    async def test_fetches_parents_when_not_given(self) -> None:
        """親を渡さなければファイル自身のメタデータから親を取る。"""
        client = _make_client({"file": ["rw"]})
        resolver = AccessResolver(client, [], ["rw"])

        assert await resolver.resolve("file") is Access.WRITE

    async def test_whitelisted_folder_itself(self) -> None:
        """ホワイトリストのフォルダそのもの（作成先の判定）も対象になる。"""
        resolver = AccessResolver(_make_client({}), [], ["rw"])

        assert await resolver.resolve("rw") is Access.WRITE

    async def test_outside_whitelist(self) -> None:
        """ルートまで辿ってもホワイトリストに当たらなければ NONE。"""
        client = _make_client({"sub": ["root"], "root": []})
        resolver = AccessResolver(client, ["ro"], ["rw"])

        assert await resolver.resolve("file", ["sub"]) is Access.NONE

    async def test_nearest_whitelisted_folder_wins(self) -> None:
        """書き込み可能フォルダの中の読み取り専用フォルダの配下は READ（近い方が勝つ）。"""
        client = _make_client({"ro": ["rw"]})
        resolver = AccessResolver(client, ["ro"], ["rw"])

        assert await resolver.resolve("file", ["ro"]) is Access.READ

    async def test_writable_inside_read_only(self) -> None:
        """読み取り専用フォルダの中の書き込み可能フォルダの配下は WRITE。"""
        client = _make_client({"rw": ["ro"]})
        resolver = AccessResolver(client, ["ro"], ["rw"])

        assert await resolver.resolve("file", ["rw"]) is Access.WRITE

    async def test_same_level_hit_prefers_read_only(self) -> None:
        """同じ段で両方に当たったら安全側（読み取り専用）に倒す。"""
        resolver = AccessResolver(_make_client({}), ["ro"], ["rw"])

        assert await resolver.resolve("file", ["rw", "ro"]) is Access.READ

    async def test_shared_drive_id(self) -> None:
        """共有ドライブはドライブの ID（最上位のフォルダの親）を書けば対象になる。"""
        client = _make_client({"top-folder": ["shared-drive-id"], "shared-drive-id": []})
        resolver = AccessResolver(client, ["shared-drive-id"], [])

        assert await resolver.resolve("file", ["top-folder"]) is Access.READ

    @pytest.mark.parametrize("status", [403, 404])
    async def test_invisible_parent_stops_traversal(self, status: int) -> None:
        """見えない親フォルダ（403 / 404）で辿るのをやめ、NONE にする。"""
        client = _make_client({}, errors={"hidden": status})
        resolver = AccessResolver(client, ["ro"], [])

        assert await resolver.resolve("file", ["hidden"]) is Access.NONE

    async def test_other_api_errors_propagate(self) -> None:
        """それ以外の API エラー（レート制限など）は呼び出し元へ伝える。"""
        client = _make_client({}, errors={"sub": 429})
        resolver = AccessResolver(client, ["ro"], [])

        with pytest.raises(aiohttp.ClientResponseError):
            await resolver.resolve("file", ["sub"])

    async def test_cycles_terminate(self) -> None:
        """親の循環があっても止まる。"""
        client = _make_client({"a": ["b"], "b": ["a"]})
        resolver = AccessResolver(client, ["ro"], [])

        assert await resolver.resolve("file", ["a"]) is Access.NONE

    async def test_gives_up_after_max_depth(self) -> None:
        """階層が上限より深ければ NONE にする。"""
        tree = {f"f{i}": [f"f{i + 1}"] for i in range(MAX_ANCESTOR_DEPTH + 5)}
        resolver = AccessResolver(_make_client(tree), [f"f{MAX_ANCESTOR_DEPTH + 5}"], [])

        assert await resolver.resolve("f0") is Access.NONE

    async def test_caches_parent_lookups(self) -> None:
        """同じ祖先は 1 回だけ問い合わせる（検索結果の絞り込みで使い回す）。"""
        client = _make_client({"sub": ["rw"]})
        resolver = AccessResolver(client, [], ["rw"])

        await resolver.resolve("file1", ["sub"])
        await resolver.resolve("file2", ["sub"])

        assert [c.args[0] for c in client.get_metadata.await_args_list] == ["sub"]
