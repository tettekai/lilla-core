# スケジュール実行で LLM に委譲する通知

「30 分おきに様子を見て、言うべきことがあれば声をかける」ような通知は、判断そのものを
LLM に委譲すると書きやすくなります。そのための定期タスクをコア組み込みのツールとして
同梱していますが、既定では有効化されていません。`${CONFIG_ROOT}/tools/` に
`task_*.yaml` を置くと opt-in で有効になります。

```yaml
# ${CONFIG_ROOT}/tools/task_reminder.yaml
type: lilla_core.builtin_tools.task_scheduled_llm
schedule: "*/30 * * * *"        # cron。ui.timezone で解釈されます
target: dm:{DISCORD_MY_USER_ID} # dm:USER_ID / channel:CHANNEL_ID
llm_provider: reminder          # 必須。llm.providers のプロバイダー名
prompt: dir:${config_root}/prompts/reminder  # 必須。file: / dir:（リストも可）
```

YAML のファイル名（stem）がツール名になるので、同じ `type` で別の
`schedule` / `target` / `prompt` を持つタスクをいくつでも並べられます。

## 各項目

- `schedule` … cron 式。省略するとスケジューラには登録されず、`!runtask <ツール名>`
  による手動実行だけができます
- `target` … 通知先。`{DISCORD_MY_USER_ID}` と書くと `discord.my_user_id` に
  置き換わります。未設定のときは LLM を呼ばずに終わります（送る先が無いため）
- `llm_provider` … **必須**。`llm.providers` のプロバイダー名を書きます。無ければ
  起動時に失敗します
- `prompt` … **必須**。[`file:` / `dir:` の source spec](tools.md) をそのまま渡します。
  `dir:` はそのディレクトリの `.md` / `.txt` をファイル名昇順で連結します。無ければ
  起動時に失敗します

## 実行時のふるまい

1. `prompt` を読み込み、本文中の `{{now}}` を実行時刻（`ui.timezone` の日時、
   `YYYY-MM-DD HH:MM` 形式）に置き換えます
2. `client_type="task"` の会話として LLM を 1 往復させます。会話履歴・ユーザーメモ・
   LLM ツールは通常会話と同じものが使えます
3. 返答が `NO_NOTIFICATION`（前後の空白や `**NO_NOTIFICATION**` のように `*` で
   囲んだ形も同じ扱い）または空なら、**会話履歴にも残さず Discord にも送りません**
4. それ以外の返答は、アシスタント発言として会話履歴に追記し、`target` へ送ります
5. 実行中の例外は ERROR ログに出して外へ漏らしません（次回の実行は通常どおり行われます）

「今回は何も無い」を LLM 自身に判断させられるので、短い間隔で起こしても通知が
増えません。プロンプト側には、通知が不要なときは `NO_NOTIFICATION` だけを返すよう
書いておいてください。

task ツールの一般的な仕組みは [ツール契約](tools.md) を参照してください。
