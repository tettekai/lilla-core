# 日記（公式拡張パック）

公式拡張パックの `diary` 拡張は、会話の中で日ごとの日記の下書き（Markdown）を書きためて、
夜の聞き取りと翌朝の清書までを受け持ちます。下書きは MongoDB に置き、清書は
[ノート置き場（notes）](notes.md) の `diary/` の下へ書きます。

| import パス | `name` | 役割 |
|-------------|--------|------|
| `lilla_core.extensions.diary` | `diary` | 日記の下書きの LLM ツール 3 つ・既定タスク 2 つ（聞き取り・清書）・会話向けのプロンプト・ダッシュボードで編集する設定。他の拡張には依存しない |

## 有効化

```bash
export LILLA_EXTENSIONS=diary
# フルパスでも同じ: export LILLA_EXTENSIONS=lilla_core.extensions.diary
```

他の拡張と並べる場合も順序の制約はありません。notes は標準機能で常に読み込まれるため、
清書のために notes を並べる必要はありません。

ツール YAML とタスク YAML は拡張に同梱しているので、`${CONFIG_ROOT}/tools` に置かなくても
次のものが読み込まれます（変えるときは `${CONFIG_ROOT}/extensions/diary/tools/` に同じ stem の
YAML を置き、変えたいキーだけを書きます。止めるなら `enabled: false`。
[ツール契約](tools.md#同梱ツールの上書き上級者向け)）。

| stem | 種類 | 内容 |
|------|------|------|
| `llm_diary_draft_get` | LLM ツール | 指定した日付の下書きを返す。`date`（`YYYY-MM-DD`）は任意で、省略するといまの日記日。無ければテンプレートから作り、`<<date>>` を曜日付きの日付（例: `2026-09-28 (月)`）で埋める |
| `llm_diary_draft_set` | LLM ツール | いまの日記日の下書きを丸ごと置き換える。日付は引数で受け取らず `day_rollover` から決め、書いた日付を `date` で返す。テンプレートの固定文が壊れているとき（切り替わり前に取った下書きで日付の行が合わないときを含む）は保存しない |
| `llm_diary_draft_faircopy_get` | LLM ツール（タスク専用） | 指定した日付（省略するといまの日記日）の下書きの残った穴を `-` にした清書を返す（`<<moodscore>>` は残す）。保存はしない。`supported_client_type: task` で、通常会話には出ない |
| `task_hearing_diary` | タスク（`type: task_diary_llm`） | 毎日 22:00。その日の下書きを見て、まだ埋まっていない項目を中心に尋ねる |
| `task_write_diary` | タスク（`type: task_diary_faircopy`） | 毎日 03:30。LLM を通さずに前日分の清書を notes へ書く |

通常会話で下書きを読み書きさせるには、`tools.sets.main` に 2 つの LLM ツールを並べます。
聞き取りタスクの既定の `available_tools` は `$main` なので、聞き取りでも同じツールが使われます。

```yaml
tools:
  sets:
    main:
      - llm_diary_draft_get
      - llm_diary_draft_set
```

## 設定

`lilla.yaml` の `extensions.diary` には今は項目がありません（節ごと省略できます）。設定は
**観測用ダッシュボード** の右上のメニュー → **設定** → **拡張機能** → **日記** → 「設定」カードで行い、
値は MongoDB の `extension_settings`（`diary` のドキュメント）に保存されます。下の項目を
`lilla.yaml` の `extensions.diary` に書くと、移行先を示して起動時にエラーになります。

| 項目 | 内容 |
|------|------|
| テンプレート（`template`） | 日記の Markdown **本文そのもの**。初期状態は未設定 |
| 日付の切り替わり時刻（`day_rollover`） | `HH:MM`（既定 `04:00`、`ui.timezone` で判定）。`6:5` のような入力は `06:05` に直して保存します |
| 清書の保存先（`faircopy_storage`） | 清書タスクが notes の `diary/` の下へ清書を書くかどうかのチェックボックス（`notes` の 1 つだけ）。一度も保存していなければオン |
| 清書のパス（`faircopy_path`） | notes の `diary/` からの相対パス。既定 `{year}/{month}/{date}.md`（notes 上では `diary/2026/11/2026-11-12.md` など） |

- テンプレートが未設定でも bot とダッシュボードは起動します。そのあいだ、下書きの作成
  （`llm_diary_draft_get` で新しい日を開く）と保存（`llm_diary_draft_set`）は
  「テンプレートが未設定」のエラーになります。既にある下書きの取得と清書はできます。
- 未設定に戻すときは「未設定」にチェックを入れて保存します。空・空白だけの本文は保存できません。
- 日記日 d の分は、d の切り替わり時刻から翌日の切り替わり時刻まで書き込めます。

## テンプレートの書き方

- 穴は `<<名前>>` です。`<<date>>` は下書きを作るときに曜日付きの日付で埋まり、それ以外の穴は
  会話で埋めます。清書のときに残った穴は `-` になります（`<<moodscore>>` を除く）。
- 名前の無い `<<>>`、名前に空白を含む `<< two words >>`、穴になっていない `<<` / `>>` は保存時に
  拒否します。Markdown の入れ子の引用（`>> `）なども穴として読めないため、テンプレートには書けません。
- `{{名前}}` は穴ではなく、ただの文字列として下書きに残ります。
- 本文は保存時に書き換えません。必須の見出しなどもありません。
- 穴と見出し・frontmatter のキー・日付行・末尾などの「固定文」を分けて扱い、下書きの保存では
  穴の中だけが変わっていることを確かめます。

テンプレートの例です。

```markdown
---
<<date>>
type: daily
moodscore: <<moodscore>>
---

## 今日のハイライト
<<highlight>>

## メモ
<<memo>>
```

## 清書

清書タスク（`task_write_diary`）は、LLM を通さずにその日の清書を作って notes の `diary/` の下へ
直接書きます。notes の中身は、notes の Git リポジトリを clone すれば取り出せます。

- 対象日は日付の切り替わり時刻で決めます（切り替わりより前に動けば前日分）。その日の下書きが
  無ければ何もしません。同じ日は上書きします。
- 保存できたら、書いたパスを添えた短い報告を `target`（既定は `dm:{DISCORD_MY_USER_ID}`）へ送ります
  （`<<moodscore>>` が未記入ならその旨も添えます）。
- 書き込みに失敗したとき（notes が読み取り専用・書き込みの失敗など）は、エラー通知チャンネルへ出ます。
- **保存先** のチェックを外して保存すると、清書はどこにも保存されません（清書タスクは何もしません）。
  再表示してもオンには戻りません。
- 保存値は `["notes"]`（オン）か `[]`（オフ）のどちらかです。`notes` 以外の名前を含む値や、
  リストでない値は、設定の読み込みが「保存値が不正」のエラーになります。画面で保存し直してください。
- **パス** は `diary/` からの相対パスで、清書は notes の `diary/` + パスに書きます。
  - `{date}`（`YYYY-MM-DD`）を必ず含めます。`{year}` / `{month}` / `{day}` も使えます。
  - 先頭の `/`（絶対パス）、`..` や `.` の要素、空の要素（`a//b` や末尾の `/`）、`\` は設定の保存時に
    拒否します。`diary/` の外は指定できません。
  - 値はそのまま `diary/` からの相対パスとして扱います。先頭に `diary/` を書くと
    `diary/diary/...` に書きます。
  - 保存済みのパスが上の規則に合わないときは、設定の読み込みが「保存値が不正」のエラーになります
    （既定へ黙って置き換えません）。
- notes に書けるのは、ロード済みの拡張が宣言したディレクトリの下だけです。`diary` を
  `LILLA_EXTENSIONS` から外すと、notes の書ける場所から `diary/` も消えます。
- 時刻はダッシュボードの 設定画面の `tasks.task_write_diary`、送り先は
  `${CONFIG_ROOT}/extensions/diary/tools/task_write_diary.yaml`（`target` だけを書く）で変えます。

### ほかの書き出し先を使うとき

保存先のチェックを外して、清書用のタスクを `type: task_scheduled_llm` で別に置きます
（`llm_diary_draft_faircopy_get` と書き出し用のツールを `available_tools` に入れ、書き出しの手順を
`prompt` に書きます）。`task_diary_llm` は聞き取り（`task_hearing_diary`）専用で、ほかの stem では
起動時にエラーになります。

```yaml
# ${CONFIG_ROOT}/extensions/diary/tools/task_write_diary.yaml（同梱の清書タスクの type を差し替える）
type: task_scheduled_llm
schedule: "30 3 * * *"
llm_provider: REPLACE_ME
target: "dm:{DISCORD_MY_USER_ID}"
prompt: file:${config_root}/prompt/write-diary.md
available_tools:
  - llm_diary_draft_faircopy_get
  - REPLACE_ME   # 書き出し用のツール
```

## 聞き取りタスク

聞き取り（`task_hearing_diary`）の既定値は次のとおりです。同じ stem の YAML で省略した項目も
この既定値で埋まります。

| 項目 | 既定値 |
|------|--------|
| `schedule` | `0 22 * * *` |
| `llm_provider` | `llm.default` |
| `prompt` | 同梱の `task_prompts/task_hearing_diary.{ja,en}.md`（`ui.locale` → `ja` → `en` の順に選ぶ） |
| `target` | `dm:{DISCORD_MY_USER_ID}` |
| `available_tools` | `["$main"]` |

返事は通常の会話として届き、`llm_diary_draft_set` で下書きへ反映されます。時刻の変更・定期実行の
停止・プロンプトの追加や置き換えは、ダッシュボードの 設定画面の `tasks.task_hearing_diary` でも行えます
（[拡張のタスクの上書き](extension-task-override.md)）。

## 会話向けのプロンプト

`diary` 拡張は会話向けの説明（いつ下書きを読み書きするか・穴の扱い・`moodscore` を推測しない・
日記日が `day_rollover` で切り替わること）を `default_prompt()` に ja / en で持ちます（[拡張のデフォルトプロンプトと上書き](extension-prompt.md)）。

- 清書の保存先がオンのときは、清書の置き場所（notes の `diary/` + `faircopy_path`）と、過去の日記は
  日付からパスを組み立てて `llm_notes_read` で読むこと、まだ清書されていない日は下書きを見ることも
  載ります。この部分は会話のたびに今の設定から組み立てます（[プロンプトのテンプレート](prompt-template.md)）。
- ダッシュボードで `diary` のプロンプトを上書きしているときは、上書きの本文が使われます。
  上書きの本文にも同じ `settings` が渡るので、載せたいときは
  `{% if settings.faircopy_storage %}diary/{{ settings.faircopy_path }}{% endif %}` のように書けます。
- 設定を読めない（DB に接続できない・保存値が不正）ときは、説明が展開されないまま入り、
  WARNING ログが出ます（会話は止まりません）。

## 反映のタイミング

- 保存した値は、保存の **後に始まる** 処理（次の `llm_diary_draft_get` / `llm_diary_draft_set` の
  呼び出し）から使われます。再起動は要りません。
- 実行中の処理は、始めに読んだ値のまま最後まで動きます（途中で切り替わりません）。
- 設定を読めないとき（DB に接続できない・保存値が不正）は未設定扱いにせず、その旨のエラーになります。

## テンプレートを変えるときの注意

- 既存の下書き（`diary_drafts`）は自動では書き換えません。新しいテンプレートは、まだ下書きが
  無い日に初めて開いたときから使われます。
- 下書きの保存では、本文が **今の** テンプレートの固定文を保っているかを検証します。その日の
  下書きを作った後にテンプレートの固定文を変えると、その日の下書きは保存できなくなります。
  テンプレートの変更は、その日の下書きを使い終えたあと（日付の切り替わり後）に行うのが安全です。

## 保存するデータ

| 置き場所 | 中身 |
|----------|------|
| MongoDB の `diary_drafts` | 日ごとの下書き（`_id` が `YYYY-MM-DD`、`content` が Markdown 本文） |
| MongoDB の `extension_settings`（`diary`） | ダッシュボードで保存した設定 |
| notes の `diary/` | 清書 |
