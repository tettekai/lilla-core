"""tool_support/jev.py（TypeSafe System One 呼び出しヘルパー）のテスト。

実 API は呼ばず、``send_http_request`` を差し替えてリクエスト形と失敗時の例外を固定する。
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from lilla_core.tool_support import jev
from lilla_core.tool_support.jev import (
    DEFAULT_MODEL,
    DEFAULT_URL,
    DIFFICULTY_SCORE,
    NEEDS_TOOL_NOUL,
    ChoiceAnswer,
    JevConfigError,
    JevError,
    JevRequestError,
    JevResponseError,
    NoulAnswer,
    ScoreAnswer,
    ask_jev,
    choice_question,
    noul_question,
    parse_response,
    resolve_api_key,
    score_question,
)

_OK_BODY = {
    "model": "jev-2026-09",
    "usage": {"input_tokens": 120, "output_tokens": 12},
    "answers": {
        "tone": {
            "type": "choice",
            "choice": "casual",
            "confidence": 0.9,
            "probabilities": {"casual": 0.9, "formal": 0.1},
        },
        "difficulty": {
            "type": "score",
            "score": 1.7,
            "confidence": 0.8,
            "legend": {"0": "easy", "1": "mid", "2": "hard"},
            "probabilities": {"0": 0.1, "1": 0.1, "2": 0.8},
        },
        "needs_tool": {"type": "noul", "noul": 0.98},
    },
}

_QUESTIONS = {
    "tone": choice_question({"casual": None, "formal": "Polite register."}),
    "difficulty": score_question(["easy", "mid", "hard"]),
    "needs_tool": noul_question("Needs a tool."),
}


@pytest.fixture
def mock_send():
    """``send_http_request`` を成功応答を返す AsyncMock に差し替える。"""
    with patch.object(
        jev, "send_http_request", new=AsyncMock(return_value=json.dumps(_OK_BODY))
    ) as mock:
        yield mock


@pytest.fixture(autouse=True)
def _no_env_key(monkeypatch):
    """実行環境の TYPESAFE_API_KEY に左右されないよう消しておく。"""
    monkeypatch.delenv(jev.API_KEY_ENV, raising=False)


def _response_error(status: int, message: str) -> aiohttp.ClientResponseError:
    """``send_http_request`` が HTTP エラー時に投げるのと同じ例外を作る。"""
    return aiohttp.ClientResponseError(
        MagicMock(), (), status=status, message=message, headers=None
    )


class TestQuestionBuilders:
    """質問 dict の組み立てヘルパー。"""

    def test_choice(self):
        """type と criteria を持ち、instructions は渡したときだけ入ること。"""
        assert choice_question({"a": None}) == {"type": "choice", "criteria": {"a": None}}
        assert choice_question({"a": "x"}, instructions="Q?") == {
            "type": "choice",
            "instructions": "Q?",
            "criteria": {"a": "x"},
        }

    def test_score(self):
        """criteria を順序どおりの list で持つこと。"""
        assert score_question(("lo", "hi"), "Rate") == {
            "type": "score",
            "instructions": "Rate",
            "criteria": ["lo", "hi"],
        }

    def test_noul_omits_empty_criteria(self):
        """true / false を渡さなければ criteria を省くこと。"""
        assert noul_question("Spam?") == {"type": "noul", "instructions": "Spam?"}
        assert noul_question(true="yes") == {"type": "noul", "criteria": {"true": "yes"}}

    def test_templates_are_valid_questions(self):
        """テンプレ定数がそれぞれ score / noul の質問であること。"""
        assert DIFFICULTY_SCORE["type"] == "score"
        assert len(DIFFICULTY_SCORE["criteria"]) == 3
        assert NEEDS_TOOL_NOUL["type"] == "noul"


class TestResolveApiKey:
    """API キーの解決。"""

    def test_argument_wins(self, monkeypatch):
        """引数が環境変数より優先され、前後の空白は除かれること。"""
        monkeypatch.setenv(jev.API_KEY_ENV, "env-key")
        assert resolve_api_key("  arg-key ") == "arg-key"

    def test_env_fallback(self, monkeypatch):
        """引数が無ければ環境変数を読むこと。"""
        monkeypatch.setenv(jev.API_KEY_ENV, "env-key")
        assert resolve_api_key() == "env-key"

    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_missing_raises(self, value):
        """キーが無い・空白だけなら JevConfigError になること。"""
        with pytest.raises(JevConfigError, match="TYPESAFE_API_KEY"):
            resolve_api_key(value)

    def test_invalid_characters_raise(self):
        """空白や非 ASCII を含むキーは弾くこと。"""
        with pytest.raises(JevConfigError):
            resolve_api_key("ab cd")
        with pytest.raises(JevConfigError):
            resolve_api_key("キー")


class TestAskJevRequest:
    """``ask_jev`` が送るリクエストの形。"""

    async def test_request_shape(self, mock_send):
        """既定の URL・POST・Bearer ヘッダ・state / model / questions のボディで送ること。"""
        await ask_jev({"message": "hi"}, _QUESTIONS, api_key="sk-test")

        mock_send.assert_awaited_once()
        args, kwargs = mock_send.call_args
        assert args == (DEFAULT_URL,)
        assert kwargs["method"] == "POST"
        assert kwargs["timeout"] == jev.DEFAULT_TIMEOUT_SECONDS
        assert kwargs["headers"]["Authorization"] == "Bearer sk-test"
        assert kwargs["headers"]["Content-Type"] == "application/json"
        assert kwargs["data"] == {
            "state": {"message": "hi"},
            "model": DEFAULT_MODEL,
            "questions": _QUESTIONS,
        }

    async def test_overrides(self, mock_send):
        """url / model / timeout を上書きできること。"""
        await ask_jev(
            "state",
            _QUESTIONS,
            api_key="k",
            url="https://example.test/v1/systemone",
            model="jev-custom",
            timeout=3,
        )
        args, kwargs = mock_send.call_args
        assert args == ("https://example.test/v1/systemone",)
        assert kwargs["data"]["model"] == "jev-custom"
        assert kwargs["timeout"] == 3

    async def test_api_key_from_env(self, mock_send, monkeypatch):
        """api_key を省略すると環境変数のキーを使うこと。"""
        monkeypatch.setenv(jev.API_KEY_ENV, "env-key")
        await ask_jev("s", _QUESTIONS)
        assert mock_send.call_args.kwargs["headers"]["Authorization"] == "Bearer env-key"

    async def test_does_not_mutate_caller_questions(self, mock_send):
        """送る質問は複製で、呼び出し側やテンプレ定数を書き換えないこと。"""
        questions = {"d": DIFFICULTY_SCORE}
        mock_send.return_value = json.dumps(
            {"answers": {"d": {"type": "noul", "noul": 0.5}}}
        )
        await ask_jev("s", questions, api_key="k")
        sent = mock_send.call_args.kwargs["data"]["questions"]["d"]
        assert sent == DIFFICULTY_SCORE
        assert sent is not DIFFICULTY_SCORE
        assert sent["criteria"] is not DIFFICULTY_SCORE["criteria"]


class TestAskJevSuccess:
    """成功時の戻り値。"""

    async def test_typed_answers(self, mock_send):
        """answers が質問名をキーに型のある答えで返ること。"""
        result = await ask_jev("s", _QUESTIONS, api_key="k")

        assert result.model == "jev-2026-09"
        assert result.usage.input_tokens == 120
        assert result.usage.output_tokens == 12
        assert result.answers["tone"] == ChoiceAnswer(
            choice="casual", confidence=0.9, probabilities={"casual": 0.9, "formal": 0.1}
        )
        assert result.answers["difficulty"] == ScoreAnswer(
            score=1.7,
            confidence=0.8,
            legend={0: "easy", 1: "mid", 2: "hard"},
            probabilities={0: 0.1, 1: 0.1, 2: 0.8},
        )
        assert result.answers["needs_tool"] == NoulAnswer(noul=0.98)


class TestAskJevErrors:
    """失敗はすべて JevError の派生で返ること。"""

    async def test_missing_key_does_not_send(self, mock_send):
        """キー未設定なら送信せずに JevConfigError になること。"""
        with pytest.raises(JevConfigError):
            await ask_jev("s", _QUESTIONS)
        mock_send.assert_not_awaited()

    @pytest.mark.parametrize(
        "questions",
        [
            {},
            {"x": {"type": "unknown"}},
            {"x": {"type": "choice"}},
            {"x": {"type": "score", "criteria": []}},
            {"": {"type": "noul"}},
            {"x": "noul"},
        ],
    )
    async def test_invalid_questions_do_not_send(self, mock_send, questions):
        """形の誤った質問は送信前に JevConfigError になること。"""
        with pytest.raises(JevConfigError):
            await ask_jev("s", questions, api_key="k")
        mock_send.assert_not_awaited()

    async def test_none_state_raises(self, mock_send):
        """state が None なら JevConfigError になること。"""
        with pytest.raises(JevConfigError):
            await ask_jev(None, _QUESTIONS, api_key="k")
        mock_send.assert_not_awaited()

    async def test_http_error(self, mock_send):
        """HTTP エラー応答は status 付きの JevRequestError になること。"""
        mock_send.side_effect = _response_error(401, '{"detail":"invalid key"}')
        with pytest.raises(JevRequestError) as exc_info:
            await ask_jev("s", _QUESTIONS, api_key="k")
        assert exc_info.value.status == 401
        assert "HTTP 401" in str(exc_info.value)
        assert isinstance(exc_info.value, JevError)

    async def test_http_error_message_does_not_leak_key(self, mock_send):
        """例外メッセージに API キーを含めないこと。"""
        mock_send.side_effect = _response_error(500, "boom")
        with pytest.raises(JevRequestError) as exc_info:
            await ask_jev("s", _QUESTIONS, api_key="sk-secret")
        assert "sk-secret" not in str(exc_info.value)

    async def test_timeout(self, mock_send):
        """タイムアウトは status=None の JevRequestError になること。"""
        mock_send.side_effect = asyncio.TimeoutError()
        with pytest.raises(JevRequestError, match="timed out") as exc_info:
            await ask_jev("s", _QUESTIONS, api_key="k", timeout=2)
        assert exc_info.value.status is None

    async def test_connection_error(self, mock_send):
        """接続失敗は JevRequestError になること。"""
        mock_send.side_effect = aiohttp.ClientConnectionError("refused")
        with pytest.raises(JevRequestError):
            await ask_jev("s", _QUESTIONS, api_key="k")

    async def test_malformed_body(self, mock_send):
        """JSON でない応答は JevResponseError になること。"""
        mock_send.return_value = "<html>bad gateway</html>"
        with pytest.raises(JevResponseError):
            await ask_jev("s", _QUESTIONS, api_key="k")

    async def test_missing_answer(self, mock_send):
        """送った質問の答えが欠けていれば JevResponseError になること。"""
        body = json.loads(json.dumps(_OK_BODY))
        del body["answers"]["tone"]
        mock_send.return_value = json.dumps(body)
        with pytest.raises(JevResponseError, match="tone"):
            await ask_jev("s", _QUESTIONS, api_key="k")


class TestParseResponse:
    """応答本文の検証。"""

    @pytest.mark.parametrize(
        "body",
        [
            "[]",
            "{}",
            '{"answers": []}',
            '{"answers": {"a": "x"}}',
            '{"answers": {"a": {"type": "mystery"}}}',
            '{"answers": {"a": {"type": "noul", "noul": "0.5"}}}',
            '{"answers": {"a": {"type": "noul", "noul": true}}}',
            '{"answers": {"a": {"type": "choice", "choice": 1, "confidence": 0.5, "probabilities": {}}}}',
            '{"answers": {"a": {"type": "choice", "choice": "x", "confidence": 0.5}}}',
            '{"answers": {"a": {"type": "score", "score": 1, "confidence": 0.5, "probabilities": {"0": 1}}}}',
            '{"answers": {"a": {"type": "score", "score": 1, "confidence": 0.5, "legend": {"x": "a"}, "probabilities": {"x": 1}}}}',
        ],
    )
    def test_malformed_raises(self, body):
        """期待した形でない応答は JevResponseError になること。"""
        with pytest.raises(JevResponseError):
            parse_response(body)

    def test_missing_usage_and_model_are_tolerated(self):
        """usage / model が無くても答えが読めれば成功とすること。"""
        result = parse_response('{"answers": {"a": {"type": "noul", "noul": 1}}}', ["a"])
        assert result.model == ""
        assert result.usage.input_tokens is None
        assert result.answers["a"] == NoulAnswer(noul=1.0)


class TestOptIn:
    """コアの経路がこのヘルパーに依存しないこと。"""

    def test_no_core_module_imports_jev(self):
        """``lilla_core`` のソースのどこからも jev を import していないこと。"""
        from pathlib import Path

        src = Path(jev.__file__).resolve().parents[1]
        offenders = [
            str(path.relative_to(src))
            for path in src.rglob("*.py")
            if path.name != "jev.py" and "tool_support.jev" in path.read_text(encoding="utf-8")
        ]
        assert offenders == []
