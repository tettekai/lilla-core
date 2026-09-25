# 登録チャンネル

`lilla.yaml` の `discord.channels` に登録したチャンネルでは、入口の作法を変えられます。

```yaml
discord:
  my_user_id: "XXXXXXXXX"
  channels:
    - name: dev
      channel_id: "123456789012345678"
      mention_optional: true
    - name: lounge
      channel_id: "234567890123456789"
      # mention_optional 省略時は false
```

- `name`: 設定上の別名。Discord 側の現在のチャンネル名と一致していなくてかまいません
- `channel_id`: チャンネルの snowflake 文字列（`approval_channel_id` などと同じ形式）
- `mention_optional`: `true` なら、そのチャンネルではオーナーのメンションなしの発言にも
  応答します。省略時は `false` で、受信条件は現行どおり（メンションまたは DM）です
- `channels` 未設定・空リストなら、受信動作はまったく変わりません
- `name` または `channel_id` が重複していると起動時に失敗します

登録チャンネルでの会話では、システムプロンプトに「今この登録チャンネルにいる」旨の
短い一節が入ります（未登録チャンネル・DM には入りません）。会話履歴そのものは登録の
有無によらず全チャンネル横断のままで、チャンネルごとに分かれることはありません。

登録チャンネルを使う機能:

- [部屋のノート（深夜要約）](channel-notes.md)
- [会話履歴の部屋名検索](history-search.md)
