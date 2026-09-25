"""Google Calendar の設定・クライアント・LLM ツールをまとめた公式パック。

`LILLA_EXTENSIONS` には `lilla_core.extensions.google_calendar` として、
`lilla_core.extensions.google_oauth`（`GoogleOAuthClient` の提供元）より後に並べて
読み込む。OAuth2 のトークン処理と `extensions.google_oauth` は `google-oauth` 拡張の
担当で、この拡張が申告するのはカレンダー固有の `extensions.google_calendar`
（取得・作成対象のカレンダー一覧。節名はコアが `name` から導く）だけになる。

この拡張が持つもの:
- `extensions.google_calendar` セクションのモデル（`GoogleCalendarConfig` /
  `CalendarEntryConfig`）とその申告
- Calendar API クライアント（`client.py`）
- 取得・作成の LLM ツール（`tools/`。`tool_roots()` でコアの探索ルートへ足す）

タイムゾーンはコア確定の `ui.timezone` をそのまま使い、この拡張は TZ 設定を
持たない。ツール YAML（`llm_calendar_get.yaml` / `llm_calendar_create.yaml`）は
利用者側の `${CONFIG_ROOT}/tools` にあるものをそのまま使うため、`tool_config_roots()`
での同梱はしない。

モジュールの import 時に `extension` インスタンスを生成するため、ここでは
サブモジュール（`client`）を import しない（`google-oauth` と同じ切り方）。
"""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from lilla_core.core.extension import Extension


class CalendarEntryConfig(BaseModel):
    """lilla.yaml の `extensions.google_calendar.calendars` の各要素（書き込み先カレンダーの申告）。"""

    id: str
    friendly_name: str


class GoogleCalendarConfig(BaseModel):
    """lilla.yaml の `extensions.google_calendar` セクション。

    OAuth2 クライアントの設定（`extensions.google_oauth`）は `google_oauth` 拡張が申告するため、
    こちらは Calendar 固有の設定だけを持つ。
    """

    #: 予定の取得・作成対象カレンダーの正。イベント作成は id への書き込みをここに
    #: 登録済みのものに限定し、任意の calendar_id への書き込みを許可しない。
    calendars: list[CalendarEntryConfig] = []


class GoogleCalendarExtension(Extension):
    """Google Calendar の設定とツールを申告する拡張。"""

    name = "google-calendar"

    #: Calendar クライアント（`client.py`）は `google-oauth` 拡張の
    #: `GoogleOAuthClient` を継承し、その `from_config()` が
    #: `cfg.extensions.google_oauth` を読む。その拡張がロード済みで、かつ
    #: `LILLA_EXTENSIONS` で自分より前に並んでいることをコアに検証させる
    #: （他の拡張の節を読む依存はコア契約上 `requires` で表す）。
    requires = ("google-oauth",)

    def config_model(self) -> type[BaseModel]:
        """カレンダー用の YAML セクションのモデルを返す（節名は `extensions.google_calendar`）。"""
        return GoogleCalendarConfig

    def required_env_fields(self) -> list[str]:
        """自分では提供しないが、自分のコードが読む秘匿フィールド名を返す。

        クライアントの組み立て（`GoogleOAuthClient.from_config()`）で
        `cfg.env.google_client_secret` を経由するため申告する。
        """
        return ["google_client_secret"]

    def tool_roots(self) -> list[Path]:
        """カレンダーの LLM ツール（このパッケージの `tools/`）の探索先を返す。

        利用者の `paths.tool_root` 配下には置かないため、コアの探索ルートへ
        この拡張から足す（YAML の `type: llm_calendar_get` のようにファイル名で解決される）。ツール YAML そのものは利用者の `${CONFIG_ROOT}/tools`
        にあるものを使う（同梱しない）。
        """
        return [Path(__file__).resolve().parent / "tools"]


extension = GoogleCalendarExtension()
