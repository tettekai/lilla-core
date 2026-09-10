"""HTTP ハンドラー共通のリクエスト入力解析ユーティリティ。

aiohttp の各ハンドラーで繰り返し書かれていた以下の定型処理を一元化する。

- 整数クエリパラメータをデフォルト値付きで取得し、下限・上限で丸め、
  ``int()`` 失敗時はデフォルトにフォールバックする（``parse_int_param``）。
- リクエストボディを JSON として解析し、成否を明示的に返す
  （``parse_json_body``）。
- パス変数を ``ObjectId`` に変換し、不正な形式なら ``None`` を返す
  （``parse_object_id``）。

いずれのヘルパーも aiohttp のレスポンス生成は行わず、解析結果のみを返す。
400 応答の生成は呼び出し側（各ハンドラー）に委ねることで、テスト時の
``web`` モック差し替えがハンドラーモジュール側だけで完結するようにしている。
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId


def parse_int_param(
    params: Mapping[str, str],
    key: str,
    default: int,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    """クエリパラメータを整数として取得し、範囲で丸めて返す。

    ``key`` が存在しない、または整数として解釈できない場合は ``default`` を
    そのまま返す（``default`` には範囲丸めを適用しない）。整数として解釈できた
    場合のみ ``minimum`` / ``maximum`` によるクランプを行う。

    Args:
        params: クエリパラメータのマッピング（``request.rel_url.query`` など）。
        key: 取得するパラメータ名。
        default: 値が無い・不正な場合に返すデフォルト値。
        minimum: 下限値。指定時は結果をこの値以上に丸める。
        maximum: 上限値。指定時は結果をこの値以下に丸める。

    Returns:
        丸め済みの整数値（解析失敗時は ``default``）。
    """
    try:
        value = int(params.get(key, default))
    except (TypeError, ValueError):
        return default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


async def parse_json_body(request) -> tuple[Any | None, bool]:
    """リクエストボディを JSON として解析する。

    解析に成功した場合は ``(body, True)`` を、``request.json()`` が例外を
    送出した場合は ``(None, False)`` を返す。呼び出し側は成否フラグが
    ``False`` のときに 400 応答を返す想定。

    Args:
        request: ``json()`` コルーチンを持つ aiohttp のリクエストオブジェクト。

    Returns:
        ``(解析済みボディ, True)`` または ``(None, False)`` のタプル。
    """
    try:
        return await request.json(), True
    except Exception:
        return None, False


def parse_object_id(value: str) -> ObjectId | None:
    """文字列を MongoDB の ``ObjectId`` に変換する。

    パス変数など str のパス識別子を ``ObjectId`` に変換する用途を想定する。
    変換に成功した場合は ``ObjectId`` を、``value`` が ``ObjectId`` として
    不正な形式（長さ・文字種が不正な文字列など）の場合は ``None`` を返す。
    呼び出し側は ``None`` のときに 400 応答を返す想定。

    Args:
        value: ``ObjectId`` に変換する文字列（パス変数など）。

    Returns:
        変換済みの ``ObjectId``。不正な形式の場合は ``None``。
    """
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        return None
