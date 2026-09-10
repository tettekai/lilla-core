"""src/utils/content_hash.py のテスト。"""
from __future__ import annotations

import hashlib

from lilla_core.utils.content_hash import compute_content_hash, normalize_newlines


class TestNormalizeNewlines:
    """normalize_newlines のテスト。"""

    def test_crlf_is_converted_to_lf(self) -> None:
        """CRLF が LF に変換されること。"""
        assert normalize_newlines("a\r\nb\r\n") == "a\nb\n"

    def test_lone_cr_is_converted_to_lf(self) -> None:
        """単独の CR が LF に変換されること。"""
        assert normalize_newlines("a\rb") == "a\nb"

    def test_lf_only_text_is_unchanged(self) -> None:
        """LF のみのテキストは変化しないこと。"""
        assert normalize_newlines("a\nb\n") == "a\nb\n"


class TestComputeContentHash:
    """compute_content_hash のテスト。"""

    def test_returns_sha256_of_normalized_utf8_bytes(self) -> None:
        """改行正規化後の UTF-8 バイト列の SHA-256 が返ること。"""
        content = "# 見出し\n本文\n"
        expected = hashlib.sha256(content.encode("utf-8")).hexdigest()

        assert compute_content_hash(content) == expected

    def test_crlf_and_lf_produce_same_hash(self) -> None:
        """改行コードが CRLF か LF かでハッシュが揺れないこと。"""
        lf = "# 見出し\n本文\n末尾\n"
        crlf = "# 見出し\r\n本文\r\n末尾\r\n"

        assert compute_content_hash(lf) == compute_content_hash(crlf)

    def test_cr_only_produces_same_hash(self) -> None:
        """CR のみの改行でも LF と同じハッシュになること。"""
        lf = "行1\n行2\n"
        cr = "行1\r行2\r"

        assert compute_content_hash(lf) == compute_content_hash(cr)

    def test_different_content_produces_different_hash(self) -> None:
        """内容が異なればハッシュも異なること。"""
        assert compute_content_hash("A") != compute_content_hash("B")
