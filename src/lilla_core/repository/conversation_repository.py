from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache

from bson import ObjectId
from lilla_core.repository.mongo_client import create_mongo_client
from pymongo import ASCENDING, DESCENDING

from lilla_core.core.exceptions import ConversationNotFoundError
from lilla_core.utils.datetime_utils import utc_now

#: リアクション付きのドキュメントだけを対象にした部分インデックスの名前。
#: `list_reacted` / `count_reactions` はこの名前で hint を渡して使わせる。
REACTIONS_INDEX_NAME = "reactions_time_id"

#: `discord_message_ids` から会話履歴を引くためのインデックスの名前。
DISCORD_MESSAGE_IDS_INDEX_NAME = "discord_message_ids"


@dataclass(frozen=True)
class ReactionEmoji:
    """リアクションの絵文字。

    標準の絵文字は `name` に文字そのもの（例: `❤️`）を持ち、`id` は `None`。
    Discord のカスタム絵文字は `name` に名前、`id` に絵文字 ID を持つ。
    `:heart:` のようなショートコードは入力上の表記なので使わない。
    異体字（`❤` と `❤️` など）は正規化せず、別の絵文字として扱う。

    Attributes:
        name: 標準の絵文字なら文字そのもの、カスタム絵文字なら名前。
        id: カスタム絵文字の ID。標準の絵文字なら `None`。
    """

    name: str
    id: int | None = None

    def match_query(self) -> dict:
        """`reactions` の要素から同じ絵文字を探す条件を返す。

        カスタム絵文字は ID で同一性を判定する（名前は変わりうるため）。標準の絵文字は
        `emoji` が一致し、`emoji_id` を持たない要素を同じ絵文字とみなす。
        """
        if self.id is not None:
            return {"emoji_id": self.id}
        return {"emoji": self.name, "emoji_id": None}

    def to_document(self) -> dict:
        """`reactions` の要素に保存する絵文字の部分（`emoji` と、カスタムなら `emoji_id`）を返す。"""
        doc: dict = {"emoji": self.name}
        if self.id is not None:
            doc["emoji_id"] = self.id
        return doc


def _reactions_of(doc: dict) -> list[dict]:
    """ドキュメントの `reactions` を返す（無い・壊れている場合は空リスト）。"""
    reactions = doc.get("reactions")
    return list(reactions) if isinstance(reactions, list) else []


@dataclass(frozen=True)
class SavedConversation:
    """保存した会話履歴エントリの識別情報。

    Attributes:
        id: 保存したドキュメントの `_id`（ObjectId の文字列）。`list_for_client` の
            `_id` と同じ値で、`after_id` や `delete()` にそのまま渡せる。
        time: 保存したドキュメントの `time`（UTC の timezone-aware な datetime。
            ミリ秒精度）。`list_for_client` が返す同じエントリの `time` と `==` で一致し、
            `since` にそのまま渡せる。
    """

    id: str
    time: datetime


def _storage_now() -> datetime:
    """会話履歴の `time` に保存する現在時刻（UTC・ミリ秒精度）を返す。

    MongoDB（BSON の日時型）はミリ秒精度で保存するため、`utc_now()` のマイクロ秒を
    ミリ秒単位に切り捨てておく。こうすると保存前の値と読み出した値が `==` で一致する。
    """
    now = utc_now()
    return now.replace(microsecond=now.microsecond // 1000 * 1000)


@lru_cache(maxsize=1)
def get_conversation_repo() -> "ConversationRepository":
    """ConversationRepository のシングルトンインスタンスを返す。

    初回呼び出し時に設定を読み込んでインスタンスを生成し、以降は同じインスタンスを返す。

    Returns
    -------
    ConversationRepository
        ConversationRepository のインスタンス
    """
    from lilla_core.core.config import get_config
    config = get_config()
    return ConversationRepository(
        config.env.mongodb_uri, config.mongodb.db_name, config.memory.conversation_ttl_hours
    )


class ConversationRepository:
    def __init__(self, mongo_uri: str, db_name: str, ttl_hours: int = 72) -> None:
        client = create_mongo_client(mongo_uri)
        self._collection = client[db_name]["conversations"]
        self._ttl_hours = ttl_hours

    async def init_collection(self) -> None:
        """コレクションの初期化を行う（起動時の一元初期化用の薄いオーケストレーション層）。"""
        await self.ensure_indexes()

    async def ensure_indexes(self) -> None:
        """インデックスを作成します。ttl_hours が 0 の場合は TTL なしの通常インデックスを作成します。

        あわせて `(discord_channel_id, time)` の複合インデックスを作成します
        （チャンネル単位・期間指定の取得に使います。`discord_channel_id` を持たない
        既存ドキュメントも通常のインデックスに含まれます）。
        `list_for_client` の `(time, _id)` の並び替え・差分取得用に `(time, _id)` の
        複合インデックスも作成します（TTL は `time` の単一インデックスが担います）。
        """
        if self._ttl_hours == 0:
            await self._collection.create_index([("time", ASCENDING)])
        else:
            await self._collection.create_index(
                [("time", ASCENDING)],
                expireAfterSeconds=self._ttl_hours * 3600,
            )
        await self._collection.create_index(
            [("discord_channel_id", ASCENDING), ("time", ASCENDING)],
        )
        await self._collection.create_index(
            [("time", ASCENDING), ("_id", ASCENDING)],
        )
        # リアクション付きのメッセージは全体のごく一部なので、`reactions` を持つ
        # ドキュメントだけを対象にした部分インデックスにする（取得・集計が
        # コレクション全体の走査にならないようにするため）。既存の `(time, _id)` と
        # キーの向きを変えて別のインデックスとして作る。
        await self._collection.create_index(
            [("time", DESCENDING), ("_id", DESCENDING)],
            name=REACTIONS_INDEX_NAME,
            partialFilterExpression={"reactions": {"$exists": True}},
        )
        # Discord のリアクションイベントから会話履歴を引くため。返信を送った
        # メッセージにしか付かないので、持つドキュメントだけを対象にする。
        await self._collection.create_index(
            [("discord_message_ids", ASCENDING)],
            name=DISCORD_MESSAGE_IDS_INDEX_NAME,
            partialFilterExpression={"discord_message_ids": {"$exists": True}},
        )

    async def load(self, max_turns: int) -> list[dict]:
        """会話履歴を新しい順に最大 max_turns 件取得し、時系列順に返します。"""
        cursor = self._collection.find(
            {},
            {"_id": 0, "message": 1, "time": 1},
        ).sort("time", DESCENDING).limit(max_turns)
        docs = await cursor.to_list(length=max_turns)
        docs.reverse()
        return [doc["message"] for doc in docs]

    async def load_with_time(self, max_turns: int) -> list[dict]:
        """時刻付きで会話履歴を返す。戻り値は {"message": {...}, "time": datetime} のリスト。"""
        cursor = self._collection.find(
            {},
            {"_id": 0, "message": 1, "time": 1},
        ).sort("time", DESCENDING).limit(max_turns)
        docs = await cursor.to_list(length=max_turns)
        docs.reverse()
        return docs

    async def load_with_time_since(self, since: datetime, max_turns: int | None) -> list[dict]:
        """since 以降の会話を時系列順に返す。max_turns を超える場合は末尾（直近）を残す。

        max_turns が None なら件数で切らず、since 以降を全件返す。各要素は
        `{"message": {...}, "time": datetime}` で、リアクションがあれば `reactions` も持つ
        （システムプロンプトの「最近のリアクション」の組み立てに使う）。
        """
        cursor = self._collection.find(
            {"time": {"$gte": since}},
            {"_id": 0, "message": 1, "time": 1, "reactions": 1},
        ).sort("time", ASCENDING)
        docs = await cursor.to_list(length=None)
        if max_turns is not None and len(docs) > max_turns:
            docs = docs[-max_turns:]
        return docs

    async def load_by_channel_between(
        self,
        discord_channel_id: int,
        start: datetime,
        end: datetime,
        max_turns: int,
    ) -> list[dict]:
        """指定チャンネルの `[start, end)` の会話を時系列順に返す。

        `discord_channel_id` を持たない既存ドキュメントは対象外（穴埋めはしない）。
        `max_turns` を超える場合は末尾（直近）を残す。

        Args:
            discord_channel_id: 対象の Discord チャンネル ID。
            start: 取得範囲の下限（この時刻を含む。UTC 推奨）。
            end: 取得範囲の上限（この時刻を含まない。UTC 推奨）。
            max_turns: 返す最大件数。

        Returns:
            {"message": {...}, "time": datetime} のリスト（古い順）。
        """
        cursor = self._collection.find(
            {
                "discord_channel_id": discord_channel_id,
                "time": {"$gte": start, "$lt": end},
            },
            {"_id": 0, "message": 1, "time": 1},
        ).sort("time", ASCENDING)
        docs = await cursor.to_list(length=None)
        if len(docs) > max_turns:
            docs = docs[-max_turns:]
        return docs

    async def search(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
        keywords: list[str] | None = None,
        role: str | None = None,
        discord_channel_id: int | None = None,
        limit: int = 30,
    ) -> list[dict]:
        """条件を指定して会話履歴を新しい順に検索する。

        条件はすべて任意で、指定されたものだけを AND で重ねる。`keywords` は
        `message.content` に対する部分一致（大文字小文字を区別しない）で、
        すべてを含むものだけを返す。利用者・LLM 由来の文字列をそのまま正規表現
        として解釈させないよう、キーワードはエスケープしてから渡す。

        Args:
            start: 取得範囲の下限（この時刻を含む。UTC 推奨）。`None` なら下限なし。
            end: 取得範囲の上限（この時刻を含む。UTC 推奨）。`None` なら上限なし。
            keywords: すべて含むべきキーワードのリスト。`None` / 空なら絞り込まない。
            role: 絞り込む発言者（`"user"` / `"assistant"` など）。`None` なら絞り込まない。
            discord_channel_id: 絞り込む Discord チャンネル ID。`None` なら全チャンネル横断。
            limit: 返す最大件数。MongoDB の仕様上 `0` は「制限なし」と同等に
                なるため、利用者入力を渡す呼び出し元が正の整数へ正規化すること。

        Returns:
            {"message": {...}, "time": datetime} のリスト（新しい順）。
        """
        mongo_filter: dict = {}

        if start is not None or end is not None:
            time_filter: dict = {}
            if start is not None:
                time_filter["$gte"] = start
            if end is not None:
                time_filter["$lte"] = end
            mongo_filter["time"] = time_filter

        if keywords:
            mongo_filter["$and"] = [
                {"message.content": {"$regex": re.escape(keyword), "$options": "i"}}
                for keyword in keywords
            ]

        if role:
            mongo_filter["message.role"] = role

        if discord_channel_id is not None:
            mongo_filter["discord_channel_id"] = discord_channel_id

        cursor = self._collection.find(
            mongo_filter,
            {"_id": 0, "message": 1, "time": 1},
        ).sort("time", DESCENDING).limit(limit)
        return await cursor.to_list(length=limit)

    async def list_for_client(
        self,
        limit: int,
        offset: int,
        after_id: str | None = None,
        since: datetime | None = None,
    ) -> list[dict]:
        """クライアント向けに会話履歴を返す（全件の新しい順か、差分取得）。

        並び順は常に `(time, _id)` の複合キーで決まり、同じ `time` のドキュメントの
        順序も毎回同じになる（`_id` は ObjectId の採番順で、同じプロセスが続けて保存した
        ものは保存順に大きくなる）。

        - `after_id` / `since` をどちらも省略した場合は従来どおり全件を新しい順
          （`time` 降順、同時刻は `_id` 降順）にし、`offset` 件飛ばして `limit` 件返す。
        - どちらかを指定した場合（差分取得）は **古い順**（`time` 昇順、同時刻は
          `_id` 昇順）で、条件に合うものの先頭から `offset` 件飛ばして `limit` 件返す。
          古い順にするのは、`limit` で切ったときに「既知の最新件の直後」から欠けなく
          返すため（新しい順で切ると間が抜ける）と、クライアントが受け取った順に
          末尾へ追加できるようにするため。続きは最後の要素の `_id` を次の
          `after_id` に渡せば重複も取りこぼしもなく取れる。

        Args:
            limit: 返す最大件数。
            offset: 先頭から飛ばす件数。
            after_id: このドキュメントより新しいものだけを返す（そのドキュメント自身は
                **含まない**）。「より新しい」は `(time, _id)` の組で判定し、
                `time` が大きいもの、または `time` が同じで `_id` が大きいものを返す
                （同じ時刻に続けて保存された後続ブロックも取りこぼさない）。
                ObjectId として不正な形式なら `ValueError`。存在しない
                （TTL で消えた・削除された）場合は、`since` があれば `since` だけで
                絞り込み、無ければ `ConversationNotFoundError` を送出する
                （空リストを返すと「新しいものは無い」と区別できず取りこぼすため）。
            since: この時刻 **以降**（`time >= since`。境界ちょうどを **含む**）のものだけを
                返す。含めるのは、同じミリ秒に保存された別ブロックを取りこぼさない
                ためで、`save_entry` が返した `time` を渡すとそのエントリ自身も返る
                （重複は `_id` で除く想定）。UTC の timezone-aware な値を推奨する。
                `after_id` と同時に指定した場合は両方の条件を満たすものだけを返す。

        Returns:
            {"_id": str, "time": datetime, "message": {...}, "reactions": [...]} のリスト。
            `reactions` はリアクションが無ければ空リスト（要素の形は `add_reaction` を参照）。

        Raises:
            ValueError: `after_id` が ObjectId として不正な形式のとき。
            ConversationNotFoundError: `after_id` のドキュメントが存在せず、
                `since` も指定されていないとき。
        """
        return await self._list_paged([], limit, offset, after_id, since, hint=None)

    async def list_reacted(
        self,
        limit: int,
        offset: int,
        emojis: list[str] | None = None,
        after_id: str | None = None,
        since: datetime | None = None,
    ) -> list[dict]:
        """リアクションの付いたメッセージだけを `list_for_client` と同じ形・並び順で返す。

        並び順・`offset` / `limit`・続きの取り方（`after_id` / `since`）は
        `list_for_client` と同じ。`after_id` には、この一覧が返した `_id` をそのまま
        渡せばよい（基準のドキュメントにリアクションが無くなっていても、`(time, _id)`
        の組で続きを判定する）。取得はリアクション付きのドキュメントだけを対象にした
        部分インデックス（`REACTIONS_INDEX_NAME`）を使う。

        Args:
            limit: 返す最大件数。
            offset: 先頭から飛ばす件数。
            emojis: 絞り込む絵文字（標準の絵文字なら文字そのもの、カスタム絵文字なら
                名前）。いずれか 1 つでも付いていれば返す。`None` / 空なら
                リアクション付きのメッセージをすべて対象にする。
            after_id: `list_for_client` と同じ。
            since: `list_for_client` と同じ。

        Returns:
            `list_for_client` と同じ形のリスト（`reactions` は空にならない）。

        Raises:
            ValueError: `after_id` が ObjectId として不正な形式のとき。
            ConversationNotFoundError: `after_id` のドキュメントが存在せず、
                `since` も指定されていないとき。
        """
        conditions: list[dict] = [{"reactions": {"$exists": True}}]
        if emojis:
            conditions.append({"reactions.emoji": {"$in": list(emojis)}})
        return await self._list_paged(
            conditions, limit, offset, after_id, since, hint=REACTIONS_INDEX_NAME
        )

    async def count_reactions(self) -> list[dict]:
        """絵文字ごとの件数を全期間で集計して返す。

        同じメッセージに同じ絵文字は 1 つまでなので、件数はその絵文字が付いた
        メッセージの数と同じ。リアクション付きのドキュメントだけを対象にした部分
        インデックス（`REACTIONS_INDEX_NAME`）を使う。

        Returns:
            `{"emoji": str, "count": int}`（カスタム絵文字は `emoji_id` も持つ）の
            リスト。件数の多い順（同数は絵文字の名前順）。
        """
        pipeline = [
            {"$match": {"reactions": {"$exists": True}}},
            {"$unwind": "$reactions"},
            {
                "$group": {
                    "_id": {"emoji": "$reactions.emoji", "emoji_id": "$reactions.emoji_id"},
                    "count": {"$sum": 1},
                },
            },
            {"$sort": {"count": DESCENDING, "_id.emoji": ASCENDING}},
        ]
        cursor = await self._collection.aggregate(pipeline, hint=REACTIONS_INDEX_NAME)
        rows = await cursor.to_list(length=None)
        result: list[dict] = []
        for row in rows:
            key = row.get("_id") or {}
            item: dict = {"emoji": key.get("emoji"), "count": row.get("count", 0)}
            if key.get("emoji_id") is not None:
                item["emoji_id"] = key["emoji_id"]
            result.append(item)
        return result

    async def _list_paged(
        self,
        base_conditions: list[dict],
        limit: int,
        offset: int,
        after_id: str | None,
        since: datetime | None,
        hint: str | None,
    ) -> list[dict]:
        """`list_for_client` / `list_reacted` の共通の取得処理。

        `base_conditions` に差分取得の条件（`after_id` / `since`）を AND で重ね、
        `(time, _id)` の複合キーで並べて返す（並び順の決め方は `list_for_client` を参照）。
        """
        conditions: list[dict] = list(base_conditions)
        if since is not None:
            conditions.append({"time": {"$gte": since}})
        if after_id is not None:
            if not isinstance(after_id, str) or not ObjectId.is_valid(after_id):
                raise ValueError("after_id is not a valid ObjectId")
            after_oid = ObjectId(after_id)
            anchor = await self._collection.find_one({"_id": after_oid}, {"time": 1})
            if anchor is not None:
                anchor_time = anchor["time"]
                conditions.append({
                    "$or": [
                        {"time": {"$gt": anchor_time}},
                        {"time": anchor_time, "_id": {"$gt": after_oid}},
                    ],
                })
            elif since is None:
                raise ConversationNotFoundError("conversation for after_id was not found")

        if not conditions:
            mongo_filter: dict = {}
        elif len(conditions) == 1:
            mongo_filter = conditions[0]
        else:
            mongo_filter = {"$and": conditions}

        incremental = after_id is not None or since is not None
        direction = ASCENDING if incremental else DESCENDING
        cursor = self._collection.find(
            mongo_filter,
            {"_id": 1, "message": 1, "time": 1, "reactions": 1},
        )
        if hint is not None:
            cursor = cursor.hint(hint)
        cursor = (
            cursor
            .sort([("time", direction), ("_id", direction)])
            .skip(offset)
            .limit(limit)
        )
        docs = await cursor.to_list(length=limit)
        return [
            {
                "_id": str(doc["_id"]),
                "time": doc["time"],
                "message": doc.get("message", {}),
                "reactions": _reactions_of(doc),
            }
            for doc in docs
        ]

    async def find_id_by_discord_message_id(self, discord_message_id: int) -> str | None:
        """Discord メッセージ ID から、それを `discord_message_ids` に持つ会話履歴の `_id` を返す。

        長文を分割して送った返信は 1 つの履歴に対して複数の Discord メッセージを
        持つため、どれか 1 つが一致すればその履歴を返す。

        Args:
            discord_message_id: Discord メッセージの ID。

        Returns:
            該当する会話履歴の `_id`（文字列）。無ければ None。
        """
        doc = await self._collection.find_one(
            {"discord_message_ids": discord_message_id}, {"_id": 1}
        )
        return str(doc["_id"]) if doc else None

    async def add_reaction(
        self,
        conversation_id: str,
        emoji: ReactionEmoji,
        source: str,
        reacted_at: datetime,
    ) -> bool:
        """会話履歴の `reactions` に絵文字を 1 つ足す。

        同じメッセージに同じ絵文字は 1 つまでで、既に付いていれば何もしない
        （重複の確認と追加は 1 回の `update_one` で原子的に行う）。要素は
        `{"emoji": 絵文字, "source": 経路, "reacted_at": 時刻}` で、カスタム絵文字は
        `emoji_id` も持つ。`message.content` には手を加えない。

        Args:
            conversation_id: 対象の会話履歴の `_id`（文字列）。
            emoji: 付ける絵文字。
            source: 付けた経路（`"discord"` / `"client"` など）。
            reacted_at: 付けた時刻（UTC）。

        Returns:
            追加した場合は True。既に付いていた・履歴が無い場合は False。
        """
        element = {**emoji.to_document(), "source": source, "reacted_at": reacted_at}
        result = await self._collection.update_one(
            {
                "_id": ObjectId(conversation_id),
                "reactions": {"$not": {"$elemMatch": emoji.match_query()}},
            },
            {"$push": {"reactions": element}},
        )
        return result.modified_count > 0

    async def remove_reaction(self, conversation_id: str, emoji: ReactionEmoji) -> bool:
        """会話履歴の `reactions` から絵文字を取り除く。

        取り除いて空になった `reactions` はフィールドごと消す（部分インデックスと
        `list_reacted` の対象から外すため）。

        Args:
            conversation_id: 対象の会話履歴の `_id`（文字列）。
            emoji: 取り除く絵文字。

        Returns:
            取り除いた場合は True。付いていなかった・履歴が無い場合は False。
        """
        oid = ObjectId(conversation_id)
        result = await self._collection.update_one(
            {"_id": oid, "reactions": {"$elemMatch": emoji.match_query()}},
            {"$pull": {"reactions": emoji.match_query()}},
        )
        if result.modified_count == 0:
            return False
        await self._collection.update_one(
            {"_id": oid, "reactions": {"$size": 0}},
            {"$unset": {"reactions": ""}},
        )
        return True

    async def save(
        self,
        message: dict,
        tags: list[str] | None = None,
        discord_channel_id: int | None = None,
        discord_message_ids: list[int] | None = None,
    ) -> str:
        """メッセージを会話履歴に保存し、保存したドキュメントの _id を返します。

        `save_entry` の薄いラッパーで、保存内容は同じ。`time` も必要なら
        `save_entry` を使う。

        Args:
            message: 保存する会話メッセージ（role / content）。
            tags: エントリの分類タグ。空・None の場合はフィールドを保存しない。
            discord_channel_id: 対応する Discord メッセージのチャンネル ID。
            discord_message_ids: 対応する Discord メッセージの ID リスト
                （長文が複数チャンクに分割された場合に備えてリストで持つ）。

        Returns:
            保存したドキュメントの _id（文字列）。
        """
        saved = await self.save_entry(
            message,
            tags=tags,
            discord_channel_id=discord_channel_id,
            discord_message_ids=discord_message_ids,
        )
        return saved.id

    async def save_entry(
        self,
        message: dict,
        tags: list[str] | None = None,
        discord_channel_id: int | None = None,
        discord_message_ids: list[int] | None = None,
    ) -> SavedConversation:
        """メッセージを会話履歴に保存し、保存したエントリの `_id` と `time` を返します。

        保存するドキュメントの内容と `time` の決め方はこのメソッドだけが持つ。
        `time` は現在時刻（UTC）をミリ秒単位に切り捨てた値で、保存した値と
        返す値は同じ（`list_for_client` が返す `time` とも `==` で一致する）。

        Args:
            message: 保存する会話メッセージ（role / content）。
            tags: エントリの分類タグ。空・None の場合はフィールドを保存しない。
            discord_channel_id: 対応する Discord メッセージのチャンネル ID。
            discord_message_ids: 対応する Discord メッセージの ID リスト
                （長文が複数チャンクに分割された場合に備えてリストで持つ）。

        Returns:
            `SavedConversation`。`id` は保存したドキュメントの `_id`（文字列）、
            `time` は保存した `time`（UTC の timezone-aware な datetime。ミリ秒精度）。
        """
        doc: dict = {
            "message": message,
            "time": _storage_now(),
        }
        if tags:
            doc["tags"] = tags
        if discord_channel_id is not None:
            doc["discord_channel_id"] = discord_channel_id
        if discord_message_ids:
            doc["discord_message_ids"] = discord_message_ids
        result = await self._collection.insert_one(doc)
        return SavedConversation(id=str(result.inserted_id), time=doc["time"])

    async def find_latest_by_tag(self, tag: str) -> dict | None:
        """指定タグを持つ会話履歴のうち最新の 1 件を返します。

        Args:
            tag: 検索対象のタグ（例: "dirty"）。

        Returns:
            該当ドキュメント（_id を含む）。存在しない場合は None。
        """
        cursor = self._collection.find({"tags": tag}).sort("time", DESCENDING).limit(1)
        docs = await cursor.to_list(length=1)
        return docs[0] if docs else None

    async def delete(self, id: str) -> bool:
        """指定 ID の会話履歴を削除します。

        Args:
            id: 削除するドキュメントの _id（文字列）。

        Returns:
            削除できた場合は True、該当がなければ False。
        """
        result = await self._collection.delete_one({"_id": ObjectId(id)})
        return result.deleted_count > 0
