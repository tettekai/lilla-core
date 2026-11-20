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
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, TypeVar

from pydantic import BaseModel, TypeAdapter, ValidationError
from pydantic.fields import FieldInfo

from lilla_core.core.extension import get_settings_models, list_file_storages

logger = logging.getLogger(__name__)

_ModelT = TypeVar("_ModelT", bound=BaseModel)


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
        values: 設定の一式（項目名 -> 値）。省略した項目は初期値になる。

    Returns:
        検証済みのモデル。

    Raises:
        ValueError: 拡張が設定を宣言していない場合。
        SettingsValidationError: 一式がオブジェクトでない・未知の項目・型や検証規則の違反。
    """
    declared = _declared_model(name, None)
    instance, errors = _validate(declared, values)
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


#: 選択肢付きの配列をチェックボックス群で出す入力部品（`json_schema_extra` の `widget`）。
WIDGET_CHECKBOX_GROUP = "checkbox-group"

#: 汎用フォームが解釈する `widget` の一覧。
SUPPORTED_WIDGETS = frozenset({WIDGET_CHECKBOX_GROUP})

#: `widget` なしで汎用フォームが入力欄を出せる型（JSON Schema の型）。
_PLAIN_FIELD_TYPES = frozenset({"string", "integer", "number", "boolean"})

#: チェックボックス群の要素に使える型（入れ子は汎用フォームの対象外）。
_CHOICE_ITEM_TYPES = frozenset({"string", "integer", "number", "boolean"})


def _writable_file_storages() -> list[dict[str, Any]]:
    """書き込める（`read_only` が偽の）保存先を、登録順に選択肢として返す。"""
    return [
        {"value": name, "title": name}
        for name, storage in list_file_storages().items()
        if not storage.read_only
    ]


#: `choices_from` の名前 -> 選択肢を返す関数。設定詳細を組み立てるたびに呼ぶ。
CHOICE_PROVIDERS: dict[str, Callable[[], list[dict[str, Any]]]] = {
    "writable_file_storages": _writable_file_storages,
}


def _field_extra(field: FieldInfo) -> dict[str, Any]:
    """`Field` の `json_schema_extra`（dict のときだけ）を返す。"""
    return field.json_schema_extra if isinstance(field.json_schema_extra, dict) else {}


def _resolve_ref(schema: dict, node: Any) -> dict:
    """`$ref` を `$defs` の定義へ解決する（解決できなければそのまま返す）。"""
    if not isinstance(node, dict):
        return {}
    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/$defs/"):
        target = schema.get("$defs", {}).get(ref[len("#/$defs/"):])
        if isinstance(target, dict):
            return target
    return node


def _schema_types(schema: dict, prop: dict) -> tuple[str | None, bool, str | None, dict]:
    """項目の JSON Schema から「型・未設定を取れるか・配列要素の型・型の節」を取り出す。

    型が分からない（`$ref` の先が無いなど）ときは `None` を返す。入れ子のモデルは `object`。
    """
    variants = prop.get("anyOf") or [prop]
    resolved = [_resolve_ref(schema, variant) for variant in variants]
    nullable = any(variant.get("type") == "null" for variant in resolved)
    main = next((variant for variant in resolved if variant.get("type") != "null"), {})
    main_type = main.get("type")
    if main_type is None and ("properties" in main or "$ref" in main):
        main_type = "object"
    items_type = None
    if main_type == "array":
        items = _resolve_ref(schema, main.get("items"))
        items_type = items.get("type")
        if items_type is None and items:
            items_type = "object"
    return main_type, nullable, items_type, main


def _normalize_choices(choices: Any) -> list[dict[str, Any]]:
    """静的な `choices` を `{value, title}` のリストへそろえる。

    要素は `{value, title}`（`title` は省略可）か値そのもの。`title` が無ければ値の文字列化。

    Raises:
        ValueError: リストでない・`value` の無い dict がある場合。
    """
    if not isinstance(choices, (list, tuple)):
        raise ValueError("choices must be a list")
    normalized = []
    for choice in choices:
        if isinstance(choice, dict):
            if "value" not in choice:
                raise ValueError("each choice object must have 'value'")
            value = choice["value"]
            title = choice.get("title")
        else:
            value = choice
            title = None
        normalized.append({"value": value, "title": str(value) if title is None else str(title)})
    return normalized


def _check_choice_values(name: str, field: FieldInfo, choices: list[dict[str, Any]]) -> None:
    """静的な選択肢の各 `value` が項目の要素の型に合うかを確かめる。

    画面は `value` を JSON の型のまま送るため、送られたときと同じ形（1 要素の配列の JSON）を
    strict で検証する（`list[int]` に `"1"` を書いた宣言ミスを保存時ではなくロード時に見つける）。

    Raises:
        ValueError: 要素の型に合わない `value` がある場合。
    """
    adapter = TypeAdapter(field.annotation)
    for choice in choices:
        value = choice["value"]
        try:
            adapter.validate_json(json.dumps([value]), strict=True)
        except (ValidationError, TypeError, ValueError) as e:
            raise ValueError(
                f"Settings field '{name}' has a choice value {value!r} "
                f"that does not match the item type: {e}"
            ) from e


def validate_widgets(model: type[BaseModel]) -> None:
    """モデルの各項目の `widget` 宣言を検証する（拡張のロード時に呼ぶ）。

    Raises:
        ValueError: 未知の `widget`、または `checkbox-group` の宣言が不正な場合
            （スカラー要素の配列でない・`choices` と `choices_from` がどちらも無い／両方ある・
            未知の `choices_from`・`choices` の形が不正・`choices` の値が要素の型に合わない）。
    """
    schema = model.model_json_schema()
    properties = schema.get("properties", {})
    for name, field in model.model_fields.items():
        extra = _field_extra(field)
        widget = extra.get("widget")
        if widget is None:
            continue
        if widget not in SUPPORTED_WIDGETS:
            raise ValueError(
                f"Settings field '{name}' declares unknown widget {widget!r} "
                f"(supported: {', '.join(sorted(SUPPORTED_WIDGETS))})"
            )
        main_type, _, items_type, _ = _schema_types(schema, properties.get(name, {}))
        if main_type != "array" or items_type not in _CHOICE_ITEM_TYPES:
            raise ValueError(
                f"Settings field '{name}' uses widget '{WIDGET_CHECKBOX_GROUP}' "
                "but is not a list of scalar values"
            )
        has_static = "choices" in extra
        has_dynamic = "choices_from" in extra
        if has_static == has_dynamic:
            raise ValueError(
                f"Settings field '{name}' uses widget '{WIDGET_CHECKBOX_GROUP}' "
                "and must declare exactly one of 'choices' or 'choices_from'"
            )
        if has_dynamic and extra["choices_from"] not in CHOICE_PROVIDERS:
            raise ValueError(
                f"Settings field '{name}' declares unknown choices_from "
                f"{extra['choices_from']!r} (supported: {', '.join(sorted(CHOICE_PROVIDERS))})"
            )
        if has_static:
            try:
                choices = _normalize_choices(extra["choices"])
            except ValueError as e:
                raise ValueError(f"Settings field '{name}' has invalid choices: {e}") from e
            _check_choice_values(name, field, choices)


def describe_fields(model: type[BaseModel]) -> list[dict]:
    """画面の汎用編集欄に出す項目の説明を、モデルの定義順に返す。

    各要素は `name` / `title` / `description` / `type`（JSON Schema の型。`string` など。
    入れ子のモデルは `object`）/ `nullable`（未設定 `null` を取れるか）/ `multiline` /
    `default` / `pattern` / `widget`（`json_schema_extra` の `widget`。無ければ `None`）/
    `items_type`（配列要素の型。配列でなければ `None`）/ `choices`（静的な選択肢を
    `{value, title}` にそろえたもの。無ければ `None`）/ `choices_from`（動的な選択肢の
    名前。ここでは解決しない）/ `supported`（汎用フォームで入力欄を出せるか）。

    部品は型から推測しない。`widget` の無い配列や入れ子のモデルは `supported` が偽になる。
    """
    schema = model.model_json_schema()
    properties = schema.get("properties", {})
    defaults = model().model_dump(mode="json")
    fields = []
    for name, field in model.model_fields.items():
        prop = properties.get(name, {})
        main_type, nullable, items_type, main = _schema_types(schema, prop)
        extra = _field_extra(field)
        widget = extra.get("widget")
        choices = _normalize_choices(extra["choices"]) if "choices" in extra else None
        if widget is None:
            supported = main_type in _PLAIN_FIELD_TYPES or main_type is None
        else:
            supported = widget in SUPPORTED_WIDGETS
        fields.append(
            {
                "name": name,
                "title": field.title or prop.get("title") or name,
                "description": field.description or "",
                "type": main_type or "string",
                "nullable": nullable,
                "multiline": bool(extra.get("multiline")),
                "default": defaults[name],
                "pattern": main.get("pattern"),
                "widget": widget,
                "items_type": items_type,
                "choices": choices,
                "choices_from": extra.get("choices_from"),
                "supported": supported,
            }
        )
    return fields


def _resolve_choices(fields: list[dict]) -> None:
    """`choices_from` を持つ項目の `choices` を、今の候補で埋める（その場で書き換える）。

    プロバイダが失敗しても画面は開けるよう、WARNING ログを出して空の候補にする。
    """
    for field in fields:
        source = field.get("choices_from")
        if source is None:
            continue
        provider = CHOICE_PROVIDERS.get(source)
        if provider is None:
            logger.warning("Unknown choices_from '%s' for settings field '%s'", source, field["name"])
            field["choices"] = []
            continue
        try:
            field["choices"] = provider()
        except Exception as e:
            logger.warning(
                "Failed to resolve choices_from '%s' for settings field '%s': %s",
                source,
                field["name"],
                e,
            )
            field["choices"] = []


async def build_settings_detail(name: str) -> dict | None:
    """編集画面に出す設定の節を組み立てる。設定を宣言していない拡張なら `None`。

    `fields` は `describe_fields()` の結果で、`choices_from` を持つ項目の `choices` は
    ここで今の候補に解決する（フォームは解決済みの `choices` だけを見る）。
    `values` は保存済みの値（未保存なら初期値）、`stored` は保存済みか、`error` は
    読めなかったとき（`unavailable`）と保存値が不正なとき（`invalid`。`values` には
    直せるよう保存されている生の値を入れる）の理由。
    """
    model = get_settings_model(name)
    if model is None:
        return None
    fields = describe_fields(model)
    _resolve_choices(fields)
    detail: dict[str, Any] = {
        "fields": fields,
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
