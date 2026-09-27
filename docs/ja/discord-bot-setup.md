# Discord ボットのセットアップ

[Discord Developer Portal](https://discord.com/developers/applications) のアプリケーション
の **Bot** ページで、招待前に以下を設定してください。

## Privileged Gateway Intents（特権インテント）

- **Message Content Intent** — ON にしてください。`bot_client.py` が
  `intents.message_content = True` を要求しています。これを ON にしないと Discord は
  メッセージ本文を空にして配送するため、Bot はメッセージ内容を読めず、何も応答できなく
  なります。

それ以外の特権インテント（Server Members / Presence）は不要です。コアはメンバー情報や
在席状態を利用していません。

## Bot 招待 URL 生成時に付与する Permissions

招待 URL（OAuth2 URL Generator、`bot` スコープ）を生成する際は、最低限以下にチェックを
入れてください。

- **View Channels** — 読み書きが必要なチャンネルを閲覧するため
- **Send Messages** — 返信・コマンド応答・承認依頼の投稿
  （`commands/` / `handlers/` 各所の `message.reply()` / `channel.send()`）
- **Read Message History** — `cleardirty` が `channel.fetch_message()` で過去メッセージを
  参照するため
- **Attach Files** — `selftest` と承認フローが `discord.File` でファイルを添付送信する
  ため

## うまく動かないとき

Message Content Intent を有効にし忘れると、Bot はメッセージを受信しているように見えても
`message.content` が空になり、何も反応しなくなります（本文が空になる症状）。起動時に
Discord から拒否された場合は、その旨を ERROR ログに出してプロセスを終了します。

上記の Permissions が不足している場合は、送信・返信エラーや、必要なチャンネルが見えない
といった症状になります。
