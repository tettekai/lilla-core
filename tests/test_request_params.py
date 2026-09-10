"""request_params.py のテスト。

クエリパラメータの整数解析ユーティリティ ``parse_int_param``、
リクエストボディ JSON 解析 ``parse_json_body``、``ObjectId`` 変換
``parse_object_id`` の挙動を検証する。
"""
from __future__ import annotations

from bson import ObjectId

from lilla_core.handlers.request_params import (
    parse_int_param,
    parse_json_body,
    parse_object_id,
)


class _FakeRequest:
    """``json()`` コルーチンだけを持つ最小限のリクエストスタブ。"""

    def __init__(self, *, body=None, raise_exc: Exception | None = None) -> None:
        self._body = body
        self._raise_exc = raise_exc

    async def json(self):
        """成功時は body を返し、raise_exc 指定時はそれを送出する。"""
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._body


class TestParseIntParam:
    def test_parses_valid_value(self) -> None:
        """有効な整数文字列をそのまま整数として返す。"""
        assert parse_int_param({"page": "5"}, "page", 1) == 5

    def test_returns_default_when_missing(self) -> None:
        """キーが無い場合はデフォルトを返す。"""
        assert parse_int_param({}, "page", 1) == 1

    def test_returns_default_when_not_int(self) -> None:
        """整数として解釈できない場合はデフォルトを返す。"""
        assert parse_int_param({"page": "abc"}, "page", 1) == 1

    def test_applies_minimum(self) -> None:
        """下限未満の値は下限に丸める。"""
        assert parse_int_param({"page": "0"}, "page", 1, minimum=1) == 1

    def test_applies_maximum(self) -> None:
        """上限超えの値は上限に丸める。"""
        assert parse_int_param({"page_size": "999"}, "page_size", 50, maximum=200) == 200

    def test_applies_both_bounds(self) -> None:
        """下限・上限の両方を同時に適用する。"""
        assert parse_int_param({"n": "500"}, "n", 50, minimum=1, maximum=200) == 200
        assert parse_int_param({"n": "-5"}, "n", 50, minimum=1, maximum=200) == 1

    def test_value_within_bounds_unchanged(self) -> None:
        """範囲内の値は丸めずにそのまま返す。"""
        assert parse_int_param({"n": "100"}, "n", 50, minimum=1, maximum=200) == 100

    def test_default_not_clamped(self) -> None:
        """解析失敗時のデフォルトには範囲丸めを適用しない。"""
        assert parse_int_param({"n": "abc"}, "n", 0, minimum=1) == 0

    def test_negative_offset_floored_to_zero(self) -> None:
        """offset 相当の下限 0 で負値が 0 に丸められる。"""
        assert parse_int_param({"offset": "-10"}, "offset", 0, minimum=0) == 0


class TestParseJsonBody:
    async def test_returns_body_and_true_on_success(self) -> None:
        """解析に成功した場合は (body, True) を返す。"""
        request = _FakeRequest(body={"content": "hello"})
        body, ok = await parse_json_body(request)
        assert ok is True
        assert body == {"content": "hello"}

    async def test_returns_none_and_false_on_error(self) -> None:
        """json() が例外を送出した場合は (None, False) を返す。"""
        request = _FakeRequest(raise_exc=ValueError("invalid json"))
        body, ok = await parse_json_body(request)
        assert ok is False
        assert body is None

    async def test_preserves_falsy_body(self) -> None:
        """空 dict など falsy な本文でも成功として (body, True) を返す。"""
        request = _FakeRequest(body={})
        body, ok = await parse_json_body(request)
        assert ok is True
        assert body == {}


class TestParseObjectId:
    def test_returns_object_id_for_valid_string(self) -> None:
        """有効な 24 桁 16 進文字列は ObjectId に変換される。"""
        oid = ObjectId()
        result = parse_object_id(str(oid))
        assert result == oid

    def test_returns_none_for_invalid_format(self) -> None:
        """不正な形式の文字列は None を返す。"""
        assert parse_object_id("invalid-id") is None

    def test_returns_none_for_empty_string(self) -> None:
        """空文字列は None を返す。"""
        assert parse_object_id("") is None

    def test_returns_none_for_wrong_type(self) -> None:
        """int など bytes/str/None 以外の型を渡しても例外を送出せず None を返す。"""
        assert parse_object_id(123) is None
