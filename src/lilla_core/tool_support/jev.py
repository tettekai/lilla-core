"""TypeSafe System One（Jev）を呼び出す任意の判定ヘルパー。

Jev は文章を書かず、「状態（state）」と「型のある質問（questions）」を受け取って
Choice / Score / Noul の答えを確率つきで返すモデル。ツール実行前のゲートや
要約の採用判定、LLM resolver のモデル選択など「定まった答えの中から速く選ぶ」
場所で使える。

このモジュールは opt-in で、コアのどこからも import しない（import しない限り
外部 API に触れない）。TypeSafe 公式 SDK には依存せず、`core/http_util.py` の
``send_http_request``（プロキシ設定を含む）で POST を 1 本送るだけの薄い層に留める。
どの答えをどう使うか（プロバイダー名への変換・confidence の閾値など）は
呼び出し側の責任で、ここには持ち込まない。

使い方::

    from lilla_core.tool_support.jev import (
        DIFFICULTY_SCORE, NEEDS_TOOL_NOUL, ask_jev, choice_question,
    )

    result = await ask_jev(
        {"message": "明日の天気は？"},
        {
            "difficulty": DIFFICULTY_SCORE,
            "needs_tool": NEEDS_TOOL_NOUL,
            "tone": choice_question({"casual": None, "formal": None}),
        },
    )
    result.answers["difficulty"].score   # 0〜2 の期待値
    result.answers["needs_tool"].noul    # yes の確率
    result.answers["tone"].choice        # 最も確からしいラベル

API キーは引数 ``api_key`` か環境変数 ``TYPESAFE_API_KEY`` から取る（YAML には書かない）。
失敗はすべて ``JevError`` の派生で投げるので、呼び出し側は ``except JevError`` で
捕まえて既定の分岐へ逃げればよい。
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import math
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypeAlias

import aiohttp

from lilla_core.core.http_util import send_http_request

logger = logging.getLogger(__name__)

DEFAULT_URL = "https://api.typesafe.ai/v1/systemone"
"""既定のエンドポイント（TypeSafe 公式）。``ask_jev(url=...)`` で上書きできる。"""

DEFAULT_MODEL = "jev-latest"
"""既定のモデル名。"""

API_KEY_ENV = "TYPESAFE_API_KEY"
"""``api_key`` 引数を省略したときに API キーを読む環境変数名（公式 SDK と同じ）。"""

DEFAULT_TIMEOUT_SECONDS = 10
"""既定のタイムアウト秒数（接続から応答本文の受信まで）。"""

_QUESTION_TYPES = ("choice", "score", "noul")
_MAX_ERROR_BODY_LENGTH = 200


class JevError(Exception):
    """Jev 呼び出しで起きた失敗の基底例外。呼び出し側はこれを捕まえて逃げればよい。"""


class JevConfigError(JevError):
    """API キーの欠落など、リクエストを送る前に分かる設定・入力の誤り。"""


class JevRequestError(JevError):
    """HTTP エラー応答・接続失敗・タイムアウト。

    Attributes:
        status: HTTP ステータスコード。応答を受け取れなかった場合（接続失敗・
            タイムアウト）は ``None``。
    """

    def __init__(self, message: str, status: int | None = None) -> None:
        """メッセージと HTTP ステータス（あれば）を保持する。"""
        super().__init__(message)
        self.status = status


class JevResponseError(JevError):
    """応答が JSON でない・期待した形でないなど、応答本文の破損。"""


@dataclass(frozen=True)
class ChoiceAnswer:
    """Choice の答え。

    Attributes:
        choice: 最も確率の高いラベル。
        confidence: ``choice`` への確信度（0〜1）。
        probabilities: ラベルごとの確率（0〜1）。
    """

    choice: str
    confidence: float
    probabilities: dict[str, float]


@dataclass(frozen=True)
class ScoreAnswer:
    """Score の答え。

    Attributes:
        score: 各段階の確率で重みづけした期待値（段階の間の値をとりうる）。
        confidence: ``score`` への確信度（0〜1）。
        legend: 段階（0 始まりの整数）→ 質問で渡した基準の説明。
        probabilities: 段階（0 始まりの整数）→ 確率（0〜1）。
    """

    score: float
    confidence: float
    legend: dict[int, Any]
    probabilities: dict[int, float]


@dataclass(frozen=True)
class NoulAnswer:
    """Noul（yes / no）の答え。

    Attributes:
        noul: yes（文が真である）確率（0〜1）。
    """

    noul: float


Answer: TypeAlias = ChoiceAnswer | ScoreAnswer | NoulAnswer


@dataclass(frozen=True)
class JevUsage:
    """API が報告したトークン数（報告が無い項目は ``None``）。"""

    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True)
class JevResult:
    """``ask_jev`` の戻り値。

    Attributes:
        model: 応答したモデル名。
        answers: 質問名 → 答え。API の ``answers`` と同じキーで、送った質問すべてを含む。
        usage: トークン数。
    """

    model: str
    answers: dict[str, Answer]
    usage: JevUsage


def choice_question(
    criteria: Mapping[str, Any], instructions: Any = None
) -> dict[str, Any]:
    """Choice の質問 dict を組み立てる。

    Args:
        criteria: ラベル → 説明（文字列・dict・list、説明しないなら ``None``）。1 件以上。
        instructions: 質問文（文字列・dict・list）。省略可。

    Returns:
        ``ask_jev`` の ``questions`` の値としてそのまま渡せる dict。
    """
    return _build_question("choice", instructions, dict(criteria))


def score_question(criteria: Sequence[Any], instructions: Any = None) -> dict[str, Any]:
    """Score の質問 dict を組み立てる。

    Args:
        criteria: 段階 0 から順に並べた各段階の説明。1 件以上。
        instructions: 質問文（文字列・dict・list）。省略可。

    Returns:
        ``ask_jev`` の ``questions`` の値としてそのまま渡せる dict。
    """
    return _build_question("score", instructions, list(criteria))


def noul_question(
    instructions: Any = None, *, true: Any = None, false: Any = None
) -> dict[str, Any]:
    """Noul（yes / no）の質問 dict を組み立てる。

    Args:
        instructions: 判定する質問文や命題（文字列・dict・list）。省略可。
        true: yes とみなす条件の説明。省略可。
        false: no とみなす条件の説明。省略可。

    Returns:
        ``ask_jev`` の ``questions`` の値としてそのまま渡せる dict。
    """
    criteria = {key: value for key, value in (("true", true), ("false", false)) if value is not None}
    return _build_question("noul", instructions, criteria or None)


def _build_question(question_type: str, instructions: Any, criteria: Any) -> dict[str, Any]:
    """``type`` / ``instructions`` / ``criteria`` から、``None`` の項目を省いた質問 dict を作る。"""
    question: dict[str, Any] = {"type": question_type}
    if instructions is not None:
        question["instructions"] = instructions
    if criteria is not None:
        question["criteria"] = criteria
    return question


DIFFICULTY_SCORE = score_question(
    [
        "Trivial: small talk or a short factual reply.",
        "Moderate: needs some reasoning or a few steps.",
        "Hard: multi-step reasoning, careful planning, or long output.",
    ],
    instructions="How demanding is it to answer the latest user message well?",
)
"""よく使う質問の例: 最新の発話に答える難しさを 0〜2 の Score で聞く。

``ask_jev`` は送る前に質問を複製するので、このままでも書き換えた複製でも使える。
"""

NEEDS_TOOL_NOUL = noul_question(
    "Answering the latest user message requires calling an external tool "
    "(search, calendar, files, etc.).",
    true="The answer depends on data or actions outside the conversation.",
    false="The answer can be written from the conversation and general knowledge alone.",
)
"""よく使う質問の例: 最新の発話に答えるのにツールが要るかを Noul で聞く。"""


def resolve_api_key(api_key: str | None = None) -> str:
    """API キーを引数 → 環境変数 ``TYPESAFE_API_KEY`` の順で解決する。

    Args:
        api_key: 明示的に渡す API キー。``None`` なら環境変数を読む。

    Returns:
        前後の空白を除いた API キー。

    Raises:
        JevConfigError: どちらからも空でないキーが得られない場合、またはキーが
            空白や印字できない文字を含む場合。
    """
    key = (api_key if api_key is not None else os.environ.get(API_KEY_ENV, "")).strip()
    if not key:
        raise JevConfigError(
            f"No TypeSafe API key was provided. Pass api_key or set {API_KEY_ENV}."
        )
    if not key.isascii() or not key.isprintable() or " " in key:
        raise JevConfigError("TypeSafe API key must be printable ASCII without whitespace.")
    return key


def _normalize_questions(questions: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """質問群を検証し、呼び出し側の dict を書き換えないよう複製して返す。

    Raises:
        JevConfigError: 質問が 0 件・名前が空・type が未知・criteria の欠落や空の場合。
    """
    if not isinstance(questions, Mapping) or not questions:
        raise JevConfigError("At least one question is required.")
    normalized: dict[str, dict[str, Any]] = {}
    for name, question in questions.items():
        if not isinstance(name, str) or not name:
            raise JevConfigError("Question names must be non-empty strings.")
        if not isinstance(question, Mapping):
            raise JevConfigError(f'Question "{name}" must be a mapping.')
        question_type = question.get("type")
        if question_type not in _QUESTION_TYPES:
            raise JevConfigError(
                f'Question "{name}" has unsupported type {question_type!r}; '
                f"expected one of {', '.join(_QUESTION_TYPES)}."
            )
        if question_type in ("choice", "score") and not question.get("criteria"):
            raise JevConfigError(f'Question "{name}" of type {question_type} requires non-empty criteria.')
        normalized[name] = copy.deepcopy(dict(question))
    return normalized


def _is_number(value: Any) -> bool:
    """bool を除く有限の int / float なら True。"""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _require_number(raw: Mapping[str, Any], key: str, path: str) -> float:
    """``raw[key]`` が数値であることを確かめて float で返す。"""
    value = raw.get(key)
    if not _is_number(value):
        raise JevResponseError(f"Malformed Jev response: {path}.{key} must be a number.")
    return float(value)


def _require_probabilities(raw: Mapping[str, Any], path: str) -> dict[str, float]:
    """``raw["probabilities"]`` が「文字列キー → 数値」の dict であることを確かめて返す。"""
    value = raw.get("probabilities")
    if not isinstance(value, dict) or not all(_is_number(v) for v in value.values()):
        raise JevResponseError(
            f"Malformed Jev response: {path}.probabilities must be an object of numbers."
        )
    return {str(k): float(v) for k, v in value.items()}


def _int_keys(value: Mapping[str, Any], path: str) -> dict[int, Any]:
    """JSON オブジェクトの文字列キー（"0", "1", ...）を整数キーに直す。"""
    try:
        return {int(k): v for k, v in value.items()}
    except (TypeError, ValueError) as e:
        raise JevResponseError(f"Malformed Jev response: {path} keys must be integers.") from e


def _parse_answer(name: str, raw: Any) -> Answer:
    """API の答え 1 件を型のある答えに変換する。"""
    path = f"answers.{name}"
    if not isinstance(raw, dict):
        raise JevResponseError(f"Malformed Jev response: {path} must be an object.")
    answer_type = raw.get("type")
    if answer_type == "choice":
        choice = raw.get("choice")
        if not isinstance(choice, str):
            raise JevResponseError(f"Malformed Jev response: {path}.choice must be a string.")
        return ChoiceAnswer(
            choice=choice,
            confidence=_require_number(raw, "confidence", path),
            probabilities=_require_probabilities(raw, path),
        )
    if answer_type == "score":
        legend = raw.get("legend")
        if not isinstance(legend, dict):
            raise JevResponseError(f"Malformed Jev response: {path}.legend must be an object.")
        return ScoreAnswer(
            score=_require_number(raw, "score", path),
            confidence=_require_number(raw, "confidence", path),
            legend=_int_keys(legend, f"{path}.legend"),
            probabilities=_int_keys(_require_probabilities(raw, path), f"{path}.probabilities"),
        )
    if answer_type == "noul":
        return NoulAnswer(noul=_require_number(raw, "noul", path))
    raise JevResponseError(f"Malformed Jev response: {path}.type {answer_type!r} is not supported.")


def _parse_usage(raw: Any) -> JevUsage:
    """``usage`` を読む。無い・形が違う項目は ``None`` にする（判定結果には影響しないため）。"""
    if not isinstance(raw, dict):
        return JevUsage()

    def _int_or_none(value: Any) -> int | None:
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    return JevUsage(
        input_tokens=_int_or_none(raw.get("input_tokens")),
        output_tokens=_int_or_none(raw.get("output_tokens")),
    )


def parse_response(response_text: str, question_names: Sequence[str] = ()) -> JevResult:
    """System One の応答本文（JSON 文字列）を ``JevResult`` に変換する。

    Args:
        response_text: 応答本文。
        question_names: 送った質問名。1 つでも答えが欠けていれば応答破損として扱う。

    Returns:
        型のある答えを持つ ``JevResult``。

    Raises:
        JevResponseError: JSON でない、または期待した形でない場合。
    """
    try:
        decoded = json.loads(response_text)
    except (json.JSONDecodeError, TypeError) as e:
        raise JevResponseError("Malformed Jev response: body is not valid JSON.") from e
    if not isinstance(decoded, dict):
        raise JevResponseError("Malformed Jev response: body must be a JSON object.")

    raw_answers = decoded.get("answers")
    if not isinstance(raw_answers, dict):
        raise JevResponseError("Malformed Jev response: answers must be an object.")
    missing = [name for name in question_names if name not in raw_answers]
    if missing:
        raise JevResponseError(f"Malformed Jev response: missing answers for {', '.join(missing)}.")

    model = decoded.get("model")
    return JevResult(
        model=model if isinstance(model, str) else "",
        answers={name: _parse_answer(name, raw) for name, raw in raw_answers.items()},
        usage=_parse_usage(decoded.get("usage")),
    )


async def ask_jev(
    state: Any,
    questions: Mapping[str, Mapping[str, Any]],
    *,
    api_key: str | None = None,
    url: str = DEFAULT_URL,
    model: str = DEFAULT_MODEL,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> JevResult:
    """Jev に state と questions を送り、型のある答えを返す。

    Args:
        state: 判定の材料になる状態（文字列・dict・list など JSON にできる値）。
        questions: 質問名 → 質問 dict（``choice_question`` / ``score_question`` /
            ``noul_question`` で組み立てたもの、または同じ形の dict）。
        api_key: API キー。省略時は環境変数 ``TYPESAFE_API_KEY``。
        url: エンドポイント。既定は TypeSafe 公式（``DEFAULT_URL``）。
        model: モデル名。既定は ``DEFAULT_MODEL``。
        timeout: タイムアウト秒数。

    Returns:
        ``JevResult``。``answers`` は送った質問名と同じキーを持つ。

    Raises:
        JevConfigError: API キーの欠落・state が ``None``・質問の形の誤り（送信前）。
        JevRequestError: HTTP エラー応答・接続失敗・タイムアウト。
        JevResponseError: 応答本文の破損・答えの欠落。
    """
    key = resolve_api_key(api_key)
    if state is None:
        raise JevConfigError("state must not be None.")
    normalized = _normalize_questions(questions)
    if not model:
        raise JevConfigError("model must be a non-empty string.")

    body = {"state": state, "model": model, "questions": normalized}
    headers = {
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    try:
        response_text = await send_http_request(
            url, method="POST", data=body, headers=headers, timeout=timeout
        )
    except aiohttp.ClientResponseError as e:
        detail = (e.message or "")[:_MAX_ERROR_BODY_LENGTH]
        raise JevRequestError(
            f"Jev request failed with HTTP {e.status}: {detail}", status=e.status
        ) from e
    except asyncio.TimeoutError as e:
        raise JevRequestError(f"Jev request timed out after {timeout}s.") from e
    except aiohttp.ClientError as e:
        raise JevRequestError(f"Jev request failed: {type(e).__name__}: {e}") from e

    return parse_response(response_text, list(normalized))
