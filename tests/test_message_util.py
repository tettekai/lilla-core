"""message_util.py のテスト。"""
from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lilla_core.services.message_util import (
    FLAG_NO_HISTORY,
    build_correlation_frontmatter,
    extract_meta_block,
    format_session_memory_block,
    parse_correlation_frontmatter,
    parse_message_flags,
    prepend_timestamp_prefix,
    resolve_dm_channel,
    send_to_discord,
    strip_timestamp_prefix,
)

sys.path.insert(0, "src")

_UUID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"


class TestParseMessageFlags:
    def test_no_flags(self):
        """フラグなしの場合、空セットと元のメッセージを返す。"""
        flags, text = parse_message_flags("おはようございます")
        assert flags == set()
        assert text == "おはようございます"

    def test_single_flag(self):
        """単一フラグを正しく解析する。"""
        flags, text = parse_message_flags("[FLAG:no_history]本文です")
        assert flags == {FLAG_NO_HISTORY}
        assert text == "本文です"

    def test_multiple_flags(self):
        """複数フラグを正しく解析する。"""
        flags, text = parse_message_flags("[FLAG:no_history][FLAG:other]本文")
        assert flags == {"no_history", "other"}
        assert text == "本文"

    def test_unknown_flag_removed(self):
        """未知のフラグはクリーン本文から除去される。"""
        flags, text = parse_message_flags("[FLAG:unknown]メッセージ")
        assert "unknown" in flags
        assert text == "メッセージ"

    def test_empty_string(self):
        """空文字列はそのまま返す。"""
        flags, text = parse_message_flags("")
        assert flags == set()
        assert text == ""

    def test_flag_only_no_body(self):
        """フラグのみで本文なしの場合。"""
        flags, text = parse_message_flags("[FLAG:no_history]")
        assert flags == {FLAG_NO_HISTORY}
        assert text == ""


class TestPrependTimestampPrefix:
    def test_str_content(self):
        """str content には prefix を前置する。"""
        assert prepend_timestamp_prefix("hello", "[Apr 2 10:00] ") == "[Apr 2 10:00] hello"

    def test_list_prefixes_first_text_block(self):
        """list content では先頭の text ブロックにのみ prefix を付与する。"""
        content = [
            {"type": "image_url", "image_url": {"url": "x"}},
            {"type": "text", "text": "hello"},
            {"type": "text", "text": "world"},
        ]
        result = prepend_timestamp_prefix(content, "[P] ")
        assert result[0] == {"type": "image_url", "image_url": {"url": "x"}}
        assert result[1] == {"type": "text", "text": "[P] hello"}
        # 2 番目以降の text ブロックは変更しない
        assert result[2] == {"type": "text", "text": "world"}

    def test_list_inserts_text_block_when_absent(self):
        """text ブロックがない list content には先頭に text ブロックを挿入する。"""
        content = [{"type": "image_url", "image_url": {"url": "x"}}]
        result = prepend_timestamp_prefix(content, "[P] ")
        assert result[0] == {"type": "text", "text": "[P] "}
        assert result[1] == {"type": "image_url", "image_url": {"url": "x"}}

    def test_does_not_mutate_original_list(self):
        """元の list とその内包 dict を変更しない。"""
        content = [{"type": "text", "text": "hello"}]
        original_block = content[0]
        result = prepend_timestamp_prefix(content, "[P] ")
        assert content == [{"type": "text", "text": "hello"}]
        assert original_block == {"type": "text", "text": "hello"}
        assert result is not content

    def test_non_str_non_list_returned_as_is(self):
        """str / list 以外はそのまま返す。"""
        assert prepend_timestamp_prefix(None, "[P] ") is None
        sentinel = {"role": "user"}
        assert prepend_timestamp_prefix(sentinel, "[P] ") is sentinel

    def test_roundtrip_with_strip(self):
        """付与したタイムスタンプは strip_timestamp_prefix で除去できる（str の往復）。"""
        prefix = "[Apr 2 10:00] "
        stamped = prepend_timestamp_prefix("hello", prefix)
        assert strip_timestamp_prefix(stamped) == "hello"


class TestFormatSessionMemoryBlock:
    def test_wraps_content_in_markers(self):
        """内容を START/END マーカーで挟んだ文字列を返す（システムプロンプト埋め込み形式）。"""
        result = format_session_memory_block("作業中: XXXを実装中")
        assert result == "---SESSION_MEMORY---\n作業中: XXXを実装中\n---END_SESSION_MEMORY---"

    def test_multiline_content_wrapped_as_is(self):
        """複数行の内容もそのままマーカーで挟まれる。"""
        result = format_session_memory_block("1行目\n2行目")
        assert result == "---SESSION_MEMORY---\n1行目\n2行目\n---END_SESSION_MEMORY---"


class TestExtractMetaBlock:
    def test_no_block_returns_none_and_unchanged_text(self):
        """ブロックがない場合は (None, 元のテキスト) を返す。"""
        text = "こんにちは、元気ですか？"
        meta, cleaned = extract_meta_block(text)
        assert meta is None
        assert cleaned == text

    def test_block_only(self):
        """ブロックのみの場合、JSON がパースされ除去後は空文字列になる。"""
        text = '---META---\n{"actions": [{"type": "set_session_memory", "value": "X"}]}\n---END_META---'
        meta, cleaned = extract_meta_block(text)
        assert meta == {"actions": [{"type": "set_session_memory", "value": "X"}]}
        assert cleaned == ""

    def test_text_before_and_after_block_preserved(self):
        """ブロック前後のテキストは保持され、ブロックのみ除去される。"""
        text = (
            "前置きです。\n\n"
            '---META---\n{"actions": []}\n---END_META---'
            "\n\n続きです。"
        )
        meta, cleaned = extract_meta_block(text)
        assert meta == {"actions": []}
        assert "前置きです。" in cleaned
        assert "続きです。" in cleaned
        assert "META" not in cleaned
        assert "\n\n\n" not in cleaned

    def test_pretty_printed_json_parsed(self):
        """整形された複数行 JSON もパースできる。"""
        text = (
            "---META---\n"
            "{\n"
            '  "actions": [\n'
            '    {"type": "set_session_memory", "value": "健康ノート整理中"}\n'
            "  ]\n"
            "}\n"
            "---END_META---"
        )
        meta, _cleaned = extract_meta_block(text)
        assert meta["actions"][0]["value"] == "健康ノート整理中"

    def test_code_fenced_json_parsed(self):
        """```json コードフェンスで囲まれた JSON もパースできる。"""
        text = (
            "---META---\n"
            "```json\n"
            '{"actions": [{"type": "set_session_memory", "value": "Y"}]}\n'
            "```\n"
            "---END_META---"
        )
        meta, cleaned = extract_meta_block(text)
        assert meta == {"actions": [{"type": "set_session_memory", "value": "Y"}]}
        assert cleaned == ""

    def test_invalid_json_returns_none_but_strips_block(self):
        """JSON が壊れている場合は (None, ブロック除去後テキスト) を返す。"""
        text = "返信です。\n\n---META---\nこれはJSONではない\n---END_META---"
        meta, cleaned = extract_meta_block(text)
        assert meta is None
        assert cleaned == "返信です。"

    def test_non_object_json_returns_none_but_strips_block(self):
        """JSON がオブジェクトでない場合は (None, ブロック除去後テキスト) を返す。"""
        text = "返信です。\n\n---META---\n[1, 2, 3]\n---END_META---"
        meta, cleaned = extract_meta_block(text)
        assert meta is None
        assert cleaned == "返信です。"

    def test_empty_body_returns_none_but_strips_block(self):
        """中身が空のブロックは (None, ブロック除去後テキスト) を返す。"""
        text = "返信です。\n\n---META---\n   \n---END_META---"
        meta, cleaned = extract_meta_block(text)
        assert meta is None
        assert cleaned == "返信です。"

    def test_start_marker_without_end_is_treated_as_no_block(self):
        """開始マーカーのみで終了マーカーがない場合はパース失敗扱い。"""
        text = '---META---\n{"actions": []}'
        meta, cleaned = extract_meta_block(text)
        assert meta is None
        assert cleaned == text

    def test_end_marker_without_start_is_treated_as_no_block(self):
        """終了マーカーのみで開始マーカーがない場合はパース失敗扱い。"""
        text = '{"actions": []}\n---END_META---'
        meta, cleaned = extract_meta_block(text)
        assert meta is None
        assert cleaned == text

    def test_end_before_start_is_treated_as_no_block(self):
        """マーカーの順序が逆の場合はパース失敗扱い。"""
        text = '---END_META---\n{"actions": []}\n---META---'
        meta, cleaned = extract_meta_block(text)
        assert meta is None
        assert cleaned == text

    def test_non_str_input_returns_none_and_input_as_is(self):
        """str 以外の入力は (None, 入力そのまま) を返す。"""
        meta, cleaned = extract_meta_block(None)
        assert meta is None
        assert cleaned is None

    def test_multiple_blocks_first_content_all_markers_removed(self):
        """複数ブロックがある場合、最初のブロックを採用し全ブロックを除去する。"""
        text = (
            '---META---\n{"actions": [{"type": "a"}]}\n---END_META---\n\n'
            '---META---\n{"actions": [{"type": "b"}]}\n---END_META---'
        )
        meta, cleaned = extract_meta_block(text)
        assert meta == {"actions": [{"type": "a"}]}
        assert "META" not in cleaned

    def test_session_memory_block_in_text_is_not_parsed(self):
        """旧 SESSION_MEMORY ブロックは META ブロックとして解釈されない。"""
        text = "返信です。\n\n---SESSION_MEMORY---\n作業中: XXX\n---END_SESSION_MEMORY---"
        meta, cleaned = extract_meta_block(text)
        assert meta is None
        assert cleaned == text


class TestResolveDmChannel:
    @pytest.mark.asyncio
    async def test_uses_cache_when_available(self):
        """get_user がヒットしたら fetch_user を呼ばずに DM チャンネルを返す。"""
        dm_channel = MagicMock()
        user = MagicMock()
        user.create_dm = AsyncMock(return_value=dm_channel)
        client = MagicMock()
        client.get_user = MagicMock(return_value=user)
        client.fetch_user = AsyncMock()

        result = await resolve_dm_channel(client, "12345")

        assert result is dm_channel
        client.get_user.assert_called_once_with(12345)
        client.fetch_user.assert_not_called()

    @pytest.mark.asyncio
    async def test_falls_back_to_fetch_on_cache_miss(self):
        """get_user がミスしたら fetch_user でユーザーを取得して DM チャンネルを返す。"""
        dm_channel = MagicMock()
        user = MagicMock()
        user.create_dm = AsyncMock(return_value=dm_channel)
        client = MagicMock()
        client.get_user = MagicMock(return_value=None)
        client.fetch_user = AsyncMock(return_value=user)

        result = await resolve_dm_channel(client, 12345)

        assert result is dm_channel
        client.get_user.assert_called_once_with(12345)
        client.fetch_user.assert_called_once_with(12345)


class TestSendToDiscord:
    def _make_discord_client(self):
        """テスト用の Discord クライアントモックを生成する。"""
        client = MagicMock()
        channel = MagicMock()
        channel.send = AsyncMock()
        user = MagicMock()
        user.create_dm = AsyncMock(return_value=channel)
        # 既定はキャッシュミス → fetch_user フォールバックの経路を模倣する。
        client.get_user = MagicMock(return_value=None)
        client.fetch_user = AsyncMock(return_value=user)
        client.get_channel = MagicMock(return_value=channel)
        return client, channel

    @pytest.mark.asyncio
    async def test_dm_target_sends_message(self):
        """dm: ターゲットで DM チャンネルにメッセージを送信する（キャッシュミス経路）。"""
        client, channel = self._make_discord_client()
        await send_to_discord(client, "dm:12345", "こんにちは")
        client.get_user.assert_called_once_with(12345)
        client.fetch_user.assert_called_once_with(12345)
        channel.send.assert_called_once_with("こんにちは")

    @pytest.mark.asyncio
    async def test_dm_target_uses_cache_when_available(self):
        """dm: ターゲットで get_user がヒットしたら fetch_user は呼ばない。"""
        client, channel = self._make_discord_client()
        cached_user = MagicMock()
        cached_user.create_dm = AsyncMock(return_value=channel)
        client.get_user = MagicMock(return_value=cached_user)

        await send_to_discord(client, "dm:12345", "こんにちは")

        client.get_user.assert_called_once_with(12345)
        client.fetch_user.assert_not_called()
        channel.send.assert_called_once_with("こんにちは")

    @pytest.mark.asyncio
    async def test_channel_target_sends_message(self):
        """channel: ターゲットでチャンネルにメッセージを送信する。"""
        client, channel = self._make_discord_client()
        await send_to_discord(client, "channel:99999", "テスト")
        client.get_channel.assert_called_once_with(99999)
        channel.send.assert_called_once_with("テスト")

    @pytest.mark.asyncio
    async def test_invalid_target_raises(self):
        """不正な target 形式は ValueError を送出する。"""
        client, _ = self._make_discord_client()
        with pytest.raises(ValueError):
            await send_to_discord(client, "invalid:123", "メッセージ")

    @pytest.mark.asyncio
    async def test_saves_to_repo_without_flag(self):
        """FLAG_NO_HISTORY なしの場合、repo.save が呼ばれる。"""
        client, channel = self._make_discord_client()
        repo = MagicMock()
        repo.save = AsyncMock()

        await send_to_discord(client, "channel:1", "保存されるメッセージ", repo=repo)

        repo.save.assert_called_once_with(
            {"role": "assistant", "content": "保存されるメッセージ"}
        )

    @pytest.mark.asyncio
    async def test_no_save_with_no_history_flag(self):
        """FLAG_NO_HISTORY がある場合、repo.save は呼ばれない。"""
        client, channel = self._make_discord_client()
        repo = MagicMock()
        repo.save = AsyncMock()

        await send_to_discord(client, "channel:1", "[FLAG:no_history]保存しない", repo=repo)

        channel.send.assert_called_once_with("保存しない")
        repo.save.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_repo_skips_save(self):
        """repo=None の場合は保存処理をスキップする。"""
        client, channel = self._make_discord_client()
        # 例外が発生しないことを確認
        await send_to_discord(client, "channel:1", "メッセージ", repo=None)
        channel.send.assert_called_once_with("メッセージ")

    @pytest.mark.asyncio
    async def test_strips_whitespace_before_send(self):
        """送信前に前後の空白を strip する。"""
        client, channel = self._make_discord_client()
        await send_to_discord(client, "channel:1", "  スペース付き  ")
        channel.send.assert_called_once_with("スペース付き")


class TestBuildCorrelationFrontmatter:
    def test_builds_frontmatter_message(self):
        """correlation_id 付き FrontMatter と本文を組み立てる。"""
        result = build_correlation_frontmatter(_UUID, "調べてほしいこと")
        assert result == f"---\ncorrelation_id: {_UUID}\n---\n\n調べてほしいこと"

    def test_strips_body_whitespace(self):
        """本文の前後の空白は除去される。"""
        result = build_correlation_frontmatter(_UUID, "\n\n本文\n\n")
        assert result.endswith("---\n\n本文")

    def test_roundtrips_with_parser(self):
        """組み立てたメッセージはパーサーで元に戻せる。"""
        message = build_correlation_frontmatter(_UUID, "本文\n複数行")
        assert parse_correlation_frontmatter(message) == (_UUID, "本文\n複数行")


class TestParseCorrelationFrontmatter:
    def test_parses_correlation_id_and_body(self):
        """FrontMatter から correlation_id と本文を取り出す。"""
        text = f"---\ncorrelation_id: {_UUID}\n---\n\n結果本文\n2行目"
        assert parse_correlation_frontmatter(text) == (_UUID, "結果本文\n2行目")

    def test_parses_with_leading_header_lines(self):
        """外部エージェントが付ける先頭のヘッダー行があっても解釈できる。"""
        text = (
            "Agent Response: web-search\n"
            f"---\ncorrelation_id: {_UUID}\n---\n本文"
        )
        assert parse_correlation_frontmatter(text) == (_UUID, "本文")

    def test_ignores_quotes_around_value(self):
        """値がクォートされていても correlation_id を取り出せる。"""
        text = f'---\ncorrelation_id: "{_UUID}"\n---\n本文'
        assert parse_correlation_frontmatter(text) == (_UUID, "本文")

    def test_allows_extra_keys(self):
        """未知のキーが含まれていても correlation_id を取り出せる。"""
        text = f"---\nfrom: agent\ncorrelation_id: {_UUID}\n---\n本文"
        assert parse_correlation_frontmatter(text) == (_UUID, "本文")

    def test_returns_none_without_frontmatter(self):
        """FrontMatter がない場合は (None, 元テキスト) を返す。"""
        assert parse_correlation_frontmatter("ただの会話") == (None, "ただの会話")

    def test_returns_none_without_correlation_id(self):
        """correlation_id を含まない FrontMatter は対象外。"""
        text = "---\ntitle: メモ\n---\n本文"
        assert parse_correlation_frontmatter(text) == (None, text)

    def test_returns_none_when_terminator_missing(self):
        """終端の --- がない場合は対象外。"""
        text = f"---\ncorrelation_id: {_UUID}\n本文"
        assert parse_correlation_frontmatter(text) == (None, text)

    def test_returns_none_for_invalid_uuid(self):
        """UUID 形式でない correlation_id は受け付けない。"""
        text = "---\ncorrelation_id: not-a-uuid\n---\n本文"
        assert parse_correlation_frontmatter(text) == (None, text)

    def test_returns_none_for_markdown_horizontal_rule(self):
        """本文中の水平線を FrontMatter と誤認しない。"""
        text = "見出し\n---\nただの本文です\n"
        assert parse_correlation_frontmatter(text) == (None, text)

    def test_returns_none_for_non_string(self):
        """文字列以外はそのまま返す。"""
        assert parse_correlation_frontmatter(None) == (None, None)

    def test_empty_body_returns_empty_string(self):
        """本文が空の場合は空文字列を返す。"""
        text = f"---\ncorrelation_id: {_UUID}\n---\n"
        assert parse_correlation_frontmatter(text) == (_UUID, "")
