"""LLM ツールの標準結果辞書を組み立てる共通ヘルパー。

各 LLM ツールの ``execute()`` は
``success / tool_name / memory_entry / needs_auth / needs_auth_list / data / error``
の 7 キーからなる標準形式の辞書を返す。本モジュールはその辞書を
用途別（成功・失敗・再認証要求）に一元的に生成し、各ツールでの
手書き重複を排除する。
"""
from __future__ import annotations

from typing import Any


def tool_success(tool_name: str, memory_entry: str, data: Any) -> dict[str, Any]:
    """ツール成功時の標準結果辞書を返す。

    Args:
        tool_name: ツール名（LLM に渡す function 名）。
        memory_entry: 短期記憶に残す実行結果の要約。
        data: ツールが取得・生成したデータ本体。

    Returns:
        標準形式の結果辞書（success=True）。
    """
    return {
        "success": True,
        "tool_name": tool_name,
        "memory_entry": memory_entry,
        "needs_auth": False,
        "needs_auth_list": [],
        "data": data,
        "error": None,
    }


def tool_error(tool_name: str, memory_entry: str, error: str) -> dict[str, Any]:
    """ツール失敗時（認証不要）の標準結果辞書を返す。

    Args:
        tool_name: ツール名（LLM に渡す function 名）。
        memory_entry: 短期記憶に残す失敗内容の要約。
        error: エラー詳細メッセージ。

    Returns:
        標準形式の結果辞書（success=False, needs_auth=False）。
    """
    return {
        "success": False,
        "tool_name": tool_name,
        "memory_entry": memory_entry,
        "needs_auth": False,
        "needs_auth_list": [],
        "data": None,
        "error": error,
    }


def tool_needs_auth(
    tool_name: str,
    auth_url: str,
    message: str,
    auth_service: str = "google",
) -> dict[str, Any]:
    """再認証が必要なときの標準結果辞書を返す。

    Args:
        tool_name: ツール名（LLM に渡す function 名）。
        auth_url: ユーザーに提示する認証フローの URL。
        message: memory_entry / error の両方に使う案内メッセージ。
        auth_service: 認証サービス識別子（既定は ``"google"``）。

    Returns:
        標準形式の結果辞書（success=False, needs_auth=True）。
    """
    return {
        "success": False,
        "tool_name": tool_name,
        "memory_entry": message,
        "needs_auth": True,
        "needs_auth_list": [{"auth_service": auth_service, "auth_url": auth_url}],
        "data": None,
        "error": message,
    }


async def tool_reauth_required(
    client: Any,
    tool_name: str,
    message: str,
    auth_service: str = "google",
) -> dict[str, Any]:
    """再認証が必要なとき、認証フローを開始して標準結果辞書を返す。

    ``client.start_authentication()`` を呼び出して認証 URL を取得し、
    その URL で ``tool_needs_auth`` を組み立てて返す。各ツールで繰り返し
    書かれていた「認証 URL の取得 → needs_auth 辞書の生成」という二段の
    手順を一元化し、``ReauthenticationRequiredError`` の捕捉時に一行で
    再認証レスポンスを返せるようにする。

    Args:
        client: ``start_authentication()`` を持つ OAuth クライアント。
        tool_name: ツール名（LLM に渡す function 名）。
        message: memory_entry / error の両方に使う案内メッセージ。
        auth_service: 認証サービス識別子（既定は ``"google"``）。

    Returns:
        標準形式の結果辞書（success=False, needs_auth=True）。
    """
    auth_url = await client.start_authentication()
    return tool_needs_auth(tool_name, auth_url, message, auth_service)
