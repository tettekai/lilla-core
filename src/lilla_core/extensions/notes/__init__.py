"""ディスク上の bare Git リポジトリに保存する汎用のノート置き場（標準機能の `notes` 拡張）。

コアの `load_extensions()` が `LILLA_EXTENSIONS` によらず常に読み込む（`BUILTIN_EXTENSION_MODULES`）。
パッケージは公式拡張パックと同じ場所にあるが、並べたときだけ有効になる拡張ではない。

設定なしで使えるテキストの置き場所で、中身は「パス + 本文 + 更新日時」を持つ仮想の
ファイルシステムで、1 回の書き込みが 1 コミットになる。LLM からは `files` 拡張と同じ形のツール（`llm_notes_list` /
`llm_notes_read` / `llm_notes_write` / `llm_notes_search`。YAML を置いたものだけ有効）で
読み書きし、人は観測用ダッシュボードの「ノート」画面で閲覧・検索・編集する。コードからは
コアの `FileStorage`（`get_file_storage("notes")`）として使える。共有 HTTP サーバーの
`/notes.git` から Git で clone / fetch / push もできる（push には同じ守りをかける）。

サブモジュール（`service` / `repository` / `storage` / `dashboard_api`）はここから
トップレベルで import しない（`extension` の生成がモジュール import 時に走るため）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from lilla_core.core.extension import (
    DashboardPage,
    DashboardRoute,
    Extension,
    HttpRoute,
    SetupContext,
)

#: 1 ファイルの本文の上限（文字数）の既定値。
DEFAULT_MAX_CONTENT_CHARS = 1_000_000
#: 1 ファイルの本文の上限の最大値。
MAX_CONTENT_CHARS_LIMIT = 3_000_000
#: `llm_notes_read` で一度に返す本文の上限（文字数）の既定値。
DEFAULT_MAX_READ_CHARS = 100_000
#: リポジトリのディレクトリの既定値（コンテナ内のパス。ホスト側はボリュームでマウントする）。
DEFAULT_REPO_PATH = Path("/app/data/notes.git")
#: 大きく消える変更を通知する、1 回の書き込みで消える行数の既定値。
DEFAULT_DELETION_ALERT_LINES = 50
#: Git に移して意味がなくなった項目と、その案内。
_REMOVED_KEYS = {
    "history_retention_days": (
        "extensions.notes.history_retention_days was removed: notes history is now kept "
        "as Git commits in extensions.notes.repo_path. Remove the key from lilla.yaml."
    ),
}

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
    #: ノートを保存する bare Git リポジトリのディレクトリ（絶対パス）。無ければ起動時に作る。
    repo_path: Path = DEFAULT_REPO_PATH
    #: 1 回の書き込みで消える行がこの数以上ならエラー通知チャンネルへ知らせる（書き込みは止めない）。
    deletion_alert_lines: int = Field(default=DEFAULT_DELETION_ALERT_LINES, ge=1)

    @model_validator(mode="before")
    @classmethod
    def _reject_removed_keys(cls, data: Any) -> Any:
        """廃止した項目が残っていたら、移行先を示して落とす（黙って捨てない）。"""
        if isinstance(data, dict):
            for key, message in _REMOVED_KEYS.items():
                if key in data:
                    raise ValueError(message)
        return data

    @field_validator("repo_path")
    @classmethod
    def _check_repo_path(cls, value: Path) -> Path:
        """リポジトリのパスは絶対パスだけを受け付ける。"""
        if not value.is_absolute():
            raise ValueError("extensions.notes.repo_path must be an absolute path")
        return value


class NotesExtension(Extension):
    """ノートの設定・リポジトリ・ツール・保存先・ダッシュボード画面を申告する拡張。"""

    name = "notes"

    def config_model(self) -> type[BaseModel]:
        """YAML セクションのモデルを返す（節名は `extensions.notes`）。"""
        return NotesConfig

    async def setup(self, ctx: SetupContext) -> None:
        """リポジトリを開く（無ければ `main` を持つ bare リポジトリを作る）。

        notes は標準機能で常に読み込まれるため、開けなくても（書き込めないパス・Git 以外の
        ディレクトリなど）ボット全体は止めず、ERROR ログを出して起動を続ける。ノートの
        読み書きはそのつど開き直し、開けなければその操作だけが失敗する。
        """
        import logging

        from lilla_core.extensions.notes.repository import get_notes_repo

        try:
            await get_notes_repo().init_repository()
        except Exception:
            logging.getLogger(__name__).exception(
                "Failed to open notes repository; notes stay unavailable until it can be opened"
            )

    def tool_roots(self) -> list[Path]:
        """LLM ツール（このパッケージの `tools/`）の探索先を返す。

        ツール YAML は同梱しない（利用者が `${CONFIG_ROOT}/tools` に置いたものだけ有効）。
        """
        return [_PACKAGE_DIR / "tools"]

    def locale_dirs(self) -> list[Path]:
        """文言カタログ（このパッケージの `locales/`）を返す。"""
        return [_PACKAGE_DIR / "locales"]

    def default_prompt(self) -> dict[str, str] | None:
        """ノートの書ける場所を LLM へ伝えるデフォルトのプロンプトを返す（`ja` / `en`）。

        本文は書き込みの許可リスト（`paths.writable_top_level_dirs()`。notes 自身の `user/` と、
        ロード済みの拡張が `notes_directories()` で宣言したディレクトリ）から組み立てる。
        どのディレクトリが書けるかと用途だけを伝え、具体的なファイルのパスやパスの形には
        触れない（パスの形は宣言した拡張が自分の `default_prompt()` で伝える）。
        """
        from lilla_core.extensions.notes.paths import writable_top_level_dirs

        dirs = writable_top_level_dirs()
        ja_lines = "\n".join(f"- `{name}/`: {usage['ja']}" for name, usage in dirs.items())
        en_lines = "\n".join(f"- `{name}/`: {usage['en']}" for name, usage in dirs.items())
        return {
            "ja": (
                "## ノート（notes）の書き込み先\n"
                "notes のルート直下にはファイルもディレクトリも作れません。"
                "書き込み（notes_write）は、次のトップレベルディレクトリの配下だけにしてください"
                "（それ以外のパスは失敗します）。読み取り・一覧・検索はどこでもできます。\n"
                f"{ja_lines}"
            ),
            "en": (
                "## Where to write notes\n"
                "You cannot create files or directories directly under the notes root. "
                "Write (notes_write) only under the following top-level directories "
                "(any other path fails). Reading, listing and searching work anywhere.\n"
                f"{en_lines}"
            ),
        }

    def notes_directories(self) -> dict[str, dict[str, str]]:
        """notes 自身が持つ書き込めるトップレベルディレクトリ（`user/`）を宣言する。

        ほかの拡張の宣言と同じ経路で集めるため、拡張が `user` を宣言すると重複として
        ロード時に落ちる。
        """
        from lilla_core.extensions.notes.paths import CORE_WRITABLE_TOP_LEVEL_DIRS

        return {name: dict(usage) for name, usage in CORE_WRITABLE_TOP_LEVEL_DIRS.items()}

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

    def http_routes(self) -> list[HttpRoute]:
        """bare リポジトリの Git smart HTTP（`/notes.git`。clone / fetch / push）を返す。

        認証は共有 HTTP サーバーの既定の Bearer。有効 / 無効の設定は持たず、共有 HTTP
        サーバーが LAN の中だけで listen している前提で常に載せる（`docs/ja/notes.md`）。
        """
        from lilla_core.extensions.notes.git_http import build_routes

        return build_routes()


extension = NotesExtension()
