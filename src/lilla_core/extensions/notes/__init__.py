"""MongoDB に保存する汎用のノート置き場（公式拡張パックの `notes` 拡張）。

設定なしで使えるテキストの置き場所で、中身は「パス + 本文 + 更新日時」を持つ仮想の
ファイルシステム。LLM からは `files` 拡張と同じ形のツール（`llm_notes_list` /
`llm_notes_read` / `llm_notes_write` / `llm_notes_search`。YAML を置いたものだけ有効）で
読み書きし、人は観測用ダッシュボードの「ノート」画面で閲覧・検索・編集する。コードからは
コアの `FileStorage`（`get_file_storage("notes")`）として使える。

サブモジュール（`service` / `repository` / `storage` / `dashboard_api`）はここから
トップレベルで import しない（`extension` の生成がモジュール import 時に走るため）。
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from lilla_core.core.extension import DashboardPage, DashboardRoute, Extension

#: 1 ファイルの本文の上限（文字数）の既定値。
DEFAULT_MAX_CONTENT_CHARS = 1_000_000
#: 1 ファイルの本文の上限の最大値（UTF-8 で 1 文字 4 バイトでも MongoDB の 16MB に収まる値）。
MAX_CONTENT_CHARS_LIMIT = 3_000_000
#: `llm_notes_read` で一度に返す本文の上限（文字数）の既定値。
DEFAULT_MAX_READ_CHARS = 100_000
#: 直前の版を残す日数の既定値。
DEFAULT_HISTORY_RETENTION_DAYS = 30

_PACKAGE_DIR = Path(__file__).resolve().parent


class NotesConfig(BaseModel):
    """`extensions.notes` セクション（全項目に既定値があり、節ごと省略できる）。"""

    model_config = ConfigDict(extra="forbid")

    #: 1 ファイルの本文の上限（文字数）。超える書き込みは断る。
    max_content_chars: int = Field(
        default=DEFAULT_MAX_CONTENT_CHARS, gt=0, le=MAX_CONTENT_CHARS_LIMIT
    )
    #: `llm_notes_read` で一度に返す本文の上限（文字数）。超えた分は `offset` で続きを読む。
    max_read_chars: int = Field(default=DEFAULT_MAX_READ_CHARS, gt=0)
    #: 上書き・削除の直前の版を残す日数。
    history_retention_days: int = Field(default=DEFAULT_HISTORY_RETENTION_DAYS, ge=1)


class NotesExtension(Extension):
    """ノートの設定・リポジトリ・ツール・保存先・ダッシュボード画面を申告する拡張。"""

    name = "notes"

    def config_model(self) -> type[BaseModel]:
        """YAML セクションのモデルを返す（節名は `extensions.notes`）。"""
        return NotesConfig

    def startup_repos(self) -> list[Callable[[], Any]]:
        """起動時にインデックスを作るリポジトリのファクトリを返す。"""
        from lilla_core.extensions.notes.repository import get_notes_repo

        return [get_notes_repo]

    def tool_roots(self) -> list[Path]:
        """LLM ツール（このパッケージの `tools/`）の探索先を返す。

        ツール YAML は同梱しない（利用者が `${CONFIG_ROOT}/tools` に置いたものだけ有効）。
        """
        return [_PACKAGE_DIR / "tools"]

    def locale_dirs(self) -> list[Path]:
        """文言カタログ（このパッケージの `locales/`）を返す。"""
        return [_PACKAGE_DIR / "locales"]

    def file_storages(self) -> dict:
        """ノートを保存先 `notes` として申告する。"""
        from lilla_core.extensions.notes.storage import NotesStorage

        return {"notes": NotesStorage()}

    def dashboard_page(self) -> DashboardPage | None:
        """ダッシュボードの常用ナビに「ノート」タブを足す。

        ダッシュボードの文言は日本語の直書き（`t()` のカタログ管理外）なのに合わせる。
        """
        return DashboardPage(label="ノート", group="main")

    def dashboard_static_dir(self) -> Path | None:
        """タブの JS モジュール（`page.js`）を置いたディレクトリを返す。"""
        return _PACKAGE_DIR / "dashboard"

    def dashboard_routes(self) -> list[DashboardRoute]:
        """ノート画面の API（`/api/notes` 配下。セッション認証の内側）を返す。"""
        from lilla_core.extensions.notes.dashboard_api import build_routes

        return build_routes()


extension = NotesExtension()
