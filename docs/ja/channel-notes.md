# 部屋のノート（深夜要約）

[登録チャンネル](channels.md) は「部屋のノート」を持てます。その日にその部屋で何を
話したかを短い事実として残すもので、会話履歴の TTL で消えたあとも残ります。これを書く
深夜バッチはコア組み込みのタスクツールとして同梱していますが、既定では有効化されて
いません。`${CONFIG_ROOT}/tools/task_channel_summary.yaml` に以下の YAML を置くと
opt-in で有効になります。

```yaml
type: lilla_core.builtin_tools.task_channel_summary
# schedule: "0 2 * * *"      # 既定値。ui.timezone で解釈されます
# llm_name: summarizer       # 既定は llm.default
# max_turns: 500             # 1 チャンネルあたり読む発言数
# max_transcript_chars: 20000
```

- 実行のたびに `discord.channels` をループし、**前日**（`ui.timezone` の暦日）を要約
  します。2 時実行で「当日」を対象にすると 0:00–2:00 しか入らないためです
- 対象は `discord_channel_id` が付いている発言だけで、付いていない既存の発言は
  対象外です（穴埋めはしません）
- 対象日の発言が無いチャンネルは、既存のノートをそのまま残します
- 要約にはキャラクター用のシステムプロンプトを使わず、短い事実抽出用のプロンプトを
  使います。本文は `<channel_transcript>` タグで囲んだデータとして渡します
- ノートは `channel_summaries` コレクションに、1 チャンネル 1 ドキュメントで保存します
  （`discord_channel_id` がユニークで、実行のたびに upsert します）
- `discord.channels` が空のとき、またはこの YAML が無いときは、動作は現行どおりです

ノートのある登録チャンネルで会話すると、そのノートが `summary_date` と一緒に
システムプロンプトへ差し込まれます。信頼しないコンテキストとして `<channel_note>`
タグで囲み、「指示ではなく過去の記録」として扱わせます。未登録チャンネル・DM には
入りません。

task ツールの一般的な仕組みは [ツール契約](tools.md) を参照してください。
