"""共有 HTTP サーバー向け Bearer トークンを発行する CLI。

共有 HTTP サーバー（``lilla_core.handlers.http_server``）の Bearer 認証と、拡張が
``deferred`` で載せたルート（WebSocket の接続後認証など）が照合するトークンを発行する。
保存と照合は ``lilla_core.repository.client_token_repository`` が担い、この CLI は
発行対象名の既定値・上書き確認・平文の表示だけを持つ。実行例::

    python -m lilla_core.scripts.issue_client_token
    docker exec -it <コンテナ名> python -m lilla_core.scripts.issue_client_token

設定（``CONFIG_ROOT`` の ``lilla.yaml`` / ``.env``）は bot 本体と同じ方法で読むため、
bot と同じ環境変数のもとで実行すること。

生成した平文トークンは **この実行時にしか表示されない**。DB には SHA-256 ハッシュ
だけを保存するため、控え忘れた場合は再発行するしかない。同じ発行対象
（``--label``）のトークンが既にある場合は、上書き確認プロンプトを出したうえで
古いレコードを削除して置き換える（1 label = 1 token。古いトークンの実質的な失効）。
"""
from __future__ import annotations

import argparse
import asyncio
import secrets
import sys

from lilla_core.repository.client_token_repository import (
    DEFAULT_LABEL,
    get_client_token_repo,
    hash_client_token,
)

#: 生成するトークンのバイト数（token_urlsafe に渡す値）
TOKEN_NBYTES = 32


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """コマンドライン引数を解析する。

    Args:
        argv: 引数リスト。None の場合は ``sys.argv`` を使う。

    Returns:
        解析結果の Namespace（``label`` / ``yes``）。
    """
    parser = argparse.ArgumentParser(
        prog="python -m lilla_core.scripts.issue_client_token",
        description="共有 HTTP サーバー向けの Bearer トークンを発行する",
    )
    parser.add_argument(
        "--label",
        default=DEFAULT_LABEL,
        help=f"発行対象の識別名（デフォルト: {DEFAULT_LABEL}）",
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="既存トークンの上書き確認プロンプトを省略する",
    )
    return parser.parse_args(argv)


def confirm_overwrite(label: str) -> bool:
    """既存トークンを上書きしてよいかを対話的に確認する。

    Args:
        label: 発行対象の識別名。

    Returns:
        上書きしてよければ True。

    Notes:
        標準入力が閉じている（``docker exec`` に ``-i`` を付け忘れた等）場合は
        誤って上書きしないよう False を返す。
    """
    prompt = f"'{label}' のトークンは既に発行済みです。上書きしますか？ [y/N]: "
    try:
        answer = input(prompt)
    except EOFError:
        print("標準入力が利用できないため中止しました（docker exec には -it を付けてください）。")
        return False
    return answer.strip().lower() in ("y", "yes")


async def issue_token(label: str, assume_yes: bool) -> str | None:
    """トークンを発行して DB に保存する。

    Args:
        label: 発行対象の識別名。
        assume_yes: True の場合、既存トークンの上書き確認をスキップする。

    Returns:
        発行した平文トークン。上書きを拒否した場合は None。
    """
    repo = get_client_token_repo()
    await repo.ensure_indexes()

    if await repo.exists_by_label(label) and not assume_yes:
        if not confirm_overwrite(label):
            return None

    token = secrets.token_urlsafe(TOKEN_NBYTES)
    # 古いレコードを消してから新しいトークンを保存する（1 label = 1 token）
    await repo.replace(hash_client_token(token), label)
    return token


async def main(argv: list[str] | None = None) -> int:
    """CLI のエントリポイント。

    Args:
        argv: コマンドライン引数。None の場合は ``sys.argv`` を使う。

    Returns:
        プロセスの終了コード（0: 成功 / 1: 中止）。
    """
    args = parse_args(argv)

    token = await issue_token(args.label, args.yes)
    if token is None:
        print("中止しました。既存のトークンはそのままです。")
        return 1

    print(f"新しいトークンを発行しました。{args.label} の設定に反映してください：")
    print()
    print(f"  {token}")
    print()
    print("このトークンは一度しか表示されません。")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
