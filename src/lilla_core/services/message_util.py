"""メッセージ送信ユーティリティ。

Lilla が主体で送信するメッセージのフラグ解析と Discord への送信を共通化する。
"""
from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lilla_core.repository.conversation_repository import ConversationRepository

logger = logging.getLogger(__name__)

FLAG_NO_HISTORY = "no_history"

_FLAG_PATTERN = re.compile(r"^\[FLAG:([^\]]+)\]")

# === タイムスタンプ除去（conversation_service からも message_splitter からも使う） ===
_TIMESTAMP_PREFIX_RE = re.compile(
    r"^\[(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d{1,2} \d{2}:\d{2}\]\s*"
)

def strip_timestamp_prefix(text: str) -> str:
    """LLM応答の先頭にあるタイムスタンプを除去する。

    分割後の各メッセージ片にも安全に適用できる公開関数。
    """
    if not isinstance(text, str):
        return text
    return _TIMESTAMP_PREFIX_RE.sub("", text, count=1)


def prepend_timestamp_prefix(content, prefix: str):
    """メッセージ content の先頭にタイムスタンプ prefix を付与した新しい content を返す。

    `strip_timestamp_prefix` の対になる操作。str / list（content_parts）の
    いずれにも対応する。

    - str: prefix を前置した新しい文字列を返す
    - list: 先頭の type="text" ブロックの text 先頭に prefix を付与する。
      text ブロックが存在しない場合は先頭に text ブロックを挿入する。
    - それ以外: そのまま返す

    元の content（list / 内包する dict）は変更せず、新しい値を返す。
    """
    if isinstance(content, str):
        return prefix + content
    if isinstance(content, list):
        new_content = list(content)
        for i, block in enumerate(new_content):
            if isinstance(block, dict) and block.get("type") == "text":
                new_content[i] = {**block, "text": prefix + block["text"]}
                return new_content
        return [{"type": "text", "text": prefix}, *new_content]
    return content


# === セッションメモリブロック（memory_manager がシステムプロンプトへ埋め込む形式） ===
SESSION_MEMORY_START = "---SESSION_MEMORY---"
SESSION_MEMORY_END = "---END_SESSION_MEMORY---"

_EXCESS_BLANK_LINES_RE = re.compile(r"\n{3,}")


def format_session_memory_block(content: str) -> str:
    """セッションメモリ本文を規定のブロック形式に整形して返す。

    システムプロンプトへの埋め込み専用の形式であり、LLM 出力のパースには使わない
    （LLM からのセッションメモリ更新は META ブロックの `set_session_memory`
    アクションで受け取る）。
    """
    return f"{SESSION_MEMORY_START}\n{content}\n{SESSION_MEMORY_END}"


# === META ブロック（LLM が出力し、conversation_service が抽出してアクションを適用する） ===
META_START = "---META---"
META_END = "---END_META---"

_META_BLOCK_RE = re.compile(
    r"[ \t]*---META---[ \t]*\r?\n?(.*?)\r?\n?[ \t]*---END_META---[ \t]*",
    re.DOTALL,
)
_CODE_FENCE_RE = re.compile(r"^```[a-zA-Z0-9_-]*[ \t]*\r?\n(.*?)\r?\n?```$", re.DOTALL)


def extract_meta_block(text: str) -> tuple[dict | None, str]:
    """LLM応答から META ブロックを抽出し、(パース済み dict, 除去後テキスト) を返す。

    META ブロックは以下の形式で、中身は JSON オブジェクトとする::

        ---META---
        {"actions": [{"type": "set_session_memory", "value": "健康ノート整理中"}]}
        ---END_META---

    - ブロックなし、またはマーカーが壊れている（開始のみ・終了のみ・順序が逆）場合は
      (None, 元のテキスト) を返す（元のテキストは一切変更しない）
    - ブロックはあるが JSON として解釈できない、または JSON オブジェクトでない場合は
      (None, 除去後テキスト) を返す（壊れた JSON をユーザーに見せないため除去はする）
    - 複数ブロックが存在する場合、内容は最初のブロックのものを採用し、
      全ブロックをテキストから除去する
    - 中身が ```json ... ``` のコードフェンスで囲まれていた場合はフェンスを外して解釈する
    """
    if not isinstance(text, str):
        return None, text
    match = _META_BLOCK_RE.search(text)
    if not match:
        return None, text
    cleaned = _META_BLOCK_RE.sub("", text)
    cleaned = _EXCESS_BLANK_LINES_RE.sub("\n\n", cleaned).strip()

    body = match.group(1).strip()
    fence = _CODE_FENCE_RE.match(body)
    if fence:
        body = fence.group(1).strip()
    if not body:
        return None, cleaned
    try:
        meta = json.loads(body)
    except ValueError as e:
        logger.warning("Failed to parse META block JSON: %s", e)
        return None, cleaned
    if not isinstance(meta, dict):
        logger.warning("META block is not a JSON object: %s", type(meta).__name__)
        return None, cleaned
    return meta, cleaned


# === FrontMatter（外部エージェントとの依頼・結果メッセージで共有する形式） ===
FRONTMATTER_DELIMITER = "---"

_FRONTMATTER_ENTRY_RE = re.compile(r"^([A-Za-z0-9_-]+)\s*:\s*(.*)$")
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def build_correlation_frontmatter(correlation_id: str, body: str) -> str:
    """correlation_id のみを持つ FrontMatter 付きメッセージを組み立てる。

    外部エージェントへの依頼メッセージで使う形式で、結果メッセージも同じ形式で
    返ってくることを前提とする::

        ---
        correlation_id: a1b2c3d4-e5f6-7890-abcd-ef1234567890
        ---

        ここに本文を書く
    """
    return (
        f"{FRONTMATTER_DELIMITER}\n"
        f"correlation_id: {correlation_id}\n"
        f"{FRONTMATTER_DELIMITER}\n\n"
        f"{body.strip()}"
    )


def parse_correlation_frontmatter(text: str) -> tuple[str | None, str]:
    """FrontMatter 付きメッセージから (correlation_id, 本文) を返す。

    ``build_correlation_frontmatter`` の対になる操作。外部エージェントが独自の
    ヘッダー行を先頭に付けてくる場合に備え、``extract_command_content`` と同様に
    最初の ``---`` 行から FrontMatter の解釈を開始する。

    - FrontMatter が見つからない、終端 ``---`` がない、``key: value`` 形式でない行が
      含まれる場合は ``(None, 元のテキスト)`` を返す
    - ``correlation_id`` が無い、または UUID 形式でない場合も ``(None, 元のテキスト)``
      を返す（コマンド文字列へ変換する際の不正な値の混入を防ぐ）
    - 解釈できた場合は ``(correlation_id, FrontMatter を除いた本文)`` を返す
    """
    if not isinstance(text, str):
        return None, text

    lines = text.split("\n")
    start = next(
        (i for i, line in enumerate(lines) if line.strip() == FRONTMATTER_DELIMITER),
        None,
    )
    if start is None:
        return None, text

    correlation_id: str | None = None
    for i in range(start + 1, len(lines)):
        line = lines[i].strip()
        if line == FRONTMATTER_DELIMITER:
            if correlation_id is None:
                return None, text
            return correlation_id, "\n".join(lines[i + 1:]).strip()
        if not line:
            continue
        entry = _FRONTMATTER_ENTRY_RE.match(line)
        if entry is None:
            return None, text
        if entry.group(1) == "correlation_id":
            value = entry.group(2).strip().strip('"').strip("'")
            if not _UUID_RE.match(value):
                logger.warning("correlation_id is not in UUID format: %r", value)
                return None, text
            correlation_id = value
    return None, text


def parse_message_flags(raw: str) -> tuple[set[str], str]:
    """メッセージ先頭の [FLAG:xxx] を解析し、(フラグセット, クリーン本文) を返す。

    - 複数フラグ対応: "[FLAG:no_history][FLAG:xxx]本文"
    - 未知のフラグは無視し、クリーン本文から除去する
    - フラグが存在しない場合は (空セット, 元のメッセージ) を返す
    """
    flags: set[str] = set()
    text = raw
    while True:
        m = _FLAG_PATTERN.match(text)
        if not m:
            break
        flags.add(m.group(1))
        text = text[m.end():]
    return flags, text


async def resolve_dm_channel(discord_client, user_id):
    """user_id からユーザーの DM チャンネルを解決する。

    まずキャッシュ（``get_user``、API 呼び出しなし）を試し、ミスした場合のみ
    ``fetch_user``（API 問い合わせ）でユーザーを取得してから ``create_dm()`` で
    DM チャンネルを解決する。Bot 再起動直後などキャッシュミスが起きやすい状況でも
    確実に解決するための cache-then-fetch は ``resolve_discord_channel``（チャンネル
    解決）と同じ方針で、``send_to_discord`` の ``dm:`` 経路と ``!toolresult`` の
    オーナー DM フォールバック配送で共有する。

    Args:
        discord_client: ``get_user`` / ``fetch_user`` を持つ Discord クライアント。
        user_id: DM 送信先のユーザー ID（int / str のいずれでも可）。

    Returns:
        解決した DM チャンネル。``User.create_dm`` は DM チャンネルがキャッシュ済み
        ならそれを返す冪等な処理のため、繰り返し呼んでも同じチャンネルを返す。
    """
    uid = int(user_id)
    user = discord_client.get_user(uid) or await discord_client.fetch_user(uid)
    return await user.create_dm()


async def send_to_discord(
    discord_client,
    target: str,
    raw_message: str,
    repo: "ConversationRepository | None" = None,
) -> None:
    """フラグ解析 → Discord へ送信 → 条件付きで会話履歴保存。

    - target: "dm:USER_ID" or "channel:CHANNEL_ID" 形式
    - repo=None の場合は保存処理をスキップ
    - FLAG_NO_HISTORY がある場合は保存しない
    """
    flags, clean_message = parse_message_flags(raw_message)

    if target.startswith("dm:"):
        channel = await resolve_dm_channel(discord_client, target[3:])
    elif target.startswith("channel:"):
        channel_id = int(target[8:])
        channel = discord_client.get_channel(channel_id)
    else:
        raise ValueError(f"Invalid target format: {target}")

    await channel.send(clean_message.strip())

    if repo is not None and FLAG_NO_HISTORY not in flags:
        msg = {"role": "assistant", "content": clean_message.strip()}
        await repo.save(msg)
