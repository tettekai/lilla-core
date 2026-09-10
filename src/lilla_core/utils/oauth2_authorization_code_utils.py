"""OAuth2 認可コードフロー（Authorization Code Grant）専用の認証情報ユーティリティ。

認可コードフローのトークンレスポンスに由来する認証情報 dict
（``access_token`` / ``refresh_token`` / ``access_token_expires_at``）の形状を前提とし、
複数の外部サービス向け OAuth2 クライアント（実装は拡張側）で重複していた
「保存済みトークンが有効か」「リフレッシュトークンを持っているか」の
判定ロジックと、認証情報 dict の組み立て処理を共通化する。

クライアントクレデンシャルフローや、API キー方式の認証情報は対象外。
"""

from __future__ import annotations

import time


def is_access_token_valid(creds: dict | None) -> bool:
    """保存済みアクセストークンが有効期限内かどうかを判定する。

    access_token と access_token_expires_at の両方が存在し、かつ現在時刻が
    有効期限より前である場合に True を返す。creds が None の場合や必要な
    フィールドが欠けている場合は False を返す。

    Parameters
    ----------
    creds : dict | None
        認証情報。access_token / access_token_expires_at を含み得る。

    Returns
    -------
    bool
        アクセストークンがそのまま利用可能なら True
    """
    return bool(
        creds
        and creds.get("access_token")
        and creds.get("access_token_expires_at")
        and time.time() < creds["access_token_expires_at"]
    )


def build_token_credentials(
    token_data: dict,
    *,
    fallback_refresh_token: str | None = None,
) -> dict:
    """OAuth トークンレスポンスから保存用の認証情報 dict を組み立てる。

    複数の OAuth フロー（実装は拡張側）で重複していた
    「access_token / refresh_token / access_token_expires_at を組み立てる」
    処理を共通化する。expires_in（秒）を現在時刻に加算して有効期限の
    Unix タイムスタンプを算出する。

    refresh_token がレスポンスに含まれない（または空の）場合は
    fallback_refresh_token を採用する。トークン更新フローでは既存の
    refresh_token を渡すことで、再ログインなしに継続利用できる。

    Parameters
    ----------
    token_data : dict
        トークンエンドポイントのレスポンス本体。access_token を必須とし、
        refresh_token / expires_in を含み得る。
    fallback_refresh_token : str | None
        レスポンスに refresh_token が無い場合に使う代替値

    Returns
    -------
    dict
        access_token / refresh_token / access_token_expires_at を含む dict
    """
    return {
        "access_token": token_data["access_token"],
        "refresh_token": token_data.get("refresh_token") or fallback_refresh_token,
        "access_token_expires_at": int(time.time()) + token_data.get("expires_in", 0),
    }


def has_refresh_token(creds: dict | None) -> bool:
    """リフレッシュトークンが保存されているかどうかを判定する。

    creds が存在し refresh_token が空でない場合に True を返す。

    Parameters
    ----------
    creds : dict | None
        認証情報。refresh_token を含み得る。

    Returns
    -------
    bool
        リフレッシュトークンを持っているなら True
    """
    return bool(creds and creds.get("refresh_token"))
