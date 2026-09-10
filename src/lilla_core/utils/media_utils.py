from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

# メディア配信エンドポイントのパス（拡張側が実装する配信サーバーの GET ルートと対応）。
MEDIA_FILES_PATH = "/media/files"


class MediaRef(NamedTuple):
    """メディアファイルの参照情報。

    Attributes
    ----------
    media_id : str
        拡張子を除いたメディア ID（例: gentle_smile_01）。
    url : str
        メディアファイルの完全な配信 URL。
    """

    media_id: str
    url: str


def build_media_ref(base_url: str, filename: str) -> MediaRef:
    """ファイル名からメディア ID と配信 URL を組み立てて返す。

    メディア ID はファイル名から拡張子を除いたもの、URL は
    ``{base_url}/media/files/{filename}`` 形式となる。メディア情報を
    保存・通知する複数の箇所で同一の組み立てを共通利用する。

    Parameters
    ----------
    base_url : str
        メディアサーバーのベース URL（末尾スラッシュなし）。
    filename : str
        メディアファイル名（例: gentle_smile_01.jpg）。

    Returns
    -------
    MediaRef
        media_id と url を持つ参照情報。
    """
    media_id = Path(filename).stem
    url = f"{base_url}{MEDIA_FILES_PATH}/{filename}"
    return MediaRef(media_id=media_id, url=url)
