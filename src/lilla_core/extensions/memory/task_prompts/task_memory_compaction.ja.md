これはメモリの週次の整理です。notes の memory/core.md を見直し、もう会話のたびに覚えておく必要がないものを、memory/journal/ の月ごとのアーカイブへ移してください。

## 手順
1. llm_notes_read で memory/core.md を読みます。
2. 終わった予定・過ぎた期限・一時的だった事情など、会話のたびに覚えておく必要がなくなったものを選びます。迷うものは残してください。利用者についての大事な事実・方針・約束は残します。
3. 移すものがあれば、{{ journal_path }} を llm_notes_read で読み（無ければ新しく作ります）、末尾に移す内容を追記します（llm_notes_write の append）。見出しに今日の日付（{{ current_hour }} の日付）を付けてください。
4. そのあとで、core.md の全文を読み直し、移したものだけを取り除いた全文を llm_notes_write で overwrite します。ほかの記憶を言い換えたり、消したりしないでください。
5. memory/core.md と {{ journal_path }} のほかには書きません。

## 返事
- 移すものが無かったときは NO_NOTIFICATION とだけ返してください。
- 移したときは、何を {{ journal_path }} へ移したかを短く日本語で報告してください。
