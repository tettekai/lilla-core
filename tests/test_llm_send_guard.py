"""core/llm_send_guard.py のテスト。

拒否リストにはダミーの語だけを使う（実在の個人情報は使わない）。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from lilla_core.core import llm_send_guard
from lilla_core.core.exceptions import (
    LLMError,
    LlmSendBlockedError,
    LlmSendGuardUnavailableError,
)

DUMMY_TERM = "Zyxwordqa"


@pytest.fixture(autouse=True)
def clear_guard_cache():
    """テストごとに読み込み済みの拒否リストを捨てる。"""
    llm_send_guard.clear_cache()
    yield
    llm_send_guard.clear_cache()


def _use_path(monkeypatch: pytest.MonkeyPatch, path: str | None) -> None:
    """`llm.send_blocklist_path` だけを差し替えた設定を `get_config()` に返させる。"""
    cfg = MagicMock()
    cfg.llm.send_blocklist_path = path
    monkeypatch.setattr(llm_send_guard, "get_config", lambda: cfg)


def _write_list(tmp_path: Path, content) -> str:
    """拒否リストファイルを書き、その絶対パスを返す。"""
    path = tmp_path / "blocklist.json"
    path.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
    return str(path)


class TestDisabled:
    def test_no_path_allows_anything(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """パス未設定なら、メールアドレスの形があっても止めない。"""
        _use_path(monkeypatch, None)
        llm_send_guard.ensure_llm_request_allowed({"messages": [{"content": "a@example.com"}]})


class TestMatching:
    @pytest.fixture
    def enabled(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _use_path(monkeypatch, _write_list(tmp_path, [DUMMY_TERM]))

    @pytest.mark.parametrize(
        "data",
        [
            {"messages": [{"role": "user", "content": f"hello {DUMMY_TERM}"}]},
            {"input": [{"role": "user", "content": DUMMY_TERM}]},
            {"instructions": f"sys {DUMMY_TERM}"},
            {"temperature": 0.5, "metadata": {"note": DUMMY_TERM}},
            {"tools": [{"type": "function", "function": {"description": DUMMY_TERM}}]},
            {"tools": [{"function": {"parameters": {"properties": {DUMMY_TERM: {}}}}}]},
        ],
    )
    def test_blocks_term_anywhere_in_body(self, enabled, data, caplog) -> None:
        """ボディのどのキーの文字列（辞書のキーも）に語があっても止め、語をログに出さない。"""
        caplog.set_level(logging.DEBUG)
        with pytest.raises(LlmSendBlockedError) as exc_info:
            llm_send_guard.ensure_llm_request_allowed(data)
        assert DUMMY_TERM.casefold() not in str(exc_info.value).casefold()
        assert DUMMY_TERM.casefold() not in caplog.text.casefold()

    def test_nfkc_and_case_insensitive(self, enabled) -> None:
        """全角・大文字でも一致する。"""
        fullwidth = "ＺＹＸＷＯＲＤＱＡ"
        with pytest.raises(LlmSendBlockedError):
            llm_send_guard.ensure_llm_request_allowed({"messages": [{"content": fullwidth}]})

    def test_email_shape_is_always_checked(self, enabled, caplog) -> None:
        """リストに無くてもメールアドレスの形なら止める。"""
        with pytest.raises(LlmSendBlockedError):
            llm_send_guard.ensure_llm_request_allowed({"messages": [{"content": "to dummy.user@example.test ok"}]})
        assert "dummy.user" not in caplog.text

    def test_clean_body_is_allowed(self, enabled) -> None:
        llm_send_guard.ensure_llm_request_allowed(
            {"model": "m", "messages": [{"role": "user", "content": "@here hello"}], "stream": False}
        )

    def test_data_url_base64_is_ignored(self, enabled) -> None:
        """画像の data URL は文字列にならない部分として対象外。"""
        data_url = "data:image/png;base64," + DUMMY_TERM + "AAAA@example.com"
        llm_send_guard.ensure_llm_request_allowed(
            {"messages": [{"content": [{"type": "image_url", "image_url": {"url": data_url}}]}]}
        )

    def test_empty_list_still_checks_email(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """空配列は検査を有効にしたまま一致なしとして扱う。"""
        _use_path(monkeypatch, _write_list(tmp_path, []))
        llm_send_guard.ensure_llm_request_allowed({"messages": [{"content": DUMMY_TERM}]})
        with pytest.raises(LlmSendBlockedError):
            llm_send_guard.ensure_llm_request_allowed({"messages": [{"content": "a@example.com"}]})


class TestUnavailable:
    @pytest.mark.parametrize(
        "content",
        ["not json", json.dumps({"a": "b"}), json.dumps(["ok", 1]), json.dumps(["ok", ""])],
    )
    def test_invalid_list_blocks_every_send(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, content: str, caplog
    ) -> None:
        """JSON でない・文字列配列でない・空文字を含むリストでは送らない。"""
        _use_path(monkeypatch, _write_list(tmp_path, content))
        with pytest.raises(LlmSendGuardUnavailableError):
            llm_send_guard.ensure_llm_request_allowed({"messages": []})
        assert "not json" not in caplog.text

    def test_missing_file_blocks_send(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _use_path(monkeypatch, str(tmp_path / "missing.json"))
        with pytest.raises(LlmSendGuardUnavailableError):
            llm_send_guard.ensure_llm_request_allowed({"messages": []})

    def test_errors_are_llm_errors(self) -> None:
        assert issubclass(LlmSendBlockedError, LLMError)
        assert issubclass(LlmSendGuardUnavailableError, LLMError)


class TestPreload:
    def test_reads_once_at_startup(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """起動時に読んだ内容をメモリに持ち、あとでファイルが変わっても読み直さない。"""
        path = _write_list(tmp_path, [DUMMY_TERM])
        cfg = MagicMock()
        cfg.llm.send_blocklist_path = path
        llm_send_guard.preload_llm_send_blocklist(cfg)
        Path(path).write_text("broken", encoding="utf-8")
        _use_path(monkeypatch, path)
        with pytest.raises(LlmSendBlockedError):
            llm_send_guard.ensure_llm_request_allowed({"messages": [{"content": DUMMY_TERM}]})

    def test_failure_does_not_raise_at_startup(self, tmp_path: Path) -> None:
        cfg = MagicMock()
        cfg.llm.send_blocklist_path = str(tmp_path / "missing.json")
        llm_send_guard.preload_llm_send_blocklist(cfg)

    def test_no_path_is_noop(self) -> None:
        cfg = MagicMock()
        cfg.llm.send_blocklist_path = None
        llm_send_guard.preload_llm_send_blocklist(cfg)
