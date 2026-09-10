"""ファイル内容のハッシュ計算に関する共通ユーティリティ。

外部ストレージへの書き込みツール（拡張側の実装）は、Git のコミットハッシュでは
なく「ファイル内容そのもののハッシュ」で楽観的排他制御を行う想定がある。読み取り時と
書き込み検証時の双方が同じ計算式を使う必要があるため、その計算をここに一元化する。
"""
from __future__ import annotations

import hashlib


def normalize_newlines(content: str) -> str:
    """改行コードを ``\\n`` に正規化した文字列を返す。

    外部ストレージ経由で取得した内容とローカルで読んだ内容とで改行コードが
    異なる場合でもハッシュが揺れないようにするための前処理。

    Args:
        content: 正規化対象の文字列。

    Returns:
        ``\\r\\n`` および単独の ``\\r`` を ``\\n`` に置換した文字列。
    """
    return content.replace("\r\n", "\n").replace("\r", "\n")


def compute_content_hash(content: str) -> str:
    """ファイル内容から content_hash（SHA-256 の16進文字列）を計算する。

    改行コードを ``\\n`` に正規化してから UTF-8 でエンコードし、SHA-256 を計算する。

    Args:
        content: ハッシュ計算対象のファイル内容。

    Returns:
        SHA-256 ハッシュの16進小文字文字列。
    """
    normalized = normalize_newlines(content)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
