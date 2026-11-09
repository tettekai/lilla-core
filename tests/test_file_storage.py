"""`lilla_core.core.file_storage` のテスト。"""
from __future__ import annotations

import pytest

from lilla_core.core import file_storage


class TestCheckWriteMode:
    """`check_write_mode()` の検証。"""

    @pytest.mark.parametrize("mode", file_storage.WRITE_MODES)
    def test_accepts_known_modes(self, mode: str) -> None:
        """既知の `mode` は通る。"""
        file_storage.check_write_mode(mode)

    @pytest.mark.parametrize("mode", ["replace", "", None, "CREATE"])
    def test_rejects_unknown_modes(self, mode) -> None:
        """それ以外は `ValueError`。"""
        with pytest.raises(ValueError, match="mode must be one of"):
            file_storage.check_write_mode(mode)


class TestFileStorageBase:
    """`FileStorage` の基底の振る舞い。"""

    def test_abstract_methods_are_required(self) -> None:
        """抽象メソッドを実装しないとインスタンスを作れない。"""
        with pytest.raises(TypeError):
            file_storage.FileStorage()  # type: ignore[abstract]

    def test_read_only_defaults_to_false(self) -> None:
        """`read_only` の既定は False。"""

        class _Storage(file_storage.FileStorage):
            async def read(self, path):
                return ""

            async def write(self, path, content, mode="overwrite"):
                return file_storage.FileWriteResult(path=path, created=False)

            async def list(self, path=""):
                return []

            async def exists(self, path):
                return False

        assert _Storage().read_only is False

    def test_errors_share_the_base_class(self) -> None:
        """種類ごとの例外はどれも `FileStorageError` で捕まえられる。"""
        for cls in (
            file_storage.FileStoragePathError,
            file_storage.FileStorageNotFoundError,
            file_storage.FileStoragePermissionError,
            file_storage.FileStorageExistsError,
            file_storage.FileStorageNotTextError,
        ):
            assert issubclass(cls, file_storage.FileStorageError)
