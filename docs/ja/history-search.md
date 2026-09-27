# 会話履歴の部屋名検索

会話履歴そのものは全チャンネル横断のままですが、「あの部屋で何を話したか」を思い出す
ための検索ツールをコア組み込みの LLM ツールとして同梱しています。既定では有効化されて
いません。`${CONFIG_ROOT}/tools/` に以下の YAML を置くと opt-in で有効になります
（LLM へ見せるツール名は YAML のファイル名になります）。

```yaml
type: lilla_core.builtin_tools.llm_conversation_get
```

- `datetime_range`（`today` / `last_7_days` / `2026-04-20/2026-04-26` など）・`query`
  （スペース区切りの AND キーワード）・`role`（`user` / `assistant` / `all`）・`limit`
  （既定 30、上限 30）で絞り込めます
- `channel_name` に `discord.channels` の登録名を渡すと、そのチャンネルの発言だけに
  絞り込みます。**設定上の別名であり、Discord の現在のチャンネル名ではありません**
  （[登録チャンネル](channels.md)）
- `channel_name` を省略すると、今までどおり全チャンネル横断で検索します
- 登録に無い名前を渡すとエラーを返します（黙って全件検索に落としません）
- 名前の突き合わせは前後の空白を除いた完全一致で、大文字小文字は区別します
- 期間の境界と結果の表示時刻はどちらも `ui.timezone` で解決したタイムゾーンで扱います
  （[タイムゾーン](timezone.md)）

LLM ツールの一般的な仕組みは [ツール契約](tools.md) を参照してください。
