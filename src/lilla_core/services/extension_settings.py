"""画面管理の拡張設定（`Extension.settings_model()`）の読み取り・検証・保存。

拡張の設定には 2 種類ある。

- `config_model()`: `lilla.yaml` の `extensions.<節名>`。起動時に `AppConfig` へ合成し、
  再起動するまで変わらない
- `settings_model()`: この画面管理の設定（設定の **本体**）。MongoDB の
  `extension_settings` に拡張ごとに 1 ドキュメントで保存し、ダッシュボードの
  Extensions 画面で編集する。`extension_overrides`（既定への **上書き**）とは別物

拡張は処理の始めに `await load_settings(name, Model)` を 1 回呼び、得たインスタンスを
その処理の間使い続ける（スナップショット）。呼ぶたびに DB を読むためキャッシュは無く、
保存の直後に始まる処理から新しい値になる。処理の途中で読み直さないこと（新旧の値が
混ざる）。どこを 1 つの処理とみなすかは拡張が決める。

失敗は「未保存」と取り違えないよう例外で分ける。

- 保存していない: 宣言された既定値（初期値）のモデルを返す（YAML などは読まない）
- DB から読めない: `SettingsUnavailableError`
- 保存値が今のモデルに合わない: `InvalidStoredSettingsError`（初期値で黙って置き換えない。
  画面で正しい値を保存し直すまで続く）

項目の `json_schema_extra` に `{"required_on_save": True}`（`REQUIRED_ON_SAVE_KEY`）を
書くと「保存時入力必須」になる。初期値の `None` や既存の保存値の `None` はそのまま
読めるが、画面から一式を保存するときだけ、その項目の省略・`null`・空文字・空白だけの
文字列を拒否する（画面には「未設定」の選択肢を出さない）。
"""
from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from lilla_core.core.extension import get_settings_models

_ModelT = TypeVar("_ModelT", bound=BaseModel)

REQUIRED_ON_SAVE_KEY = "required_on_save"
"""項目の `json_schema_extra` に書く「保存時入力必須」のキー。"""


class ExtensionSettingsError(Exception):
    """画面管理の拡張設定を読めないときの基底クラス。"""


class SettingsUnavailableError(ExtensionSettingsError):
    """設定を DB から読めなかった（未保存とは区別する）。"""


class InvalidStoredSettingsError(ExtensionSettingsError):
    """保存されている設定が今のモデルの検証を通らない。

    Attributes:
        errors: 項目ごとの理由（`{"field": ..., "message": ...}` のリスト）。
    """

    def __init__(self, message: str, errors: list[dict[str, str]]) -> None:
        super().__init__(message)
        self.errors = errors


class SettingsValidationError(ValueError):
    """保存しようとした設定が不正（リクエスト全体を拒否する）。

    Attributes:
        errors: 項目ごとの理由（`{"field": ..., "message": ...}` のリスト）。
    """

    def __init__(self, message: str, errors: list[dict[str, str]]) -> None:
        super().__init__(message)
        self.errors = errors


def get_settings_model(name: str) -> type[BaseModel] | None:
    """拡張が宣言した設定のモデルを返す。宣言していない・未ロードなら `None`。"""
    return get_settings_models().get(name)


def _declared_model(name: str, model: type[BaseModel] | None) -> type[BaseModel]:
    """宣言済みのモデルを返す（`model` を渡したら一致も確かめる）。

    Raises:
        ValueError: 拡張が設定を宣言していない場合、または `model` が宣言と異なる場合。
    """
    declared = get_settings_model(name)
    if declared is None:
        raise ValueError(f"Extension '{name}' does not declare dashboard-managed settings")
    if model is not None and model is not declared:
        raise ValueError(
            f"Extension '{name}' settings model is {declared.__name__}, not {model.__name__}"
        )
    return declared


def _format_errors(error: ValidationError) -> list[dict[str, str]]:
    """pydantic の検証エラーを「項目 -> 理由」のリストにする。"""
    return [
        {"field": ".".join(str(part) for part in item["loc"]), "message": item["msg"]}
        for item in error.errors()
    ]


def _field_extra(field: Any) -> dict:
    """項目の `json_schema_extra`（dict でなければ空）を返す。"""
    extra = field.json_schema_extra
    return extra if isinstance(extra, dict) else {}


def is_required_on_save(field: Any) -> bool:
    """項目が「保存時入力必須」（`json_schema_extra` の `required_on_save`）を宣言しているか。"""
    return _field_extra(field).get(REQUIRED_ON_SAVE_KEY) is True


def _required_on_save_errors(model: type[BaseModel], values: dict) -> list[dict[str, str]]:
    """保存時入力必須の項目の省略・`null`・空や空白だけの文字列をエラーにする。"""
    errors = []
    for name, field in model.model_fields.items():
        if not is_required_on_save(field):
            continue
        value = values.get(name)
        if value is None or (isinstance(value, str) and not value.strip()):
            errors.append({"field": name, "message": "this setting is required"})
    return errors


def _validate(model: type[BaseModel], values: Any) -> tuple[BaseModel | None, list[dict[str, str]]]:
    """値の一式をモデルで検証する。未知のキーもエラーにする（モデルの `extra` 設定によらない）。"""
    if not isinstance(values, dict):
        return None, [{"field": "", "message": "settings must be an object"}]
    unknown = sorted(str(key) for key in values if key not in model.model_fields)
    if unknown:
        return None, [{"field": key, "message": "unknown setting"} for key in unknown]
    try:
        return model.model_validate(values), []
    except ValidationError as e:
        return None, _format_errors(e)


async def _read_document(name: str) -> dict | None:
    """設定ドキュメントを読む。読めなければ `SettingsUnavailableError`。"""
    from lilla_core.repository.extension_settings_repository import get_extension_settings_repo

    try:
        return await get_extension_settings_repo().find_one(name)
    except Exception as e:
        raise SettingsUnavailableError(f"Failed to load settings of extension '{name}': {e}") from e


async def load_settings(name: str, model: type[_ModelT] | None = None) -> _ModelT:
    """拡張の画面管理の設定を型付きで読む（呼ぶたびに DB を読む）。

    1 つの処理の始めに 1 回呼び、戻り値をその処理の間使い続けること。

    Args:
        name: 拡張名（`Extension.name`）。
        model: 宣言したモデル。渡すと戻り値に静的な型が付き、宣言との一致も確かめる。

    Returns:
        保存済みの値のモデル。保存していなければ宣言された初期値のモデル。

    Raises:
        ValueError: 拡張が設定を宣言していない、または `model` が宣言と異なる場合。
        SettingsUnavailableError: DB から読めなかった場合。
        InvalidStoredSettingsError: 保存値が今のモデルの検証を通らない場合。
    """
    declared = _declared_model(name, model)
    doc = await _read_document(name)
    if doc is None:
        return declared()
    instance, errors = _validate(declared, doc.get("values"))
    if instance is None:
        raise InvalidStoredSettingsError(
            f"Stored settings of extension '{name}' are invalid; "
            "save valid settings on the dashboard Extensions page",
            errors,
        )
    return instance


def validate_settings(name: str, values: Any) -> BaseModel:
    """保存しようとする設定の一式を検証する。

    Args:
        name: 拡張名（設定を宣言していること）。
        values: 設定の一式（項目名 -> 値）。省略した項目は初期値になる（保存時入力必須の
            項目は省略・`null`・空や空白だけの文字列を拒否する）。

    Returns:
        検証済みのモデル。

    Raises:
        ValueError: 拡張が設定を宣言していない場合。
        SettingsValidationError: 一式がオブジェクトでない・未知の項目・型や検証規則の違反・
            保存時入力必須の項目が空。
    """
    declared = _declared_model(name, None)
    instance, errors = _validate(declared, values)
    if isinstance(values, dict) and not any(e["message"] == "unknown setting" for e in errors):
        # 保存時入力必須の違反を先に挙げ、同じ項目のモデル側のエラー（空白の拒否など）は重ねない
        required_errors = _required_on_save_errors(declared, values)
        if required_errors:
            reported = {e["field"] for e in required_errors}
            errors = required_errors + [e for e in errors if e["field"] not in reported]
            instance = None
    if instance is None:
        raise SettingsValidationError(f"Invalid settings for extension '{name}'", errors)
    return instance


async def save_settings(name: str, values: Any) -> BaseModel:
    """設定の一式を検証し、1 回の書き込みで保存する（不正なら何も保存しない）。

    Returns:
        保存したモデル。

    Raises:
        ValueError: 拡張が設定を宣言していない場合。
        SettingsValidationError: 一式が不正な場合（保存しない）。
        Exception: DB への保存に失敗した場合（リポジトリの例外をそのまま伝える）。
    """
    from lilla_core.repository.extension_settings_repository import get_extension_settings_repo

    instance = validate_settings(name, values)
    await get_extension_settings_repo().save(name, instance.model_dump(mode="json"))
    return instance


def describe_fields(model: type[BaseModel]) -> list[dict]:
    """画面の汎用編集欄に出す項目の説明を、モデルの定義順に返す。

    各要素は `name` / `title` / `description` / `type`（JSON Schema の型。`string` など）/
    `nullable`（未設定 `null` を取れるか）/ `required_on_save`（保存時に入力が必要か。
    真なら画面は「未設定」の選択肢を出さない）/ `multiline` / `default` / `pattern`。
    """
    schema = model.model_json_schema()
    properties = schema.get("properties", {})
    fields = []
    for name, field in model.model_fields.items():
        prop = properties.get(name, {})
        variants = prop.get("anyOf") or [prop]
        types = [variant.get("type") for variant in variants if isinstance(variant, dict)]
        nullable = "null" in types
        main = next(
            (variant for variant in variants if isinstance(variant, dict) and variant.get("type") not in (None, "null")),
            {},
        )
        extra = _field_extra(field)
        fields.append(
            {
                "name": name,
                "title": field.title or prop.get("title") or name,
                "description": field.description or "",
                "type": main.get("type", "string"),
                "nullable": nullable,
                "required_on_save": is_required_on_save(field),
                "multiline": bool(extra.get("multiline")),
                "default": model().model_dump(mode="json")[name],
                "pattern": main.get("pattern"),
            }
        )
    return fields


async def build_settings_detail(name: str) -> dict | None:
    """編集画面に出す設定の節を組み立てる。設定を宣言していない拡張なら `None`。

    `values` は保存済みの値（未保存なら初期値）、`stored` は保存済みか、`error` は
    読めなかったとき（`unavailable`）と保存値が不正なとき（`invalid`。`values` には
    直せるよう保存されている生の値を入れる）の理由。
    """
    model = get_settings_model(name)
    if model is None:
        return None
    detail: dict[str, Any] = {
        "fields": describe_fields(model),
        "values": None,
        "stored": False,
        "error": None,
        "updated_at": None,
    }
    try:
        doc = await _read_document(name)
    except SettingsUnavailableError:
        detail["error"] = {"kind": "unavailable", "message": "Failed to load settings", "errors": []}
        return detail
    if doc is None:
        detail["values"] = model().model_dump(mode="json")
        return detail
    detail["stored"] = True
    updated_at = doc.get("updated_at")
    detail["updated_at"] = updated_at.isoformat() if hasattr(updated_at, "isoformat") else None
    raw = doc.get("values")
    instance, errors = _validate(model, raw)
    if instance is None:
        detail["values"] = raw if isinstance(raw, dict) else {}
        detail["error"] = {
            "kind": "invalid",
            "message": "Stored settings are invalid for the current settings model",
            "errors": errors,
        }
    else:
        detail["values"] = instance.model_dump(mode="json")
    return detail
