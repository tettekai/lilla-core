# メッセージへのリアクション

Discord のリアクションや独自クライアントのスタンプなど、ユーザーがメッセージに付けた
絵文字を会話履歴（`conversations`）と結び付けて保存し、エージェントが次の応答で
「どの発言がユーザーに響いたか」を知れるようにします。

## 保存形式

リアクションは会話履歴の各ドキュメントの `reactions` 配列に入ります。リアクションの無い
メッセージはフィールド自体を持ちません（最後の 1 つを取り除いたときもフィールドごと消します）。

```json
{
  "message": {"role": "assistant", "content": "..."},
  "time": "...",
  "reactions": [
    {"emoji": "❤️", "source": "discord", "reacted_at": "2026-11-10T07:25:00Z"},
    {"emoji": "lilla_smile", "emoji_id": 123456789012345678, "source": "discord", "reacted_at": "..."}
  ]
}
```

- `emoji`: 標準の絵文字は文字そのもの（`❤️`）、Discord のカスタム絵文字は名前。
  `:heart:` のようなショートコードは入力上の表記なので保存しません
- `emoji_id`: カスタム絵文字の ID（標準の絵文字は持たない）
- `source`: 付けた経路（`discord` / `client` など）
- `reacted_at`: 付けた時刻（UTC）

同じメッセージに同じ絵文字は 1 つまでです（標準の絵文字は文字で、カスタム絵文字は ID で
同じかを判定します）。重複した追加は何もせず、削除は付いていれば取り除きます。どの経路で
付いたものでも、同じ絵文字なら取り除きます。

サーバーはどの絵文字も特別扱いしません。`❤` と `❤️` のような異体字は別の絵文字として
保存し、絵文字ごとの意味付けも行いません（利用側で決めてください）。`message.content` には
リアクションの情報を混ぜません。

`memory.conversation_ttl_hours` が有効な環境では、メッセージが TTL で消えるときに、
そのメッセージのリアクションも一緒に消えます。

## 追加・削除の共通処理とフック

経路に依存しない追加・削除は `lilla_core.services.reactions` にあります。Discord も、
拡張が持つ独自クライアントも同じ処理を通してください。

```python
from lilla_core.services.reactions import ReactionEmoji, add_reaction, remove_reaction

await add_reaction(conversation_id, ReactionEmoji("❤️"), "client")
await remove_reaction(conversation_id, ReactionEmoji("lilla_smile", 1234), "client")
```

`conversation_id` は `ConversationRepository.list_for_client()` の `_id` と同じ値です。
戻り値は実際に変わったかどうか（重複した追加・付いていない絵文字の削除は `False`）です。

実際に変わったときは、`register_reaction_hook()` で登録したフックが `ReactionChange`
（`action`（`added` / `removed`）・`conversation_id`・`emoji`・`source`・`time`）を受け取ります。
WebSocket で接続中のクライアントへの通知などは、拡張の `setup()` でフックを登録して行って
ください（同期関数・非同期関数のどちらでもよく、フックの失敗は WARNING ログだけで、保存と
他のフックは止めません）。

```python
from lilla_core.services.reactions import register_reaction_hook

async def notify(change):
    ...  # 接続中のクライアントへ送る

register_reaction_hook(notify)
```

## Discord

Bot の発言へのリアクションの追加・削除を、raw 系のイベント（`on_raw_reaction_add` /
`on_raw_reaction_remove`）で受け取って保存します。キャッシュに無い古いメッセージや DM でも
拾えます。

- 対象のメッセージは、保存済みの `discord_message_ids`（長文を分割して送った返信は複数）から
  特定します。該当する会話履歴が無ければ何もしません
- Bot 自身が付けたリアクションは無視します
- 受け付けるのは、通常会話と同じくオーナー（`discord.my_user_id`）のリアクションだけです

必要なインテントは [Discord ボットのセットアップ](discord-bot-setup.md) を参照してください
（特権インテントではないため Developer Portal での設定は要りません）。

## 取得

`ConversationRepository`（`lilla_core.repository.conversation_repository`）のメソッドで
取得します。HTTP API などとしての公開は利用側のアプリケーションで行います。

- `list_for_client()`: 各要素に `reactions` を含めます（リアクションが無ければ空配列）。
  既存の項目（`_id`・`time`・`message.role`・`message.content`）の形は変わりません
- `list_reacted(limit, offset, emojis=None, after_id=None, since=None)`: リアクションの付いた
  メッセージだけを `list_for_client()` と同じ形・並び順・続きの取り方で返します。`emojis`
  （標準の絵文字は文字、カスタム絵文字は名前）を複数指定すると、いずれかが付いたものに
  絞ります
- `count_reactions()`: 絵文字ごとの件数を全期間で集計し、`{"emoji", "count"}`（カスタム
  絵文字は `emoji_id` も）を件数の多い順で返します。今の一覧に出ていない古い絵文字も拾えます

リアクション付きのメッセージは全体のごく一部なので、`reactions` を持つドキュメントだけを
対象にした部分インデックス（`reactions_time_id`）を起動時に作り、`list_reacted()` と
`count_reactions()` はそれを使います。

## LLM への渡し方

会話履歴の各メッセージにはリアクションを混ぜません。代わりにシステムプロンプトの末尾に
「最近のリアクション」（`## Recent Reactions`）の節を足し、そのターンで送る会話履歴に
含まれるメッセージに付いたリアクションだけを載せます。

```
## Recent Reactions
...
- ❤️ on your message [Nov 10 16:22] "今日の予定をまとめると…" (reacted Nov 10 16:25)
```

- 各行は絵文字（カスタム絵文字は `:名前:`）・対象メッセージの発言者・履歴と同じ形式の時刻・
  抜粋（1 行・80 文字まで）・付けた時刻です。メッセージの ID や番号は渡しません
- 載せるのは付けた時刻の新しいものから 20 件までです
- 対象のリアクションが無ければ節自体を出しません
- 節は、リアクションが付いたときや履歴の範囲が動いたときに変わるため、前綴キャッシュを
  なるべく壊さないようシステムプロンプトの最後に置きます
