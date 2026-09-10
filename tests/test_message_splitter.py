"""message_splitter.py のテスト。"""
from __future__ import annotations

import sys
from pathlib import Path

from lilla_core.services.message_splitter import split_response


sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


class TestSplitResponse:
    def test_single_block_no_split(self) -> None:
        """\\n\\n を含まない場合は1要素のリストを返す。"""
        assert split_response("こんにちは") == ["こんにちは"]

    def test_splits_on_double_newline(self) -> None:
        """\\n\\n で分割して複数ブロックを返す。"""
        assert split_response("ブロック1\n\nブロック2") == ["ブロック1", "ブロック2"]

    def test_strips_whitespace_from_blocks(self) -> None:
        """各ブロックの前後の空白を除去する。"""
        assert split_response("  hello  \n\n  world  ") == ["hello", "world"]

    def test_removes_empty_blocks(self) -> None:
        """空のブロック（空白のみを含む）は除去する。"""
        assert split_response("A\n\n\n\nB") == ["A", "B"]

    def test_empty_string_returns_empty_list(self) -> None:
        """空文字列は空リストを返す。"""
        assert split_response("") == []

    def test_whitespace_only_returns_empty_list(self) -> None:
        """空白のみの文字列は空リストを返す。"""
        assert split_response("   \n\n   ") == []

    def test_three_blocks(self) -> None:
        """3ブロック以上も正しく分割する。"""
        assert split_response("A\n\nB\n\nC") == ["A", "B", "C"]

    # --- 意味なし判定（除外されるべきブロック） ---

    def test_skips_hyphen_separator(self) -> None:
        """ハイフンだけのブロックをスキップする。"""
        assert split_response("A\n\n---\n\nB") == ["A", "B"]

    def test_skips_asterisk_separator(self) -> None:
        """アスタリスクだけのブロックをスキップする。"""
        assert split_response("A\n\n***\n\nB") == ["A", "B"]

    def test_skips_underscore_separator(self) -> None:
        """アンダースコアだけのブロックをスキップする。"""
        assert split_response("A\n\n___\n\nB") == ["A", "B"]

    def test_skips_single_separator_char(self) -> None:
        """1文字のセパレータ（-）もスキップする。"""
        assert split_response("A\n\n-\n\nB") == ["A", "B"]

    def test_skips_code_fence(self) -> None:
        """コードフェンス（```）をスキップする。"""
        assert split_response("A\n\n```\n\nB") == ["A", "B"]

    def test_skips_equal_separator(self) -> None:
        """イコール区切り（===）をスキップする。"""
        assert split_response("A\n\n===\n\nB") == ["A", "B"]

    def test_skips_tilde_separator(self) -> None:
        """チルダ区切り（~~~）をスキップする。"""
        assert split_response("A\n\n~~~\n\nB") == ["A", "B"]

    def test_skips_punctuation_only_block(self) -> None:
        """句読点のみのブロックをスキップする。"""
        assert split_response("A\n\n•••\n\nB") == ["A", "B"]
        assert split_response("A\n\n※※※\n\nB") == ["A", "B"]

    # --- 意味あり判定（残るべきブロック） ---

    def test_keeps_japanese_text(self) -> None:
        """日本語テキストは残す。"""
        assert split_response("こんにちは") == ["こんにちは"]

    def test_keeps_english_text(self) -> None:
        """英語テキストは残す。"""
        assert split_response("Hello") == ["Hello"]

    def test_keeps_number_only_block(self) -> None:
        """数字のみのブロックは残す。"""
        assert split_response("42") == ["42"]

    def test_keeps_emoji_with_japanese(self) -> None:
        """絵文字＋日本語の混在ブロックは残す。"""
        assert split_response("✨ おはよう") == ["✨ おはよう"]

    def test_keeps_symbol_with_english(self) -> None:
        """記号＋英字の混在ブロックは残す。"""
        assert split_response("- item A") == ["- item A"]

    def test_does_not_skip_text_with_separator_chars(self) -> None:
        """セパレータ文字を含んでも他の文字があればスキップしない。"""
        assert split_response("A\n\n-- 見出し --\n\nB") == ["A", "-- 見出し --", "B"]

    def test_keeps_cyrillic_text(self) -> None:
        """キリル文字のみのブロックは残す。"""
        assert split_response("Привет") == ["Привет"]

    def test_keeps_hangul_text(self) -> None:
        """ハングルのみのブロックは残す。"""
        assert split_response("안녕") == ["안녕"]

    def test_keeps_greek_text(self) -> None:
        """ギリシャ文字のみのブロックは残す。"""
        assert split_response("Γειά") == ["Γειά"]

    def test_keeps_arabic_text(self) -> None:
        """アラビア文字のみのブロックは残す。"""
        assert split_response("مرحبا") == ["مرحبا"]

    def test_keeps_thai_text(self) -> None:
        """タイ文字のみのブロックは残す。"""
        assert split_response("สวัสดี") == ["สวัสดี"]

    def test_keeps_hebrew_text(self) -> None:
        """ヘブライ文字のみのブロックは残す。"""
        assert split_response("שלום") == ["שלום"]

    def test_keeps_emoji_only_block(self) -> None:
        """絵文字のみのブロックは残す。"""
        assert split_response("😀🎉") == ["😀🎉"]

    def test_keeps_decorative_line_block(self) -> None:
        """罫線のみのブロックは意図的に残す（Symbolカテゴリは絞り込まない）。"""
        assert split_response("────") == ["────"]

    def test_keeps_decorative_star_block(self) -> None:
        """装飾記号（★）のみのブロックは意図的に残す（Symbolカテゴリは絞り込まない）。"""
        assert split_response("★★★") == ["★★★"]

    # --- 統合ケース ---

    def test_separator_at_start_and_end(self) -> None:
        """先頭・末尾のセパレータブロックもスキップする。"""
        assert split_response("---\n\nA\n\n---") == ["A"]

    def test_multiple_separators_between_content(self) -> None:
        """本文の前後にセパレータが挟まれた入力から本文ブロックのみが返る。"""
        text = "こんにちは\n\n---\n\n今日もよろしく\n\n```\n\nまたね"
        assert split_response(text) == ["こんにちは", "今日もよろしく", "またね"]

    def test_multiple_content_blocks_in_order(self) -> None:
        """複数の本文ブロックが正しい順序で返る。"""
        text = "first\n\n===\n\nsecond\n\n~~~\n\nthird"
        assert split_response(text) == ["first", "second", "third"]
