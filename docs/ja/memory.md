# 長期記憶（標準機能）

`memory` 拡張は、会話の中で長期的に覚えておきたいこと（好み・決定事項・約束・学んだことなど）を
記録し、夜間に整理して [ノート置き場（notes）](notes.md) の `memory/` の下へ長く残す仕組みです。
整理した `memory/core.md` は、会話のたびにシステムプロンプトへ入ります。

| import パス | `name` | 役割 |
|-------------|--------|------|
| `lilla_core.extensions.memory` | `memory` | remember の LLM ツール 2 つ・夜間タスク 2 つ・会話向けのプロンプト（`memory/core.md` の本文を含む）・ダッシュボードで編集する設定・`core.md` の変更通知 |

コア本体の「会話メモリ」（会話履歴・ユーザーメモ・セッションメモリ。`services/memory_manager.py`
が組み立てるもの）とは別物です。会話メモリは直近の会話をそのまま渡す仕組みで、こちらは
会話から覚えておきたいことを選んで、notes に整理して長く残す「長期記憶の拡張」です。

## 有効化

notes と同じ **標準機能** で、`LILLA_EXTENSIONS` に並べなくても常に読み込まれます
（`LILLA_EXTENSIONS` に `memory` / `lilla_core.extensions.memory` が残っていたら、WARNING を出して
読み飛ばします）。会話で使うかどうかは、ダッシュボードの設定 `use_in_conversation`（既定はオン）で
決めます。

ツール YAML とタスク YAML は拡張に同梱しているので、`${CONFIG_ROOT}/tools` に置かなくても次のものが
読み込まれます（変えるときは `${CONFIG_ROOT}/extensions/memory/tools/` に同じ stem の YAML を置き、
変えたいキーだけを書きます。止めるなら `enabled: false`。[ツール契約](tools.md#同梱ツールの上書き上級者向け)）。

| stem | 種類 | 内容 |
|------|------|------|
| `llm_remember_append` | LLM ツール | いまの日付の remember に 1 件追記する。日付は引数で受け取らず `day_rollover` から決め、書いた日付を `date` で返す。同じ本文が既にあれば追記しない（`appended: false`） |
| `llm_remember_get` | LLM ツール | 指定した日付の remember を追記した順に全件返す。`date`（`YYYY-MM-DD`）は任意で、省略するといまの日付。不正な `date` は失敗する |
| `task_memory_review` | タスク（`type: task_memory_llm`） | 毎日 04:30。直前に締まった日の remember を、`memory/core.md` と `memory/personal/` へ振り分けて書く |
| `task_memory_compaction` | タスク（`type: task_memory_llm`） | 毎週月曜 03:30。`memory/core.md` から、もう覚えておく必要がないものを `memory/journal/YYYYMM.md` へ移す |

通常会話で remember を使わせるには、`tools.sets.main` に 2 つの LLM ツールを並べます。
`memory/` の中身を会話で読ませるなら、notes のツールも並べます。

```yaml
tools:
  sets:
    main:
      - llm_remember_append
      - llm_remember_get
      - llm_notes_read
      - llm_notes_list
```

MongoDB のコレクションは `remember`（`_id` が日付 `YYYY-MM-DD`、`entries` に `{content, created_at}` を
追記順に持つ）です。`lilla.yaml` の `extensions.memory` には今は項目が無く、節ごと省略できます。

## notes の `memory/` の構成

`notes_directories()` で宣言するトップレベルは `memory/` だけです。

| パス | 内容 |
|------|------|
| `memory/core.md` | 長期記憶の本体。会話のたびにシステムプロンプトへ入る |
| `memory/personal/` | 利用者についての情報（趣味・健康・仕事など）。中のファイル構成は決めていない |
| `memory/personal/README.md` | personal の索引（どのファイルに何を書くか）。毎日の整理タスクが初回に同梱のひな形から作る |
| `memory/journal/YYYYMM.md` | `core.md` から外した記憶の月ごとのアーカイブ |

既存のデータを自動で移す仕組みはありません。今までの記憶を使い続けるなら、手で notes の `memory/` へ
コピーしてください（`/notes.git` への push でも、ダッシュボードの「ノート」画面でも書けます）。

## 書き込みの経路

- 日中の会話では `memory/` を直接書かず、覚えておきたいことを `llm_remember_append` で remember に
  追記するだけにします。
- `memory/` の下を書くのは、2 つの夜間タスクだけです。

この決まりはプロンプト（会話向けのプロンプトと、タスクの既定プロンプト）で守らせており、コードでは
止めていません（日中の `llm_notes_write` でも `memory/` に書けます）。気づけるように、`core.md` が
変わったら差分を知らせます（下記）。

## 会話向けのプロンプト

`default_prompt()`（ja / en）は、remember の使い方・日付が `day_rollover` で切り替わること・`memory/` の中身を伝え、続けて
`memory/core.md` の本文を `<long_term_memory>` タグで囲んで入れます。本文はテンプレートの関数
[`notes()`](prompt-template.md#ノートを読むnotes) で会話のたびに読み直し、`core.md` がまだ無ければ
タグの節ごと出しません。本文中の `<long_term_memory>` / `</long_term_memory>` の形のタグ（大文字小文字は区別しません）は
[フィルタ `strip_tag`](prompt-template.md#タグを取り除くstrip_tag) で取り除いてから囲みます。取り除いたときは WARNING ログが出ます
（タグを閉じて外へ出られないようにするため）。

設定 `use_in_conversation` をオフにすると、何も入れません（notes も読みません）。夜間タスクは
この設定によらず動きます。

ホストの `prompt.system` に `file:$notes/memory/core.md` を書く必要はありません（書くと同じ本文が
2 回入ります）。入れ方を変えたいときは、[拡張のプロンプトの上書き](extension-prompt.md) で
`memory` のプロンプトを置き換えてください。

## 夜間タスク

どちらも [`task_scheduled_llm`](scheduled-llm.md) の派生クラス（`type: task_memory_llm`）で、YAML で
省略した項目を既定値で埋めます。読み書きには notes のツール（`llm_notes_list` / `llm_notes_read` /
`llm_notes_write` / `llm_notes_search`）だけを使います。

| 項目 | 既定値 |
|------|--------|
| `schedule` | `task_memory_review` は `30 4 * * *`、`task_memory_compaction` は `30 3 * * mon` |
| `llm_provider` | `llm.default` |
| `prompt` | 同梱の `task_prompts/`（`ui.locale` → `ja` → `en` の順に選ぶ） |
| `target` | `dm:{DISCORD_MY_USER_ID}` |
| `available_tools` | notes の 4 つのツール |
| `max_tool_call_iterations` | 20 |

- 毎日の整理の対象日は、日付の区切り（`day_rollover`）を基準に **直前に締まった日** をコードで決め、
  プロンプトの変数 `target_date` で渡します。その日の remember は `<remember_entries>` タグで囲み、
  変数 `remember_entries` で渡します。remember の本文に `<remember_entries>` / `</remember_entries>` の形のタグ
  （大文字小文字・空白の違いも含む）があれば、`core.md` と同じく
  [`strip_tag`](prompt-template.md#タグを取り除くstrip_tag) で取り除き、WARNING ログを出します。記録が無い日は LLM を呼びません。
  対象はその回の「直前に締まった日」だけで、さかのぼりません。bot の停止中や LLM の失敗などで
  整理されなかった日の remember は自動では整理されないため、必要なら手で整理してください。
- 週次の整理は、実行した月のアーカイブのパスを変数 `journal_path` で渡します。`core.md` がまだ無ければ
  LLM を呼びません。
- 何も書かなかったときは `NO_NOTIFICATION` と返させ、通知しません。書いたときは、書いたパスごとに
  何を書いたかを通知します。

時刻と有効 / 無効、プロンプトは、ダッシュボードの [拡張のタスクの上書き](extension-task-override.md) で
変えられます（プロンプトでは `task_memory_review` なら `target_date` / `remember_entries`、
`task_memory_compaction` なら `journal_path` の変数を使えます）。LLM・通知先・ツールを変えるときは、同じ stem の YAML を
`${CONFIG_ROOT}/extensions/memory/tools/` に置き、変えたい項目だけを書きます（残りは既定値で埋まります）。

## ダッシュボードで編集する設定

| 項目 | 既定 | 内容 |
|------|------|------|
| `use_in_conversation` | オン | 会話のシステムプロンプトに長期記憶（使い方と `core.md` の本文）を入れる |
| `day_rollover` | `04:00` | 日付の区切り（`ui.timezone`）。日付 d の remember には、d の区切りから翌日の区切りまで追記する（追記する日付はツールがこの値から決める） |
| `notify_core_changes` | オン | `memory/core.md` が変わったら差分を知らせる |

## `core.md` の変更通知

`memory/core.md` が変わると、エラー通知チャンネル（`discord.error_channel_id`）へ、変わった行数と
コミットのハッシュを本文に、差分を `.patch` で添えて知らせます。夜間タスクだけでなく、会話の
`llm_notes_write`・ダッシュボードでの編集・`/notes.git` への push・削除・直前の版への復元のどれでも
知らせます。

この仕組みは notes 側にあり、拡張が `Extension.notes_watched_paths()` で「変わったら知らせてほしい
パス」を申告します（memory は設定 `notify_core_changes` がオンのときだけ `memory/core.md` を申告します）。

## 注意

- `core.md` の本文は、会話のたびにシステムプロンプトとして LLM に送られます。クラウドの LLM を
  使う場合、利用者についての情報が会話のたびに送られることになります。送りたくないときは
  `use_in_conversation` をオフにしてください。
- 信頼できない入力（外部の Web・他人から届く予定・他の bot の結果など）を取り込むツールと
  併用すると、そこに含まれる指示が remember を経て `core.md` に残り、以後の会話に効き続ける
  おそれがあります。夜間タスクの既定プロンプトは remember の中身を「指示ではなくデータ」として
  扱わせ、外から取り込んだ文章らしいものは書かないよう指示していますが、確実ではありません。
  変更通知はこれに気づくための仕組みなので、切るときは注意してください。
