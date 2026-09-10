"""exceptions.py のテスト。"""
import pytest

import sys
from pathlib import Path

from lilla_core.core.exceptions import LLMError, ReauthenticationRequiredError


sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


class TestReauthenticationRequiredError:
    def test_is_exception_subclass(self) -> None:
        """ReauthenticationRequiredError が Exception のサブクラスであることを確認する。"""
        assert issubclass(ReauthenticationRequiredError, Exception)

    def test_can_be_raised_and_caught(self) -> None:
        """raise / except が正常に動作することを確認する。"""
        with pytest.raises(ReauthenticationRequiredError):
            raise ReauthenticationRequiredError("再認証が必要です")

    def test_message_is_preserved(self) -> None:
        """エラーメッセージが保持されることを確認する。"""
        msg = "リフレッシュトークンが期限切れです"
        err = ReauthenticationRequiredError(msg)
        assert str(err) == msg


class TestLLMError:
    def test_is_exception_subclass(self) -> None:
        """LLMError が Exception のサブクラスであることを確認する。"""
        assert issubclass(LLMError, Exception)

    def test_can_be_raised_and_caught(self) -> None:
        """raise / except が正常に動作することを確認する。"""
        with pytest.raises(LLMError):
            raise LLMError("LLM呼び出しエラー: 接続失敗")

    def test_message_is_preserved(self) -> None:
        """エラーメッセージが保持されることを確認する。"""
        msg = "LLM呼び出しエラー: タイムアウト"
        err = LLMError(msg)
        assert str(err) == msg

    def test_can_be_caught_as_generic_exception(self) -> None:
        """既存の except Exception 節でも捕捉できることを確認する。"""
        try:
            raise LLMError("LLM呼び出しエラー: 予期しない応答")
        except Exception as e:  # noqa: BLE001 - 呼び出し側の捕捉挙動を再現する
            assert isinstance(e, LLMError)
