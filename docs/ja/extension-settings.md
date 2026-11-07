# 画面で管理する拡張の設定

拡張の設定のうち、利用者がダッシュボードで編集し、再起動なしで使えるものを
`Extension.settings_model()` で宣言できます。値は MongoDB の `extension_settings` に
拡張ごとに 1 ドキュメントで保存し、観測用ダッシュボードの **Extensions** 画面
（管理メニュー、`#/admin/extensions`）で編集します。

## YAML の設定・上書きとの違い

| | 置き場 | 編集 | 反映 |
|---|---|---|---|
| `config_model()` | `lilla.yaml` の `extensions.<節名>`（[設定の合成](extension-config.md)） | ファイル | 起動時 |
| `settings_model()`（このページ） | MongoDB の `extension_settings`（設定の **本体**） | ダッシュボード | 次の処理から |
| `extension_overrides` | MongoDB（`default_prompt()` と同梱タスクの既定への **上書き**） | ダッシュボード | [プロンプト](extension-prompt.md) / [タスク](extension-task-override.md) |

- 1 つの項目の担当は YAML か画面の **どちらか一方だけ** です。優先順位やフォールバックで
  両方に書けるようにはしません。
- 同じ拡張の中で、YAML 担当の項目（`config_model()`）と画面担当の項目（`settings_model()`）を
  混ぜて持てます。

## 宣言

```python
from pydantic import BaseModel, ConfigDict, Field, field_validator

from lilla_core.core.extension import Extension


class DiarySettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    template: str | None = Field(
        default=None,
        title="テンプレート",
        description="日記の Markdown 本文",
        json_schema_extra={"multiline": True},
    )
    day_rollover: str = Field(default="04:00", title="日付の切り替わり", pattern=r"^\d{2}:\d{2}$")

    @field_validator("template")
    @classmethod
    def _not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("template must not be blank")
        return value


class DiaryExtension(Extension):
    name = "diary"

    def settings_model(self):
        return DiarySettings
```

- **全項目に既定値が必要です。** 既定値が「まだ保存していないときの初期値」になります。
  未設定を表すときは `str | None = None` のように `None` を既定にします。
- 既定値の無い項目がある・既定値がモデルの検証を通らない（宣言ミス）場合は、ロード時に落ちます。
- `config_model()` と同じ名前の項目を持つと、担当の二重宣言としてロード時に落ちます。
- `settings_model()` の項目を `lilla.yaml` の `extensions.<節名>` に書くと、移行先
  （ダッシュボードの Extensions 画面）を示して起動時に落ちます。YAML の値を黙って捨てたり
  使ったりしません。
- 画面の入力欄は項目の型から決まります。今対応しているのは文字列（`multiline` で複数行）・
  真偽値・数値と、それぞれの未設定（`None`）です。`title` / `description` は画面の見出しと説明に、
  `pattern` やバリデータは保存時の検証に使います。

## 読み取り

```python
from lilla_core.services.extension_settings import load_settings


async def execute(input, context):
    settings = await load_settings("diary", DiarySettings)  # 処理の始めに 1 回だけ
    ...  # この処理の間は settings を使い続ける
```

- `load_settings()` は呼ぶたびに MongoDB を読みます（キャッシュは持ちません）。保存の直後に
  始まる処理から新しい値になり、再起動は要りません。変更の監視やポーリングもしません。
- **1 つの処理の中では、最初に読んだインスタンス（スナップショット）を使い続けてください。**
  途中で読み直すと、保存をまたいで新旧の値が混ざります。保存しても実行中の処理は変わらず、
  止まりもしません。どこからどこまでを 1 つの処理とみなすか（ツール 1 回の実行・タスク 1 回の
  実行など）は拡張が決めます。`frozen=True` にしておくと、うっかり書き換えるのも防げます。
- `model` を渡すと戻り値に型が付き、宣言したモデルと違えば `ValueError` になります。

失敗は「未保存」と区別して例外で返します。

| 状態 | 結果 |
|------|------|
| 保存していない | 宣言した初期値のモデルを返す（YAML や外部のファイルは読まない） |
| MongoDB から読めない | `SettingsUnavailableError` |
| 保存値が今のモデルの検証を通らない（未知の項目を含む） | `InvalidStoredSettingsError`（`errors` に項目ごとの理由） |

どちらの例外も `ExtensionSettingsError` の派生です。保存値が不正なときに初期値へ黙って
置き換えたり、書き戻したりはしません。画面で正しい値を保存し直すまで失敗が続きます。

## 保存

ダッシュボードの Extensions 画面の「設定」欄で一式を保存します（API は
`PUT /api/admin/extensions/{name}/settings`、ボディは `{"values": {項目名: 値}}`）。

- 一式をモデルで検証してから、1 回の書き込みで置き換えます（`updated_at` は UTC）。1 か所でも
  不正（未知の項目・型の違い・検証規則の違反）なら何も保存せず、項目ごとの理由を返します。
- 省略した項目は初期値で保存されます。
- 保存に失敗したら成功と表示しません。

## モデルを変えるとき

汎用の移行の仕組みはありません。モデルを変えるときは次に気をつけてください。

- 項目を **足す** ときは既定値を付ける。保存済みの一式に無い項目は既定値になります。
- 項目を **消す・名前を変える・型や検証規則を厳しくする** と、保存済みの値が検証を通らなくなり、
  `InvalidStoredSettingsError` になります（画面にも「保存されている値が合わない」と出ます）。
  移行が要るなら、拡張のバージョンアップの手順として「画面で保存し直す」ことを利用者に案内します。
- YAML の項目を画面へ移すときは、`config_model()` から消して `settings_model()` へ足します。
  利用者の `lilla.yaml` に残っていると起動時に移行先を示して落ちるので、移行手順（値を控える →
  YAML から消す → 画面で保存する）を案内します。

## 関連ページ

- [拡張の基本](extensions.md)
- [設定の合成](extension-config.md)
- [観測用ダッシュボード](dashboard.md#extensions)
