"""Google Drive / Google Sheets の設定・クライアント・LLM ツールをまとめた拡張（公式拡張パックの 1 つ）。

`LILLA_EXTENSIONS` には `lilla_core.extensions.google_drive` として、
`lilla_core.extensions.google_oauth`（`GoogleOAuthClient` の提供元）より後に並べて
読み込む。OAuth2 のトークン処理と `extensions.google_oauth` は `google-oauth` 拡張の
担当で、この拡張が申告するのは Drive 固有の `extensions.google_drive`
（アクセスを許すフォルダのホワイトリストと書き込みスコープ。節名はコアが `name` から導く）だけになる。

この拡張が持つもの:
- `extensions.google_drive` セクションのモデル（`GoogleDriveConfig`）とその申告
- Drive / Sheets API クライアント（`client.py`）
- ホワイトリストの判定（`access.py`）とツール共通の処理（`tool_support.py`）
- テキストファイルの検索・取得・書き込みと、Sheets の範囲取得の LLM ツール
  （`tools/`。`tool_roots()` でコアの探索ルートへ足す）

扱うのはホワイトリストのフォルダ配下にある `text/*` のファイルと、Google Sheets の
スプレッドシート（読み取りのみ）だけで、それ以外は存在しないものとして扱う。
ツール YAML は利用者側の `${CONFIG_ROOT}/tools` にあるものをそのまま使うため、
`tool_config_roots()` での同梱はしない。

モジュールの import 時に `extension` インスタンスを生成するため、ここでは
サブモジュール（`client` など）を import しない（`google-calendar` と同じ切り方）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from lilla_core.core.extension import Extension

#: `get_text` などで LLM へ返す本文の既定の上限（文字数）。
DEFAULT_MAX_READ_CHARS = 100_000


class GoogleDriveConfig(BaseModel):
    """lilla.yaml の `extensions.google_drive` セクション。

    OAuth2 クライアントの設定（`extensions.google_oauth`）は `google_oauth` 拡張が申告するため、
    こちらは Drive 固有の設定だけを持つ。フォルダの ID はホワイトリストで、指定した
    フォルダとその配下すべてが対象になる（共有ドライブはドライブの ID を書けば対象になる）。
    両方とも空ならすべてのアクセスを拒否する。
    """

    # 安全のためのホワイトリストなので、綴り間違いの項目名を黙って無視しない。
    model_config = ConfigDict(extra="forbid")

    #: 読み取りだけを許すフォルダの ID。
    read_only_folders: list[str] = []
    #: 読み取りと書き込み（テキストファイルの作成・上書き）を許すフォルダの ID。
    writable_folders: list[str] = []
    #: 書き込みに使う OAuth スコープ。`file` は `drive.file`（この拡張が作ったファイルしか
    #: 上書きできない）、`full` は `drive`（書き込み可能フォルダ配下の既存ファイルも上書き
    #: できるが、範囲の制限はこの拡張のホワイトリストだけになる）。`writable_folders` が
    #: 空なら書き込みのスコープ自体を要求しない。
    write_scope: Literal["file", "full"] = "file"
    #: テキストファイルの本文・Sheets の値を LLM へ返すときの上限（文字数）。超えた分は切り詰める。
    max_read_chars: int = Field(default=DEFAULT_MAX_READ_CHARS, gt=0)

    @field_validator("read_only_folders", "writable_folders")
    @classmethod
    def _reject_blank_ids(cls, value: list[str]) -> list[str]:
        """空文字・空白だけの ID を拒否し、前後の空白を取り除く。"""
        stripped = [item.strip() for item in value]
        if any(not item for item in stripped):
            raise ValueError("folder IDs must not be blank")
        return stripped

    @model_validator(mode="after")
    def _reject_ids_in_both_lists(self) -> "GoogleDriveConfig":
        """同じフォルダ ID が読み取り専用と書き込み可能の両方にあれば拒否する。"""
        if set(self.read_only_folders) & set(self.writable_folders):
            raise ValueError(
                "a folder ID must not appear in both read_only_folders and writable_folders"
            )
        return self


class GoogleDriveExtension(Extension):
    """Google Drive / Google Sheets の設定とツールを申告する拡張。"""

    name = "google-drive"

    #: Drive クライアント（`client.py`）は `google-oauth` 拡張の
    #: `GoogleOAuthClient` を継承し、その `from_config()` が
    #: `cfg.extensions.google_oauth` を読む。その拡張がロード済みで、かつ
    #: `LILLA_EXTENSIONS` で自分より前に並んでいることをコアに検証させる。
    requires = ("google-oauth",)

    def config_model(self) -> type[BaseModel]:
        """Drive 用の YAML セクションのモデルを返す（節名は `extensions.google_drive`）。"""
        return GoogleDriveConfig

    def required_env_fields(self) -> list[str]:
        """自分では提供しないが、自分のコードが読む秘匿フィールド名を返す。

        クライアントの組み立て（`GoogleOAuthClient.from_config()`）で
        `cfg.env.google_client_secret` を経由するため申告する。
        """
        return ["google_client_secret"]

    def tool_roots(self) -> list[Path]:
        """Drive / Sheets の LLM ツール（このパッケージの `tools/`）の探索先を返す。

        利用者の `paths.tool_root` 配下には置かないため、コアの探索ルートへ
        この拡張から足す（YAML の `type: llm_google_drive_get_text` のようにファイル名で
        解決される）。ツール YAML そのものは利用者の `${CONFIG_ROOT}/tools` にあるものを使う
        （同梱しない）。
        """
        return [Path(__file__).resolve().parent / "tools"]


extension = GoogleDriveExtension()
