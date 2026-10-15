"""`lilla_core.extensions.files.tool_support`（ツール共通の処理）のテスト。"""
from __future__ import annotations

import codecs
from unittest.mock import AsyncMock, MagicMock

import pytest

from lilla_core.extensions import files
from lilla_core.extensions.files import tool_support
from lilla_core.extensions.files.access import FileAccessError


class TestDecodeText:
    """テキストとしての読み取り。"""

    def test_utf8(self) -> None:
        """UTF-8 はそのまま読む。"""
        assert tool_support.decode_text("日本語".encode("utf-8")) == "日本語"

    def test_strips_bom(self) -> None:
        """先頭の BOM は取り除く。"""
        assert tool_support.decode_text(codecs.BOM_UTF8 + b"abc") == "abc"

    def test_nul_in_head_is_binary(self) -> None:
        """先頭に NUL があればバイナリ。"""
        with pytest.raises(tool_support.NotTextError):
            tool_support.decode_text(b"abc\x00def")

    def test_nul_after_sniff_window_is_not_checked(self) -> None:
        """NUL を探すのは先頭だけ（後ろの NUL は UTF-8 としては読める）。"""
        raw = b"a" * tool_support.BINARY_SNIFF_BYTES + b"\x00"

        assert tool_support.decode_text(raw).endswith("\x00")

    def test_shift_jis_is_not_text(self) -> None:
        """UTF-8 として読めなければテキストではない。"""
        with pytest.raises(tool_support.NotTextError):
            tool_support.decode_text("日本語".encode("shift_jis"))

    def test_read_text_file_size_limit(self, tmp_path) -> None:
        """上限を超えるファイルは読まない。"""
        path = tmp_path / "big.txt"
        path.write_text("abcdef", encoding="utf-8")

        with pytest.raises(tool_support.FileTooLargeError):
            tool_support.read_text_file(str(path), max_bytes=3)
        assert tool_support.read_text_file(str(path), max_bytes=6) == "abcdef"


class TestRunFileOperation:
    """例外の標準結果辞書への変換。"""

    @pytest.fixture(autouse=True)
    def _config(self, monkeypatch, tmp_path) -> None:
        """設定を `tmp_path` だけのホワイトリストにする。"""
        config = files.FilesConfig(writable_dirs=[str(tmp_path)])
        monkeypatch.setattr(tool_support, "load_config", lambda: config)

    async def test_success_passes_through(self) -> None:
        """操作の結果をそのまま返す。"""
        result = await tool_support.run_file_operation("t", {}, lambda c, r: {"success": True})

        assert result == {"success": True}

    async def test_access_error_message(self) -> None:
        """`FileAccessError` の文言を返す。"""

        def op(config, roots):
            raise FileAccessError("だめです")

        result = await tool_support.run_file_operation("t", {}, op)

        assert result["success"] is False
        assert result["error"] == "だめです"

    @pytest.mark.parametrize(
        ("error", "message"),
        [
            (FileNotFoundError("/secret/path"), tool_support.FILE_NOT_FOUND_MESSAGE),
            (PermissionError("/secret/path"), tool_support.PERMISSION_DENIED_MESSAGE),
        ],
    )
    async def test_expected_os_errors(self, error, message) -> None:
        """不在・権限は短い文言にし、例外の文字列（実体パス）は返さない。"""

        def op(config, roots):
            raise error

        result = await tool_support.run_file_operation("t", {}, op)

        assert result["error"] == message
        assert "/secret/path" not in str(result)

    async def test_unexpected_error_is_notified(self, monkeypatch) -> None:
        """想定外の例外は汎用の文言にして `notify_error` へ流す。"""
        from lilla_core.core import error_notify

        notify = AsyncMock()
        monkeypatch.setattr(error_notify, "notify_error", notify)
        bot = MagicMock()
        boom = OSError(28, "No space left on device: /secret/path")

        def op(config, roots):
            raise boom

        result = await tool_support.run_file_operation("file_write", {"discord_client": bot}, op)

        assert result["success"] is False
        assert result["error"] == tool_support.UNEXPECTED_ERROR_MESSAGE
        assert "secret" not in str(result)
        notify.assert_awaited_once()
        args = notify.await_args.args
        assert args[0] is bot
        assert args[2] is boom

    async def test_unexpected_error_uses_shared_bot(self, monkeypatch) -> None:
        """context に Discord クライアントが無ければ共有インスタンスを使う。"""
        from lilla_core.core import error_notify

        notify = AsyncMock()
        monkeypatch.setattr(error_notify, "notify_error", notify)
        shared = MagicMock()
        monkeypatch.setattr(tool_support, "_resolve_bot", lambda context: shared)

        def op(config, roots):
            raise RuntimeError("boom")

        await tool_support.run_file_operation("t", {}, op)

        assert notify.await_args.args[0] is shared
