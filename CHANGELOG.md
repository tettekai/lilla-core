# Changelog

このプロジェクトの主な変更点を記録します。
形式は [Keep a Changelog](https://keepachangelog.com/ja/1.1.0/) に、
バージョニングは [Semantic Versioning](https://semver.org/lang/ja/) に、
それぞれ準じています。

## [Unreleased]

### Added

- `ConversationRepository.list_for_client()` に省略可能な差分取得の引数 `after_id` / `since` を追加した（#288）。
  `after_id` はそのドキュメントより `(time, _id)` の組で新しいもの（自身は含まない。同じ `time` の後続も含む）、`since` は
  その時刻以降（境界ちょうどを含む）を返し、どちらかを渡したときは古い順に返す。`after_id` が ObjectId として不正なら
  `ValueError`、存在しなければ `since` があれば `since` だけで絞り込み、無ければ新しい例外 `ConversationNotFoundError`
  （`lilla_core.core.exceptions`。`LookupError` の派生）を送出する。引数を省略した場合は従来どおり新しい順
- `ConversationRepository.save_entry()` と `MemoryManager.add_conversation_entry()` を追加した（#288）。引数は `save()` /
  `add_conversation()` と同じで、保存したエントリの `_id`（`str`）と `time`（UTC の timezone-aware な `datetime`）を持つ
  `SavedConversation` を返す。返す `time` は `list_for_client()` が返す値と `==` で一致する。既存の `save()` /
  `add_conversation()` の引数と戻り値（`_id` の `str`）は変えていない

- `run_conversation()` に任意の引数 `pre_tool_calls`（`[{"tool": str, "args": dict}]`。`args` は省略可）を追加した（#286）。
  LLM を呼ぶ前に書かれた順にツールを実行し、LLM が自分で呼んだときと同じ形の呼び出しと結果を最新の user メッセージのあとに積んでから
  通常のツールループに入る。実行できるのはその会話で LLM に渡すツールだけで、使えないツール・不正な引数・実行中の例外はエラーの結果として
  LLM に渡す（会話は止めない）。往復回数の上限には数えず、会話履歴にも保存しない。実行したツール名は resolver に渡す `called_tool_names` に含める。ツールなしの会話では WARNING ログを出して無視する。
  渡さなければ従来どおり

- 拡張がデフォルトのプロンプトを持てるようにした（#281）。`Extension.default_prompt()` が言語ごとの本文
  （`{"ja": "...", "en": "..."}`。特定の言語は必須ではない。既定は `None`）を返すと、`client_type` が `task` 以外の会話で、既存の
  システムプロンプト・クライアント固有プロンプト・会話プロンプトの後ろ、登録チャンネルの一節の前に、拡張のロード順で
  空行区切りに注入する。本文は `ui.locale` に一致する言語、無ければ `ja` → `en` → 定義の先頭の言語の順に選ぶ。形が不正（`dict` でない・空・
  文字列以外を含む）な拡張はロード時に失敗する。メソッドの追加のため `EXTENSION_API_VERSION` は変えていない
- MongoDB の新しいコレクション `extension_overrides`（`extension` にユニークインデックス。起動時に作成）で、拡張ごとに
  プロンプトを上書きできるようにした（#281）。ドキュメントの `prompt`（文字列）がデフォルトの全文を置き換え、空文字は
  「注入しない」を意味する。`prompt` が文字列でない場合と読み込みに失敗した場合は WARNING ログを出してデフォルトを使う。
  上書きは言語を区別せず、キャッシュもしない（組み立てのたびに読む）。書き込み API・ダッシュボードの編集画面はまだ無い
- `extension_overrides` の拡張ごとのドキュメントの `tasks` キー（`{"<ツール名>": {"schedule": "<crontab>", "enabled": false}}`）で、
  拡張が `tool_config_roots()` で同梱したタスクの実行スケジュールと定期実行の有効・無効を、タスクごと・項目ごとに上書きできるようにした
  （#284）。上書きは起動時にスケジューラへジョブを登録するときに読む（キャッシュしない）。`enabled: false` はジョブを登録しないだけで、
  ツール YAML の `enabled: false` と違いツールはロードされ `!runtask` で実行できる。不正な値・拡張が同梱していないツール名・読み込みの
  失敗は、拡張名とツール名を含む WARNING ログを出してデフォルトにフォールバックする（起動は止めない）。コア組み込みのタスクは対象外。
  稼働中のスケジューラへ再起動なしで反映する非同期関数 `handlers/task_handler.py` の `apply_task_overrides(extension_name, task_name=None)`
  を追加したが、本番のコードから呼ぶ箇所はまだ無いため、現時点で上書きを変えたら再起動が必要
- `loaders/tool_paths.py` に、拡張が同梱したツール YAML の stem と拡張名の対応を返す `resolve_tool_config_owners()` を追加し、
  `load_all_tools()` の各エントリに同梱した拡張の名前（`extension`。無ければ `None`）を入れるようにした（#284）

### Changed

- **BREAKING**: `!toolresult` の結果を Discord 以外の配送先（拡張が `result_deliveries()` で登録したもの）へ届けるときは、
  先に会話履歴へ保存してから配送関数を呼ぶようにし、配送関数の型 `DeliveryFn` を
  `Callable[[str], Awaitable[None]]` から `Callable[[str, SavedConversation], Awaitable[None]]` に変えた（#291）。
  2 つ目の引数は保存したエントリの `_id` と `time` で、`list_for_client()` が返す同じ項目の値と一致する。配送関数が例外を
  投げても保存済みの履歴は残す。Discord への配送は従来どおり送信してから保存する。`EXTENSION_API_VERSION` を 3 に上げたため、
  `api_version = 2` を宣言した拡張はロード時に失敗する（配送関数を 2 引数に直して `api_version` を 3 にするか、宣言を外す）
- `handlers/task_handler.py` の `start_scheduler()` を非同期関数にした（#284。起動時に MongoDB の上書きを読むため）。
  呼び出し側は `await` が必要
- 会話履歴の `time` をミリ秒単位に切り捨てて保存するようにした（MongoDB の保存精度と同じ。#288）
- `ConversationRepository.list_for_client()` の並び順を `(time, _id)` の複合キーにし、同じ `time` のドキュメントの順序を
  決定的にした（#288）。あわせて起動時に `conversations` へ `(time, _id)` の複合インデックスを作る

## [0.7.1] - 2026-10-25

### Added

- LLM 呼び出しが一時的に失敗したとき（HTTP `429` / `500` / `502` / `503` / `504`・接続エラー）、同じ具体プロバイダーへ
  同じリクエストを自動で送り直すようにした（#253）。**既定で有効**（2 回まで）で、Chat Completions・Responses API・
  Ollama ネイティブのすべての経路に効く。具体プロバイダーごとに `llm.providers.<キー>.max_retries`（既定 `2`。`0` で従来どおり
  再試行しない）・`retry_backoff_seconds`（既定 `1`。指数バックオフ＋ジッターの基準秒数）・`retry_max_wait_seconds`（既定 `30`。
  1 回の待ちの上限で、`Retry-After` がこれを超えたら再試行せずに失敗させる）・`retry_on_timeout`（既定 `false`。料金の二重計上を
  避けるため、タイムアウトは既定で再試行しない）を書ける。それ以外の `4xx`・応答の解釈失敗・送信前検査の拒否は再試行しない。
  送信前検査は最初の 1 回だけ行う。再試行のたびに WARNING ログを出し、最終的な失敗は従来どおり `LLMError` になる。使用量は
  成功した 1 回分だけを記録する。`type: resolver` に書くと起動時に失敗する。再試行の残りがある試行の HTTP エラーは ERROR では
  なく WARNING で記録する（最後の試行は従来どおり ERROR）
- `send_http_request()` に任意引数 `http_error_log_level`（既定 `logging.ERROR`）を追加した（#253）。HTTP エラーを記録するログ
  レベルを呼び出し側で下げられる

- `type: ollama` のプロバイダーに `llm.providers.<キー>.keep_alive` を追加した（#261）。文字列（`30m` など）か
  整数（秒。`-1` は無期限、`0` は即時アンロード）で、設定するとそのプロバイダーへの呼び出し（tools なしの
  `/api/chat`、tools 付きの `/v1/chat/completions`）のボディのトップレベルに付く。未指定なら送らない。
  `openai_compat` / `resolver` に書いた場合と、文字列・整数以外（`bool` など）の値は起動時に失敗する
- `llm.on_tool_call_limit`（`summarize` / `message`。**既定 `summarize`**）を追加した（#255）。ツール呼び出しの往復が
  上限に達したとき、ツールなしのまとめを返すか（`summarize`）、従来の固定文言だけを返すか（`message`）を選ぶ。
  それ以外の値は起動時に失敗する

### Changed

- ツールが失敗結果（`success: False`）を返したとき、`error` の詳細も LLM への tool メッセージに含めるようにした（#258）。
  これまでは `data` → `memory_entry` しか見なかったため、`tool_error()` に渡したエラー詳細が LLM に届かず
  （`memory_entry` が `None` のツールでは空文字になっていた）、LLM が失敗理由を推測するしかなかった。`memory_entry` と
  `error` が異なれば `"{memory_entry}: {error}"`、同じか `memory_entry` が無ければ `error` だけを載せ、失敗時に `data` が
  あれば次の行に続ける。成功時は変わらない。あわせて、ツールループ（会話・`llm_expert` 共通の `services/tool_loop.py`）で
  失敗結果を受け取ったら、ツール名と `error` を WARNING ログに出す
- ツール呼び出しの往復が上限（`llm.max_tool_call_iterations`・定期タスクの `max_tool_call_iterations`）に達したとき、
  既定では固定文言の代わりに、ツールなしで LLM を 1 回だけ呼んでここまでの結果をまとめさせた本文を返すようにした（#255）。
  指示文で、冒頭での打ち切りの伝達・分かったこと / できたこと / できていないことの区別・未完了を完了と書かないこと・
  続きを依頼できる旨を求め、本文の後ろに固定文言は付けない。META アクションの適用と再認証案内の付け足しは通常の
  最終応答と同じ。まとめは最後のループの具体プロバイダーが書き、`final_reply_provider` があれば指定先が書く（書き直しは
  重ねない。`llm_expert` の中では専門家のプロバイダーのまま）。まとめの呼び出しが失敗した・応答が空なら従来の固定文言を
  返す。共通部品 `services/tool_loop.py` で行うため通常会話と `llm_expert` の両方に効く。上限に達した回は LLM の呼び出しが
  1 回増える。従来の挙動に戻すには `llm.on_tool_call_limit: message` を書く。`run_tool_loop()` に任意引数
  `summarize_with_final_reply_provider` を足し、上限到達時は `ToolLoopResult.content` にまとめが入る（得られなければ None）

- LLM のツール呼び出しループを `services/tool_loop.py` の `run_tool_loop()` に共通化し、通常会話と組み込みツール
  `llm_expert` の両方がこれを使うようにした（#254）。拡張が自前のエージェント的なツールを作るときにも使える。
  これにより `llm_expert` の挙動が会話側に揃う: ツール呼び出しを `finish_reason` ではなく `tool_calls` の有無で
  判定し、引数の JSON が壊れた呼び出しはツール全体を失敗させずにエラー内容を LLM へ返して続ける。上限到達時の
  中断の文言は `ui.locale` のカタログ（`tool.llm_expert.tool_call_limit`）から出す。`needs_auth` での打ち切り・
  固定のプロバイダー・戻り値の形は変わらず、resolver の再判定と `final_reply_provider` による書き直しは引き続き
  会話の経路だけで動く。`build_tool_message_content` の実体は `services/tool_loop.py` へ移した
  （`services/conversation_service.py` からも従来どおり import できる）
- `llm.providers.<キー>.final_reply_provider` の書き直しで、書き直し先にも元のツールループと同じツールを渡すようにした（#259）。
  書き直し先がツールを呼べば実行し、そのまま書き直し先でツールループを続ける（以降は resolver で再判定せず、
  書き直し先の `final_reply_provider` も見ない）。往復回数は元のツールループと通算で `llm.max_tool_call_iterations`
  まで。書き直しの指示文（user メッセージ）は足さなくなり、捨てた最終応答を除いたメッセージ列をそのまま渡す。
  書き直し先がツールを呼ばなければ、従来どおり問い合わせ 1 回で最終応答になる

### Removed

- Ollama の起動確認（WOL）処理と、`llm.providers.<キー>.wakeup_file` / `wol_sleep_seconds` 設定を削除した（#256）。
  `type: ollama` のプロバイダーへ送る前に `/api/version` へ疎通確認を送らなくなり、本来のエンドポイントへの
  1 回だけを送る。ホストの起動が必要なら利用側で用意する。設定に `wakeup_file` / `wol_sleep_seconds` が
  残っていても起動は失敗せず、無視される

## [0.7.0] - 2026-10-18

### Added

- ツールループの最終応答を別プロバイダーで書き直す `llm.providers.<キー>.final_reply_provider` を追加した（#247）。
  具体プロバイダーに書くと、`run_conversation` のツールループでそのプロバイダーが最終応答（tool_calls なし）を
  返したとき、その本文を捨て、指定先の具体プロバイダーへツールなしで 1 回だけ問い合わせた結果を最終応答にする。
  入力は捨てた最終応答を除いたメッセージ列に、口調を揃え事実を変えないよう求める指示を足したもの。書き直し先の
  `final_reply_provider` は見ない（連鎖しない）。台帳に無い名前・resolver 型を指す場合と、resolver 型のエントリに
  書いた場合は起動時に落とす。未設定なら従来どおりで、ツールなしの会話・`llm_expert`・`chat_to_llm` を直接呼ぶ
  経路には効かない。説明は `docs/ja/llm-final-reply.md`
- `send_http_request` にキーワード引数 `allow_redirects`（既定 `True`＝従来どおり追従）を追加した。`False` にするとリダイレクトを
  追従せず、3xx を `aiohttp.ClientResponseError` として送出する（`status` に 3xx、`headers` に `Location`）。
  POST が 301 / 302 / 303 で GET に変わりボディを失ったまま成功扱いになるのを避けたい呼び出し側向け。
  `stream_http_request` は対象外。
- LLM のトークン使用量の記録を追加した（#239）。Chat Completions（tools 付きを含む）・Responses API・
  Ollama ネイティブのどの経路でも、応答の入力・出力・合計トークン数（応答に無ければ不明として残し、推定は
  しない）と生の `usage`（数値だけ）を、プロバイダー名・モデル名・入口・所要時間とともに MongoDB の
  `llm_usage` コレクションへ 1 呼び出し 1 件で記録する。メッセージ本文・プロンプト・ツール結果は記録しない。
  書き込みは非同期で、失敗しても WARNING ログだけで LLM 呼び出しは成功扱いのまま。入口は `contextvars`
  （新設の `lilla_core.core.llm_call_origin`）で渡し、会話（`conversation:<client_type>`）・定期タスク
  （`task:<YAML stem>`。`!runtask` / `POST /api/runtask` を含む）・LLM ツール（`tool:<YAML stem>`）・
  `selftest` / `toolresult` を区別する。公開関数の引数は変えていない。設定は `llm.usage_log`
  （`enabled` 既定 `true` / `retention_days` 既定 90。各記録の `expire_at` に反映し TTL インデックスで消す）。
  具体プロバイダーに任意の単価 `input_price_per_million_tokens` / `output_price_per_million_tokens`
  （100 万トークンあたり。0 以上。resolver 型には書けない）を書くと概算コストも記録・表示する。
  ダッシュボードに Usage 画面（`#/usage`、`GET /api/usage?days=N`）を足し、日別・プロバイダー別・入口別の
  集計を出す。Home には直近 24 時間の合計を一行足した（`GET /api/dashboard/status` の `llm_usage`）。
  説明は `docs/ja/llm-usage.md`
- LLM が自分でスキルを読み込む組み込みツール `llm_load_skill` を追加した（#227）。`${CONFIG_ROOT}/tools/` に
  `type: llm_load_skill` の YAML を置き `tools.sets` に含めた場合だけ使える opt-in で、引数は `name`（必須。
  `!skill` と同じ規則で大文字小文字を区別しない）と `reason`（任意。結果には使わない）。結果は `!skill` と同じ
  「システムからの動作指示」の形式（追加指示なし）で、キャッシュしない。このツールが LLM に渡される会話では、
  有効なスキルの名前と説明の一覧（本文は含めない）をシステムプロンプトへ足す。一覧の上限はツール YAML の
  `max_skills`（既定 50）/ `max_description_chars`（既定 200）/ `max_list_chars`（既定 4000）で変えられ、
  0 以下や数値でない値は起動時に落とす。説明は `docs/ja/skills.md`
- 公式拡張パックにローカルのファイル読み書きの拡張 `lilla_core.extensions.files`（`name = "files"`。
  他の拡張に依存しない）を追加した（#233）。`lilla.yaml` の `extensions.files` にホワイトリストの
  ディレクトリ（`read_only_dirs` / `writable_dirs`。絶対パスの文字列か `{path, name}`。入れ子にすると
  最も深いディレクトリで判定する）と `max_read_chars`（既定 100,000 文字）を書く。ホワイトリストが
  空ならすべて拒否する。LLM ツールは `llm_file_list`（直下の一覧）・`llm_file_read`（UTF-8 テキストの
  読み取り。BOM は除去、`offset` / `limit` で続きを読む、バイナリ・非 UTF-8 は中身を返さない）・
  `llm_file_write`（`create` / `overwrite` / `append`。書き込み可能ディレクトリの配下だけ。UTF-8 として読めないファイルには追記しない）・
  `llm_file_search`（中身のキーワード検索）の 4 つで、YAML は利用者の `${CONFIG_ROOT}/tools` に置く。
  パスは「ディレクトリの名前/相対パス」の形でやりとりし、`..`・絶対パス・シンボリックリンクで外へ出る
  アクセスは実体パスで判定して拒否する。想定外の例外は文字列を LLM へ返さず、エラー通知チャンネルへ
  流す。説明は `docs/ja/files.md`
- 公式拡張パックに Google Drive / Google Sheets の拡張 `lilla_core.extensions.google_drive`
  （`name = "google-drive"`、`requires = ("google-oauth",)`）を追加した（#231）。`LILLA_EXTENSIONS` に
  `google_oauth` より後ろで並べ、`lilla.yaml` の `extensions.google_drive` にアクセスを許すフォルダ ID
  （`read_only_folders` / `writable_folders`。配下すべてが対象で、最も近いフォルダで判定する。
  共有ドライブはドライブ ID を書けば対象になる）を書く。ホワイトリストが空ならすべて拒否する。
  LLM ツールは `llm_google_drive_search_text`（テキストファイルの全文検索）・
  `llm_google_drive_get_text`（本文の取得）・`llm_google_drive_write_text`（新規作成・上書き。
  新規作成では作成先フォルダが必須）・`llm_google_sheets_get_range`（スプレッドシートの範囲の
  読み取り）の 4 つで、YAML は利用者の `${CONFIG_ROOT}/tools` に置く。扱うのは `text/*` の
  ファイルだけで、それ以外とゴミ箱のファイルは存在しないものとして扱う。スコープは
  `drive.readonly` と `spreadsheets.readonly` を基本に、書き込み可能フォルダがあれば
  `write_scope`（`file` 既定で `drive.file`＝この拡張が作ったファイルしか上書きできない /
  `full` で `drive`）に応じて足す（`file` では、この拡張が作っていないフォルダへの新規作成も
  拒否される可能性があり、拒否されたときはその旨のエラーを返す）。設定変更で必要なスコープが増えたら次の呼び出しで再認証を求める。
  本文と Sheets の値は `max_read_chars`（既定 100,000 文字）で切り詰める。説明は `docs/ja/google.md`
- `send_http_request` に生のボディを送る `body`（str / bytes）引数を追加した（#231）。Content-Type は
  `headers` で渡し、`data` / `form_data` より優先する。DEBUG ログには中身を出さず長さだけを出す

- `!` コマンドを Discord 以外のクライアントから実行できるようにした（#229）。トランスポート
  非依存の入口 `lilla_core.handlers.command_handler.dispatch_command(text, ctx)` と、呼び出し元を
  表すコマンドコンテキスト `lilla_core.commands.context.CommandContext`（`reply` / `reply_error` /
  `start_conversation` / `mark_failed`、`client_type` / `author_id` / `is_owner` / `attachments` /
  `discord_message`）を追加した。拡張は `CommandContext` を継承して `deliver_reply` /
  `deliver_error` / `deliver_conversation` を実装し、認証済みの接続についてのみ `is_owner=True` で
  `dispatch_command` を呼ぶ（承認判定は行わず、`is_owner` が `False` なら何も実行しない）。
  戻り値の `CommandResult`（`status` が `ok` / `error`、`replied`、`conversation_started`）で、
  コマンドが何も返信しなかった場合も完了・失敗を判別できる。未知のコマンド・ハンドラ内の例外は
  従来どおり `notify_error` に出て結果が `error` になり、例外の文字列は呼び出し元へ渡さない。
  `tools` / `bot` を省略したときは起動時に `set_command_runtime()` で登録した値を使う
  （`bot.py` がツールのロード直後に登録する）。説明は `docs/ja/commands.md`
- `register_command(name, discord_only=True)` で Discord 専用のコマンドを宣言できるようにした
  （#229。判定は `is_discord_only()`）。Discord 以外から呼ばれると実行せず、
  `command.handler.discord_only` の文言を `reply_error` で返す。`!selftest` は添付付きで結果を
  返すため Discord 専用にした

- 引数なし（空白のみを含む）の `!skill` で、呼び出せるスキルの一覧を返すようにした（#224）。
  LLM は呼ばず、有効なスキルだけを名前の昇順で、説明があれば `` - `名前`: 説明 ``（説明の改行は
  空白に置き換える）、無ければ `` - `名前` `` の行で並べ、見出しと呼び出し方の案内を添えて同じ
  チャンネルへ返信する。0 件なら `command.skill.list_empty` の文言を返す。1 メッセージに収まらない
  一覧は行の境界で 1900 文字以内に分割して続けて送る。これに伴い、以前の使い方案内
  `command.skill.usage` をカタログから削除し、`command.skill.list_header` / `list_footer` /
  `list_empty` を足した

- スキル機能を追加した（#222）。名前付きの再利用プロンプトを MongoDB の `skills` コレクション
  （`name` にユニークインデックス）へ保存し、`!skill <スキル名> [追加指示]` で呼び出す。スキルは
  ユーザーの発言ではなくシステムからの動作指示と分かるラッパー（ja なら
  `[システム指示: スキル「<スキル名>」]`・前置き文・本文・`[/システム指示]`。文言は `ui.locale` の
  カタログ `command.skill.instruction_*` から取る）で囲んで展開され（追加指示は空行を挟んで
  `追加指示: ` のラベル付きで後ろに続き、空なら付けない。#225）、展開形式は
  `services/skills.py` の `format_skill_instruction` 1 か所で組み立てる。#225 より前の
  `---SKILL: <スキル名>---` 〜 `---END_SKILL---` 形式で保存済みの会話履歴は移行しない。展開した内容は
  そのターンの最新の user メッセージとして通常の会話と同じ流れ（履歴保存・入力中表示・分割送信・
  同じチャンネルの返答中タスクのキャンセル）で LLM へ渡り、展開後の内容が会話履歴に残る
  （システムプロンプトには入れない）。スキル名は半角英数字・`_`・`-` のみの 64 文字以内で、大文字
  小文字を区別せず小文字に正規化して照合する。存在しない・無効なスキルは LLM を呼ばずに元の
  チャンネルへエラーを返す。オーナー以外からの `!skill` は他のコマンドと同じく承認フローの対象。
  観測用ダッシュボードに組み込みの **Skills** 画面（`#/skills`。`GET` / `POST /api/skills`、
  `PATCH` / `DELETE /api/skills/{id}`）を足し、一覧・追加・編集・削除・有効 / 無効の切り替えが
  できる（規則に合わない名前は 400、重複は 409）。これに伴い `skills` を拡張の予約名
  （`RESERVED_EXTENSION_NAMES`）に加えたため、`name = "skills"` の拡張はロード時に失敗する

- `llm.response_prefix_strip_patterns`（正規表現のリスト）を追加した（#217）。Discord の通常会話で
  LLM 応答を分割した各ブロックの先頭から、書いた順にパターンを除去する（先頭にだけマッチさせるため
  `^` は不要。文中・末尾の一致は残す。除去して空になったブロックは送らない）。省略時の既定は従来の
  英語月名形式 `[Apr 28 11:22]` に加えて `[HH:MM]` 形式で、モデルが自発的に付けた `[19:14] ` のような
  prefix も消えるようになった。空リストなら除去しない。不正な正規表現は起動時に失敗する

- PR がマージされたとき、外部の Webhook へ要点を JSON で POST する GitHub Actions workflow
  `notify-grok-bot-merged.yml` を追加した（#213）。対象は `develop` 向け（主）と `main` 向けの
  マージで、マージされずに閉じた PR・fork からの PR・`release-prep` PR・`develop` ↔ `main` の
  同期 / リリース PR（リリース通知は `release.yml` が担当）は通知しない。送り先と認証は
  レビュー用・リリース用とは別の Secrets `GROK_BOT_CORE_MERGED_WEBHOOK_URL` /
  `GROK_BOT_CORE_MERGED_WEBHOOK_AUTHORIZATION` で、どちらか一方でも未設定なら警告を出して
  スキップする（ジョブは失敗しない）。送信の失敗はジョブを失敗させる。payload は
  `action`（`"merged"` 固定）/ `owner` / `repo` / `repository` / `pr_number` / `title` / `url` /
  `base` / `head` / `author` / `merged_by` / `merged_at` / `merge_commit_sha` / `labels`（ラベル名の
  配列）/ `body`（無ければ空文字。4000 文字で切り詰め）/ `body_truncated`（切り詰めたら `true`）

- 具体プロバイダー（`ollama` / `openai_compat`）のエントリに `timeout_seconds`（LLM の応答を待つ上限の
  秒数。既定 300 秒で、これまでの固定値と同じ）を書けるようにした（#238）。これまで resolver だけが
  読んでいた項目で、resolver の既定は従来どおり 10 秒。0 以下はどの型でも起動時に落ちる

### Changed

- **BREAKING**: `google-calendar` 拡張の LLM ツールを、提供元を接頭辞に含む名前へ改名した（#242）。
  ツール名（YAML の `type:`・`tools.sets` に書く名前）は `llm_calendar_get` → `llm_google_calendar_get`、
  `llm_calendar_create` → `llm_google_calendar_create`、LLM に渡す関数名は `get_calendar_events` →
  `google_calendar_get_events`、`create_calendar_event` → `google_calendar_create_event`。旧名の互換
  エイリアスは無く、旧名の `type:` は未知のツールとして扱われる。`${CONFIG_ROOT}/tools/` の YAML の
  ファイル名と `type:`、`lilla.yaml` の `tools.sets` を書き換えること。`Extension` 契約の変更ではないため
  `EXTENSION_API_VERSION` は上げていない

- 拡張の `name` の予約名（`RESERVED_EXTENSION_NAMES`）に `usage` を足した（#239）。ダッシュボードの
  組み込み画面 Usage（`#/usage`）と経路が重なるため。`name = "usage"` の拡張はロード時に落ちる

- `api/llm_client.py` の LLM 送信経路をプロバイダーアダプターに統一した（#238）。Ollama ネイティブ
  （`/api/chat`）・Chat Completions・Responses API をそれぞれアダプター（リクエストの組み立てと応答の
  解釈）にし、送信前検査 → Ollama の WOL → HTTP 送信 → 例外ラップは共通の送信関数 1 か所で行う。
  `type` からアダプターへの対応表も 1 か所にまとめた。`chat_to_llm` / `chat_to_llm_with_tools` /
  `chat_to_llm_responses` のシグネチャと戻り値の形、送るリクエストの内容は変えていない

- **BREAKING**: コマンドハンドラの第 1 引数を Discord のメッセージからコマンドコンテキスト
  （`CommandContext`）に変え、シグネチャを `(ctx, arg, tools, bot)` にした（#229）。拡張が
  `command_packages()` などで登録したコマンドも書き換えが必要で、返信は `message.reply` ではなく
  `ctx.reply` / `ctx.reply_error`、添付は `ctx.attachments` を使う。ボタン付きメッセージの書き換え
  など Discord のメッセージそのものが要るコマンドは `discord_only=True` で登録して
  `ctx.discord_message` を使う。`Extension` のメソッドではないため `EXTENSION_API_VERSION` は
  上げていない。Discord での各コマンドの返信先・承認フロー・`notify_error` への出力は変わらない
  - `handle_command(message, content, tools, bot)` は Discord 用のコンテキストを組み立てて
    `dispatch_command` を呼ぶ薄いラッパーになり、`CommandResult` を返す。`content` が `!` で
    始まらないと `ValueError` になる（以前は先頭 1 文字を捨てて未知のコマンドとして扱っていた）
  - `!model` の未定義名、`!skill` の存在しない・無効なスキル、`!` だけの入力は `ctx.reply_error`
    で返す（Discord では従来どおり元メッセージへの返信）
  - `!skill` は展開した内容を `ctx.start_conversation` へ渡す（Discord では従来どおり
    `message_handler.start_conversation` の会話フロー）。スキル一覧の分割送信は Discord 用の
    コンテキストが行う（1 通目は返信、2 通目以降は同じチャンネルへ送信。従来と同じ）
  - `!runtask` / `!mongodata` / `!toolresult` の、`notify_error` だけで報告していた入力エラー
    （`!toolresult` の correlation_id なし・依頼が見つからない場合を含む）は、Discord では
    従来どおり `notify_error` のみで、結果を `error` にする
  - `commands.runtask.run_task` は実行できたら `True`、ツールが見つからない・手動実行に
    非対応・実行中の例外（`notify_error` 済み）なら `False` を返すようにした

- LLM へ渡す会話履歴が `memory.max_history_turns` の件数制限に掛かったとき、切り出し位置を
  「時の境界」（`ui.timezone` の HH:00）に揃えるようにした（#219）。直近 `max_history_turns` 件の
  うち最も古い発言が属する時の開始で切り、その時刻以降の発言は件数制限で切られていたものも含めて
  すべて渡す。これにより同じ時の中では新しい発言が来ても履歴の先頭が動かず、LLM の前綴キャッシュが
  効きやすくなる（時刻表示が時単位でマスクされているのと揃えた）。そのため返す件数は
  `max_history_turns` を超えることがあり、`max_history_turns` は上限ではなく「最低でもこの件数まで
  さかのぼる」目安になる（絶対上限は設けない）。件数制限が効かない場合と `max_history_turns` が 0 以下の
  場合は従来と同じ結果になる。`memory.history_days` の日数制限（日数制限で落ちた発言は時の境界の
  基準にも使わない）とタイムスタンプの付与は変わらない

- `google-calendar` 拡張の LLM ツール `llm_calendar_get` / `llm_calendar_create` の戻り値（`data`）を、
  予定ごとに `calendar`（取得元・作成先の `friendly_name`。`calendars` が空の後方互換モードでは
  `calendar_1` などの汎用名）・`summary`・`start`・`end`（文字列。終日の `end` は最終日を含む日付）・
  `all_day` と、値があるときだけ `status`・`location`・`description` に絞った（#236）。
  `organizer` / `creator` / `attendees` / `htmlLink` / `iCalUID` / `id` などそれ以外の項目は返さない。
  `primary` カレンダーの予定に入っている本人のメールアドレスが LLM へ渡り、送信ブロックリストで
  会話が止まっていたため。作成ツールの `memory_entry` にも `htmlLink` を載せない。説明は `docs/ja/google.md`
  - **BREAKING**: `GoogleCalendarClient.get_events()` の戻り値を、予定の dict のリストから
    （取得元カレンダー ID, 予定）の組 `CalendarEvent`（`calendar_id` / `event`）のリストに変えた。
    このメソッドを直接呼んでいる拡張は書き換えが必要。`Extension` 契約の変更ではないため
    `EXTENSION_API_VERSION` は上げていない

## [0.6.0] - 2026-10-10

### Added

- `llm.providers` の具体プロバイダー（`ollama` / `openai_compat`）に任意の `prompt` を追加した
  （#209）。値は `prompt.system` と同じ source spec（`file:` / `dir:`、`${config_root}` 展開、
  相対パスは `CONFIG_ROOT` 基準）で、そのプロバイダーへ実際に送る回だけ、システムプロンプトの
  末尾（`client_type` の追記のあと）へ空行区切りで足す。`chat_to_llm` / `chat_to_llm_with_tools` /
  `chat_to_llm_responses` の中で、送る具体プロバイダーが決まってから呼び出しのたびにディスクから
  読むため、`run_conversation` のツールループで resolver がプロバイダーを替えた呼び出しや、
  resolver 名の直呼びで `fallback` に落ちた回も、実際に送るキーの追記になる。`file:` / `dir:` で
  始まらない値と空の指定は起動時に落とす。`type: resolver` のエントリに書いた `prompt` は
  起動を落とさずに無視する

- `type: resolver` の展開を、`run_conversation` のツールループ内で LLM を呼ぶたびに
  （最初の呼び出しを含む）やり直すようにした（#207）。`LlmResolveContext` に、その回しで
  既に呼んだツール名（出現順・重複あり。引数や結果は載せない）の `called_tool_names` と、
  前回選んだ具体プロバイダー名の `previous_provider`（最初は `None`）を足した。同じ
  プロバイダーを続けるにはスクリプトが `previous_provider` を返す（`None` は従来どおり
  `fallback`）。これまで 1 回目の判定だけで足りていたスクリプトもそのまま動くが、ツールを
  呼んだ回しでは複数回呼ばれるため、判定モデルを呼ぶような重いスクリプトは
  `previous_provider` があれば前回の名前を返すようにするとよい。具体プロバイダーのキー
  （明示の `llm_name` や `!model` での固定）と、`chat_to_llm` を直接呼ぶ経路は従来どおり
  スクリプトを呼ばない

- `llm.send_blocklist_path`（任意）を追加した（#204）。JSON の文字列配列ファイルの絶対パスを
  書くと、`chat_to_llm` / `chat_to_llm_with_tools` / `chat_to_llm_responses` が送る直前に
  リクエストボディ内の文字列（ヘッダーと画像の data URL は除く）を NFKC 正規化・大文字小文字を
  無視して調べ、リストの語（部分一致）を含む回は送信せず `LlmSendBlockedError`
  （`LLMError` の派生）を送出する。一致した語・本文はログにも例外にも出さない。Discord の
  通常会話では止めた回のユーザー発言を会話履歴から取り除き、次の会話まで止め続けないようにする。リストは
  起動時に一度だけ読み、読めない・形式が不正な場合は起動を止める
  （`LlmSendGuardUnavailableError`）。相対パス・`file:` / `dir:`・`${config_root}` は
  起動時に拒否する。未設定なら従来どおり送る

- MongoDB に書くログで、例外付きのレコード（`logger.exception` / `exc_info=True`）は
  traceback を新しい `exception` フィールドに保存するようにした（#202）。`message` は従来と
  同じで、例外なしのログの文書は変わらない。長い traceback は末尾（例外の種類とメッセージ）を
  残して `MAX_EXCEPTION_TEXT_CHARS`（8000 文字）に切り詰める

- `lilla_core.handlers.http_server` の `LLM_TOOLS_KEY` を、拡張が `http_routes()` で
  載せたハンドラから LLM ツールレジストリを読むための公開名にした（#197）。
  `request.app[LLM_TOOLS_KEY]` で読める（文字列 `"llm_tools"` では読めない）。
  `TOOLS_KEY` / `BOT_KEY` / `ROUTE_AUTH_KEY` は従来どおり非公開
- 観測用ダッシュボードの Home に、生存確認（ダッシュボードの応答と MongoDB への ping。
  LLM は呼ばない）と直近 24 時間の `ERROR` / `WARNING` 件数を表示するようにした（#185）。
  取得元はログイン必須の `GET /api/dashboard/status` で、ログの本文は返さない。
  MongoDB に届かなくても Home は落ちず、該当の項目だけが失敗表示になる
- `task_scheduled_llm` の YAML に任意キー `max_tool_call_iterations` を追加した（#184）。
  そのタスクだけツール呼び出しの往復上限を上書きできる（正の整数。省略時は従来どおり
  `llm.max_tool_call_iterations`）。整数以外・bool・1 未満は起動時に失敗する
- `run_conversation` に任意引数 `max_tool_call_iterations` を追加した。`None`（既定）なら
  従来どおり `llm.max_tool_call_iterations` を使う

### Changed

- **BREAKING**: `run_conversation` の `tool_call_notifier` は、整形済みの文字列ではなく
  `(tool_name, depth)` を受け取るコールバック（`Callable[[str, int], Awaitable[None]]`）に
  なった（#200）。depth は直接呼び出しが 0 で、ネストするたびに 1 増える。あわせて
  `client_type` が `discord` 以外でも、notifier を渡せばツール呼び出しが通知されるように
  した（渡さなければ従来どおり通知しない）。Discord の `-# 🔧 {tool_name}` 行とネスト時の
  インデントは Discord 側（`handlers/message_handler.py` の `format_tool_call_line`）で
  組み立てるため、Discord の表示は変わらない
- **BREAKING**: 引数なしの `!model` は上書きを解除しなくなった（#186）。状態を変えずに、
  `llm.providers` に定義済みのプロバイダー一覧と今有効なモデル（上書きが無ければ
  `llm.default`）を返す。上書きの解除は `!model reset` で行い、解除後は従来どおり
  `llm.default` に戻る
  - `reset` は予約語になり、`llm.providers` のキーに使うと起動時に失敗する
  - 未定義の名前を指定したときに状態を変えず一覧を返す挙動・切り替えが効く範囲は変わらない
- **BREAKING**: 通常会話で LLM に見せるツールの許可リストを、名前付きツールセット
  `tools.sets` の `main` セットに置き換えた（#189）。`tools.main_available_tools` は廃止し、
  残っていると起動時に失敗する。中身を `tools.sets.main` へ移すこと
  - `main` が未設定（`tools.sets` が無い・`sets.main` が無い・`main: []`）なら、通常会話は
    **ツールなし** になった（以前の `main_available_tools` 未設定はロード済みの全ツールだった）。
    `$main` の展開結果も空になる。通常会話でツールを使うには `tools.sets.main` に stem を並べること
  - `main` 以外のセット名は自由（半角英数字と `-` `_` のみ。大文字小文字は区別）。
    空文字や `$` などを含む名前は起動時に失敗する
  - セットの要素はツール YAML の stem か、別セットへの `$` + セット名の参照。スケジュール LLM や
    `llm_expert` の `available_tools` からも `$main` と同じ形で `$health` のように参照できる
  - 展開は出現順・重複は先勝ち（LLM に渡す並びは従来どおりロード順）。参照の循環・未知のセット名・
    ロード済みでないツール名は、どこからも参照されていないセットも含めて起動時に失敗する
    （`lilla_core.loaders.llm_tool_loader.validate_tool_sets()`）
  - 利用側アプリケーションは `config.example/lilla.yaml` の `tools:` 節も更新すること

- MongoDB の非同期ドライバーを deprecated になった Motor から PyMongo Async
  （`pymongo.AsyncMongoClient`）へ移行した（#3）。依存は `motor==3.7.0` を外して
  `pymongo==4.18.2` に固定した。利用側アプリケーションも `motor` を外し、同じ `pymongo` の版に揃えること
  - クライアントの共通ファクトリ `lilla_core.repository.motor_client.create_motor_client` を
    `lilla_core.repository.mongo_client.create_mongo_client` に改名した。旧名の別名は残さないため、
    旧名を import している拡張・ホストは新名へ書き換えること。戻り値は `AsyncMongoClient`
    （`tz_aware=True`・プロセス内で 1 インスタンス共有は従来どおり）
  - 戻り値のコレクションを直接使う側は、`aggregate()` が Motor と違ってコルーチンになった点に
    注意すること（`cursor = await collection.aggregate(pipeline)`）。それ以外の
    `find` / `to_list` / `insert_one` / `update_one` / `find_one_and_update` などは書き方を変えずに使える
  - クライアントはスレッドセーフではなく、最初に使ったイベントループに結び付く。
    別スレッドへ渡さないこと
  - `config.example/logging.yaml` から `motor` ロガーの設定を外した（手元の `logging.yaml` に
    残っていても害は無い）
- 依存を更新した（`==` 固定は維持。Motor は据え置き）: aiohttp 3.11.14 → 3.14.3、
  discord.py 2.4.0 → 2.7.1、pydantic 2.11.1 → 2.13.5、pydantic-settings 2.8.1 → 2.15.0、
  tiktoken 0.9.0 → 0.14.0。利用側アプリケーションも同じ版に揃えること
- プロキシ認証を aiohttp 3.14 で非推奨になった `proxy_auth` / `BasicAuth` から、
  `Proxy-Authorization` ヘッダー（`aiohttp.encode_basic_auth(..., encoding="latin-1")`）を
  `proxy_headers` へ渡す形に変えた（`core/http_util.py`・`services/attachment_download.py`）。
  `services/attachment_download.py` の `resolve_proxy_settings()` の 2 番目の戻り値と
  `download_attachment_bytes()` の 3 番目の引数が `aiohttp.BasicAuth | None` から
  `dict[str, str] | None`（`proxy_headers`）に変わった。なお `bot.http.proxy_auth` は
  discord.py 2.7.1 が `BasicAuth` を要求するためそのまま
- 外部エージェントが結果を「FrontMatter だけ」と「本文なしのテキスト添付だけ」の 2 通に
  分けて送ってきた場合も `!toolresult` の承認フローへ載せるようにした（#187）。添付だけの
  メッセージを受けたときだけ同じチャンネルの直前 1 件を見て、同じ送信者の FrontMatter だけの
  メッセージ（UUID の `correlation_id`・本文も添付も無い）で、その ID が pending として実在する
  ときに限り添付の中身を BODY にする。待ち状態は持たず、条件を満たさない添付つきメッセージは無視する
  - FrontMatter だけ（本文も添付も無い）のメッセージは、`BODY未検出` のエラー通知を出さず何もしなくなった
  - 1 通の中に FrontMatter（または `!toolresult`）と添付がある場合、本文がある場合は従来どおり
  - `commands/attachment_body.py` の `_is_textual_attachment` / `_get_attachments` を
    `is_textual_attachment` / `get_attachments` として公開した

## [0.5.2] - 2026-10-03

### Added

- 共有 HTTP サーバー向け Bearer トークンの発行 CLI `python -m lilla_core.scripts.issue_client_token`
  を追加した（#176）。パッケージに同梱されるため `pip install` だけで使える。発行対象名
  （`--label`）の既定は `client_token_repository.DEFAULT_LABEL`（`default`）で、同じ label の
  既存トークンは上書き確認（`-y` で省略）のうえ置き換える（1 label = 1 token）。平文は実行時に
  一度だけ表示し、DB にはハッシュだけを保存する。詳細は `docs/ja/http-server.md`
  - ホストが別の label（例: `lilla-client`）で発行済みの環境は、`--label` で同じ名前を指定するか
    `default` で再発行して利用側の設定を揃える

- 共有 HTTP サーバーに `!selftest` 相当の診断 API `GET /api/selftest` を追加した（#168）。
  Bearer 必須で、`?full=true`（値なしの `?full` も同じ）を付けると LLM 疎通確認も行う
  （LLM API の課金が 1 往復分発生する）。本文は `{"ok", "mode", "summary", "checks"}` の
  JSON で、`summary` が Discord 本文の要約・`checks`（`name` / `ok` / `detail` /
  `elapsed_ms`）が添付ファイルの詳細に相当する。全チェック成功で 200、1 件でも失敗すれば 503
  - 実行するチェックの組み合わせは `services/system_checks.py` の新しい
    `run_selftest_checks(tools, full_mode)` に 1 か所だけ持ち、`!selftest` と共有する
    （Discord と HTTP で結果が食い違わない）
  - 公開の `GET /` は生存確認のまま変えていない（MongoDB 疎通だけを見て `{"status": ...}`
    のみ返す）。診断の詳細を返すのは認証の内側のこのエンドポイントだけ
  - 詳細は `docs/ja/http-server.md`

- 機械向けクライアントの共有 HTTP サーバー（`handlers/http_server.py`）を追加した（#172）。
  `bot.py` の `main()` が拡張の `setup()`・ダッシュボードのあとに起こし、Discord から
  抜けるときに止める。ダッシュボードとは別ポート・別認証で、設定は新しいコア確定の
  `http:` 節（`host` 既定 `0.0.0.0` / `port` 既定 `8080` / `cors_allowed_origins` 既定は空）。
  節を省略しても起動するため、**既存の構成でも 8080 番ポートを新たに listen する**
  （使っているポートと重なる場合は `http.port` を変える）
  - コアのエンドポイントは `GET /`（生存確認。公開）・`POST /api/tools/call`（LLM ツールの
    直接呼び出し）・`POST /api/runtask`（task ツールの手動実行）の 3 本。後ろ 2 本は Bearer 必須
  - 認証は既定拒否の Bearer トークン。トークンは新しい `repository/client_token_repository.py`
    が `client_tokens` コレクションに SHA-256 ハッシュだけを保持し（期限なし・同じ label の
    再発行で置き換え）、照合は `verify_client_token()`。発行の CLI はホスト側で用意する
  - 拡張は新しいメソッド `Extension.http_routes()` で `HttpRoute(method, path, handler, auth)`
    を申告してルートを足せる。パスの接頭辞に制約は無く、`auth` は `bearer`（既定）/
    `public` / `deferred`（ハンドラ側で別方式の認証）。コアのルートや他の拡張とメソッド・
    パスが重なるとロード時に失敗する。メソッドの追加なので `EXTENSION_API_VERSION` は据え置き
  - 詳細は `docs/ja/http-server.md`

- 専門家サブエージェントへ委譲する LLM ツール `llm_expert` をコア組み込みツール
  （`lilla_core/builtin_tools/llm_expert.py`）として追加した（#163）。lilla-agent の
  `tools/experts/llm_expert.py` から移したもので、`SCHEMA` / `execute` の契約は変えていない。
  YAML（`prompt` / `llm_provider` / `available_tools` / `api` / `grok_tools`）は従来どおり
  `${CONFIG_ROOT}/tools` に置き、`type: llm_expert`（ファイル名）と書く。ホストや拡張に同名の
  `llm_expert.py` があればそちらが優先される。詳細は `docs/ja/tools.md`

### Changed

- コア組み込みツール（`lilla_core/builtin_tools`）を**常に最後のツール探索ルート**として
  足した（#161）。ツール YAML の `type` にファイル名（`type: task_scheduled_llm` など）を
  書けるようになり、文書もそちらを正の書き方にした。従来の import パス形式
  （`type: lilla_core.builtin_tools.task_scheduled_llm`）も引き続き使えるため、既存の YAML は
  そのまま動く。探索ルートに入るだけでは有効にならず、`${CONFIG_ROOT}/tools/` に YAML を
  置いたときだけロードする opt-in は変わらない。同名ファイルの扱いは、ホストや拡張に同名が
  あればそちらが勝ち（組み込みは常に負ける側で、起動は止まらない）、組み込み以外どうしの
  同名は従来どおり起動時に失敗する
- 依存の版上げ（第1弾・パッチ／小幅）。pyyaml 6.0.3、python-dotenv 1.2.3、apscheduler 3.11.3、
  開発依存の pytest 9.1.1、pytest-asyncio 1.4.0 へ更新した（`==` 固定は維持）（#167）

### Fixed

- `type: task_scheduled_llm` で `available_tools` キーが無いときに起動が落ちていたのを修正し、
  省略時はツールなしとしてロードするようにした（#170）。`available_tools: []` は従来どおり
  ツールなしで、キーがあり値が不正なときは引き続き起動時に失敗する

### Added

- スケジュール実行で LLM に処理を委譲する組み込みタスクツール
  `lilla_core.builtin_tools.task_scheduled_llm` を足した（#157）。既定では読み込まれず、
  `${CONFIG_ROOT}/tools/task_*.yaml` に `type` を書いたときだけ opt-in で有効になる。
  YAML から `schedule`（cron）・`target`（通知先。`{DISCORD_MY_USER_ID}` は
  `discord.my_user_id` に置換）・`llm_provider`（必須）・`prompt`（必須。`file:` / `dir:` の
  source spec）・`available_tools`（必須。この実行で LLM に見せるツールの許可リスト。#159）を
  読み、`client_type="task"` で 1 往復させたうえで、返答が
  `NO_NOTIFICATION`（`*` で囲んだ形も含む）または空なら会話履歴にも残さず Discord へも
  送らない。それ以外の返答はアシスタント発言として会話履歴へ追記して `target` へ送る。
  プロンプト中の `{{now}}` は `ui.timezone` で解決した実行時刻に置き換わる。
  詳細は `docs/ja/scheduled-llm.md`（英訳 `docs/en/scheduled-llm.md`）
  - `available_tools` はキーが無ければ起動時に失敗し、`main_available_tools` を継承しない。
    空リストはツールなし、`$main` はその位置で `tools.main_available_tools` の中身（未設定なら
    ロード済みの LLM ツールすべて）に展開され、並べた stem と合わせて許可リストになる。
    ロード済みでない stem・`$main` 以外のトークンは起動時に失敗する。実際に渡すのはそのうち
    `supported_client_type` が `task` / `all` のツールだけで、`supported_client_type: task` の
    ツールをこのリストにだけ書けば、それを並べたタスクにだけ見せられる
- ツールの許可リストを展開する `lilla_core.loaders.llm_tool_loader.resolve_available_tools()`
  を足した（#159）。YAML stem と `$main` の並びを受け取り、`build_tools_param` の
  `allowed_names` に渡せる stem のリストを返す（`supported_client_type` ではここで絞らない）
- `run_conversation()` に `allowed_tool_names` 引数を足した（#159）。その回だけ LLM に見せる
  ツールの許可リストで、省略（`None`）時は従来どおり `tools.main_available_tools`、空リストなら
  ツールを渡さない
- `LILLA_EXTENSIONS` で公式拡張パックをモジュール名だけの短縮記法で指定できるようにした
  （#153）。例えば `google_oauth` は `lilla_core.extensions.google_oauth` と同じ意味になる。
  `.` を含まない項目がそのまま import できないときだけ `lilla_core.extensions.` を補って
  もう一度だけ import を試み、フォールバック対象は「その名前自体が見つからない」
  `ModuleNotFoundError` に限る（同名モジュールが見つかったうえで初期化に失敗した場合は
  公式へ逃げずその場で失敗する）。`.` を含むパス（第三者パックや、すでにフルパスで書いた
  公式拡張）はそのままのパスとして扱われ、フォールバックの対象にならない

## [0.5.1] - 2026-09-27

### Added

- 任意の判定ヘルパー `lilla_core.tool_support.jev` を足した（#134）。`ask_jev(state, questions)`
  が TypeSafe の System One モデル（Jev）へ POST を 1 本送り、Choice / Score / Noul の答えを
  型つき（`ChoiceAnswer` / `ScoreAnswer` / `NoulAnswer`）で返す。コアのどこからも import せず、
  TypeSafe SDK などの新しい依存も足していない。API キーは引数か環境変数 `TYPESAFE_API_KEY`、
  エンドポイント（既定 `https://api.typesafe.ai/v1/systemone`）とモデル（既定 `jev-latest`）は
  引数で上書きできる。キー欠落・HTTP 失敗・タイムアウト・応答破損は `JevError` の派生で投げる。
  詳細は `docs/ja/jev.md`（英訳 `docs/en/jev.md`）

- `llm.providers` に `type: resolver` を足し、回しごとに具体プロバイダーをホストの
  スクリプトで選べるようにした（#132）。キーの決め方（呼び出し側の `llm_name` →
  `!model` → `llm.default`）は変えず、決まったキーが resolver 型のときだけ
  `run_conversation` がスクリプトの `resolve(ctx)`（`LlmResolveContext`。`client_type` /
  チャンネル / 直近発話の要約 / `has_image` など）を呼んで展開する
  - 項目は `script`（`CONFIG_ROOT` 配下の `.py`。必須）・`fallback`（具体プロバイダー名。
    必須）・`timeout_seconds`（任意。既定 10 秒）。`None`・未知名・別の resolver 名・
    例外・タイムアウトは警告ログを出して `fallback` に落ち、会話は止まらない（深さ 1）
  - `script` が `CONFIG_ROOT` の外・ファイル不在・`resolve` 不在、`fallback` が台帳に
    無い / resolver 型、は起動時に失敗する
  - `run_conversation` を通らず `chat_to_llm` などへ resolver 名が直接渡った場合は、
    スクリプトを呼ばずに `fallback` を使う
  - コピーして使えるテンプレを `lilla_core/templates/llm_resolver.py` に同梱した。
    詳細は `docs/ja/llm-resolver.md`（英訳 `docs/en/llm-resolver.md`）

- 公式拡張パックの第 1 段として、Google OAuth2 と Google Calendar の拡張をコアに同梱した
  （#126）。ソースは `lilla_core/extensions/` 下で、`LILLA_EXTENSIONS` に import パス
  （`lilla_core.extensions.google_oauth` / `lilla_core.extensions.google_calendar`）を
  並べたときだけ読み込まれる。未指定なら今までどおりコア単体で起動する（別パッケージや
  extras にはしていない）
  - `google-oauth`: YAML の `extensions.google_oauth`（`client_id` / `redirect_uri`）と
    `GOOGLE_CLIENT_SECRET`（`cfg.env.google_client_secret`）を申告し、認可コードの戻り先
    `GET /oauth/google-oauth/callback` をダッシュボードの公開ルートとして載せる。
    他の Google API 拡張が継承する `GoogleOAuthClient`
    （`lilla_core.extensions.google_oauth.client`）を提供し、サブクラスは
    `CREDENTIAL_TYPE` と `SCOPES` だけを持てばよい。Calendar を載せなくても単独で使える
  - `google-calendar`（`requires = ("google-oauth",)`）: YAML の
    `extensions.google_calendar.calendars` と、LLM ツール `llm_calendar_get` /
    `llm_calendar_create`（YAML は利用者の `${CONFIG_ROOT}/tools` に置く）。タイムゾーンは
    `ui.timezone` を使い、独自の TZ 設定は持たない
  - コールバックは `state` を保存済みの値との完全一致だけで検証し、credential_type を
    固定の一覧で絞らない（`GoogleOAuthClient` を継承する任意の拡張の種別を受け付ける）
  - 詳細は `docs/ja/google.md`（英訳 `docs/en/google.md`）

### Changed

- MongoDB ログハンドラの書き込みをイベントループから外した（#2）。`setup_logging()` が
  `logging.yaml` の `mongodb` ハンドラを `QueueHandler` + `QueueListener` 経由に付け替え、
  `insert_one` は別スレッドで行う（ロガーに付くのは `QueueHandler`。`logging.yaml` の書き方は
  変わらない）。ログ文書の形・レベル別 TTL は従来どおり
- `mongodb` ハンドラがあるとき、起動時に MongoDB へ届かなければ約 5 秒
  （`serverSelectionTimeoutMS=5000`）で起動を失敗させるようにした（従来は pymongo 既定の
  約 30 秒待ってから落ちていた）。`mongodb` ハンドラが無ければ従来どおり接続しない
- `llm.providers` の `url` / `model` は `ollama` / `openai_compat` のときだけ必須になった
  （resolver 型では書けない）。具体プロバイダーに `script` / `fallback` を書くと起動時に
  失敗する（#132）
- `README.md` / `README.ja.md` を「何か・インストール・最低限の起動・拡張の紹介・ドキュメント
  目次へのリンク」までに短くし、登録チャンネル・部屋のノート・部屋名検索・タイムゾーン・
  観測用ダッシュボード・Discord ボットの設定・ツール契約・拡張 API などの詳細を `docs/ja/`
  （正）と `docs/en/`（英訳）へ移した（#121）。目次は `docs/ja/README.md` /
  `docs/en/README.md`。PyPI 上でもリンクが切れないよう、README からのリンクは GitHub の
  絶対 URL にした
- Message Content Intent が未有効のときの ERROR ログが案内する先を、README の節から
  `docs/en/discord-bot-setup.md` の URL に変えた

### Fixed

- 承認後に実行したコマンド（`!model` / `!cleardirty` / `!enable_tools` / `!disable_tools` など）の
  完了メッセージが、元の依頼チャンネルではなく承認チャンネルに付いていたのを直した（#5）。
  `ApprovedMessage.reply` が承認依頼メッセージへ委譲していたため、差し替えた `channel` が
  discord.py の `Message.reply()` に使われていなかった。自前の `reply` を持たせ、`custom_id` から
  解決した元チャンネルへ `send` する（解決できないときは従来どおり承認チャンネルへ落とす）。
  送信は元メッセージへの参照付き返信にしないので、相手 bot が「自分への返信」と見なして
  再応答することもない。実行内容の信頼境界は変わらず、元メッセージの再取得もしない

### Security

- 観測用ダッシュボードの初期設定（`POST /api/setup`）に一度きりのセットアップトークンを
  必須にした（#125）。管理パスワードが未登録のあいだ、起動時に推測できないトークンを生成して
  起動ログへ WARNING で一度だけ出し、ボディの `setup_token` がそれと一致したときだけ登録を
  受け付ける（不足・不一致は 403 でパスワードを作らない。ヘッダでの代替は無い）。初期設定画面
  にトークンの入力欄を足した。トークンは画面の HTML にも認証前の API 応答にも載せないので、
  ポートに先着しただけでは管理者パスワードを決められない（ログを読める人は決められる）。
  登録に成功したらトークンは捨て、未登録のまま再起動すると新しいトークンになる。既に
  パスワードを登録済みのデプロイは動作が変わらない

## [0.5.0] - 2026-09-25

### Added

- 観測用ダッシュボードの HTTP サーバーをコアが起動するようになった
  （`handlers/dashboard_server.py`）。`bot.py` の `main()` が拡張の `setup()` を
  await したあと、Discord へ接続する前に起こす（申告の集約が `load_extensions()` で
  済んでいること・拡張の起動が失敗したら観測窓も開かないこと、の 2 つが理由）。
  提供するのは初期設定 / ログイン / セッション Cookie による認証と、会話履歴・
  ユーザーメモ・ログの API、拡張のページを並べる `GET /api/dashboard/nav`、および
  #105 で入った拡張の申告の配線（`/static/ext/{name}/`（認証不要）・`/api/{name}`
  （Cookie 必須）・`/oauth/{name}`（認証の外側））。SPA（Alpine.js・ハッシュ
  ルーティング・拡張ページの `import()` + `mount()` / `unmount()`）は
  `src/lilla_core/dashboard/` にパッケージ同梱し、`locales/` と同じく wheel へ入る。
  拡張が 0 個でも組み込み 4 画面（Home / Conversations / Memos / Logs）で起動する
  （`Extension` の契約は変えていないので `EXTENSION_API_VERSION` は 1 のまま）
- コア確定の YAML セクション `dashboard:` を追加（`DashboardConfig`）。`host`
  （既定 `0.0.0.0`）・`port`（既定 `8765`）・`cookie_secure`（既定 `true`）の 3 つで、
  全項目に既定があるため `lilla.yaml` に節そのものが無くても起動する。
  **このポートは管理画面を開く。** パスワード未登録の間は `POST /api/setup` に
  先に到達した者が管理者パスワードを決められるブートストラップなので、公開
  ネットワークへ晒さないこと（前段でアクセス制御を掛けるか、`host` を
  `127.0.0.1` に絞る）。詳細は README と `SECURITY.md` を参照

### Changed

- **BREAKING**: 拡張の設定節名を `Extension.name` から導くようにし、`EXTENSION_API_VERSION` を
  **2** に上げた（#110。受け付けるのも 2 だけ）
  - `Extension.config_models() -> dict[str, type[BaseModel]]` を廃止し、
    `config_model() -> type[BaseModel] | None` に置き換えた。1 拡張が足せるモデルは 1 つで、
    節名は `name` のハイフンをアンダースコアに置き換えたもの（`google-oauth` →
    `extensions.google_oauth`。ハイフンの無い名前はそのまま）。節名を自分で書く API は無い。
    複数の設定を持つ拡張は 1 つのモデルの子としてまとめること
  - `Extension.required_config_sections()` を廃止した。他の拡張の節を読む依存は
    `requires`（拡張名）で表す。`required_env_fields()` / `required_tool_context_keys()` は残る
  - 廃止した 2 メソッドのどちらかを定義したままの拡張は、`api_version` を宣言していなくても
    代わりの経路を示してロード時に fail-fast する（黙って無視しない）
  - `config_model()` が `BaseModel` のサブクラス以外を返す拡張、および `name` が数字で始まり
    節名が識別子にならない拡張がモデルを申告した場合もロード時に fail-fast する
  - `core/config.py` に `extension_section_name(name)` を追加。`get_section()` は拡張の `name`
    （`get_section("google-oauth", GoogleConfig)`）でも節名でも引ける
  - `get_config_models()` は「導いた節名 -> モデル」を返す（`compose_config()` の引数の形は不変）
- **BREAKING**: 拡張が `config_models()` で申告した YAML セクションの置き場を、
  トップレベルからコア確定の `extensions:` の下へ移した（#109）。読み出しは
  `get_config().extensions.<節名>` で、トップレベルの `get_config().<節名>` は作らない。
  `lilla.yaml` も同じ形に書き換えること（`google:` → `extensions:` の下の `google:`）。
  `config_models()` の書き方（節名を自分で返す形）は変えていないので
  `EXTENSION_API_VERSION` は 1 のまま
  - 名前空間が別になったため、コア確定の節と同名のセクションを申告しても起動できる
    （これまでは予約名としてロード時に fail-fast していた）
  - `extensions:` の下に、ロードしていない拡張のキーがあれば起動時に落とす
    （払い残しの検出）。拡張が 0 個なら `extensions` は空
  - 申告した節をトップレベルに書いたままの場合も、移し忘れとして起動時に落とす
    （トップレベルの未知キーは無視されるため、既定値のまま気付かず動くのを防ぐ）。
    コア確定と同名のトップレベルキーはコアのものなので対象外
  - `get_section(name, model)` は `extensions:` の下だけを探すようになった。
    **コア確定の節（`get_section("ui", UiConfig)` など）は取れなくなった**ので、
    `get_config().ui` のように直接読むこと
  - `required_config_sections()` はコア確定の節名では満たされなくなった
    （コアの節は `extensions:` の下に無いため）。書いている場合は外すこと
  - `load_extensions()` を経ずに `get_config()` を呼んだとき（拡張をロードしない
    運用スクリプトなど）は、`extensions:` の中身を検証せずに捨て、コア確定の節だけを
    読む。どの拡張が載るか分からないためで、未知キーの検査は合成結果にだけ掛かる
- **コアの設計方針の線引きを更新した。** これまで「HTTP/ダッシュボードサーバーは
  コアに含めない」としていたが、観測用ダッシュボードはコアが所有することにした
  （見せる中身がすべてコアの状態のため）。用途特化のサーバー（機械向けの Bearer
  API・WebSocket クライアントなど）を拡張側に置く方針は変わらない。
  `dashboard` はコア確定のセクション名になったため、**同名の YAML セクションを
  申告している拡張はロード時に fail-fast する**（`config_models()` から外すこと）

### Added

- 拡張が観測用ダッシュボードへ差し込むための申告を `Extension` に追加。
  `dashboard_page()`（タブ 1 つ。`DashboardPage(label, group)` で `group` は
  `main` / `admin`）・`dashboard_static_dir()`（`/static/ext/{name}/` に載せる
  ディレクトリ。タブを出すなら直下に `page.js`）・`dashboard_routes()`（セッション
  認証の内側。パスは `/api/{name}` 配下のみ）・`dashboard_public_routes()`（認証の
  外側。パスは `/oauth/{name}` 配下のみ）の 4 つで、いずれも既定は「何も貢献しない」。
  経路の識別子は既存の `Extension.name` だけで、ハッシュ（`#/{name}` / `#/admin/{name}`）・
  API 接頭辞・公開コールバック・静的 URL・JS モジュール URL はコアが `name` から導出する
  （新しい ID 欄は作らない）。集約結果は `get_dashboard_pages()`（導出済みの
  `DashboardPageEntry`）・`get_dashboard_static_mounts()`・`get_dashboard_routes()` ・
  `get_dashboard_public_routes()` からロード順で読める。ルートは aiohttp の型ではなく
  コア独自の `DashboardRoute(method, path, handler)` で受け取り、`Extension` 契約を
  HTTP ライブラリのバージョンに縛らない。ダッシュボードの HTTP サーバー本体はコアには
  含まれず、申告を集めて配るところまでがコアの役目（メソッドの追加のみで非破壊。
  `EXTENSION_API_VERSION` は 1 のまま据え置き）

### Changed

- **`Extension.name` の形を検査するようになった。** `name` はダッシュボードの URL
  パス・ハッシュ・静的ディレクトリ名へそのまま埋まるため、`^[a-z0-9][a-z0-9-]*$` に
  合わない名前（大文字・アンダースコア・空白・`/`・`..` など）はロード時に `ValueError`
  で落ちる。加えて、組み込みの経路と衝突する名前（`api` / `oauth` / `static` / `admin` /
  `dashboard` / `auth` / `login` / `logout` / `setup` / `home` / `conversations` /
  `memos` / `logs`）を `RESERVED_EXTENSION_NAMES` として予約し、使った拡張は落とす。
  該当する `name` の拡張は改名が必要（既存の `google-oauth` / `google-calendar` /
  `lilla-agent` はいずれも影響を受けない）

## [0.4.1] - 2026-09-19

### Added

- 拡張が UI 文言カタログを同梱できる `Extension.locale_dirs()` を追加。ディレクトリに
  コアと同じ命名の `{locale}.yaml`（`ja.yaml` / `en.yaml` など）を置くと、
  `lilla_core.ui.messages` がコアのカタログへロード順に重ねて解決する。カタログの
  トップレベルのキーはその拡張の `name` ただ 1 つでなければならず（`name = "lilla-habits"` なら
  `t("lilla-habits.notify.title")`。コアが prefix を付けることはしない）、違反や拡張名とコアの
  トップレベルキーの衝突は、拡張の登録時（`set_extensions()`）に `ValueError` で fail-fast する
  （`t()` 自体は従来どおり例外を投げない）。1 つの拡張が複数のディレクトリを返した場合は、その
  拡張のノードをロード順に浅くマージする。存在しないディレクトリは WARNING を出して
  （ロケールごとに 1 回）読み飛ばし、壊れた YAML は ERROR ログを出してそのロケール分だけ空として扱う。
  `t()` の解決順（`ui.locale` → `ja` → キー名）は従来どおりで、`ja.yaml` しか同梱していない
  拡張でも `ui.locale: en` で例外にならない。`translations()` / `available_locales()` も
  合成後のカタログを対象にする（メソッドの追加のみで非破壊。`EXTENSION_API_VERSION` は据え置き）
- 拡張リポジトリ向けのテストヘルパー `lilla_core.testing` を追加。
  `use_extensions(*extensions, config_root=None)`（登録・設定合成・`set_config()` を行い、
  抜けるときに登録内容・設定インスタンス・`CONFIG_ROOT` を元へ戻すコンテキストマネージャ）と
  `write_minimal_lilla_yaml(directory, ...)`（コアが必須にしている項目だけの `lilla.yaml` を
  書き出す。`extra` で拡張が必須にしているセクションを深いマージで足せる）を提供する。
  `lilla_core.testing` 本体は pytest を import しないため、pytest の無い環境でも import できる
- pytest 向けの fixture を `lilla_core.testing.pytest_plugin` に追加（`lilla_config_root` /
  `lilla_extensions`）。`pytest11` entry point による自動登録はしないので、利用側は自分のルート
  `conftest.py` に `pytest_plugins = ["lilla_core.testing.pytest_plugin"]` と書いて opt-in する

- `Extension.tool_config_roots()` を追加。拡張が既定のツール YAML（`llm_*.yaml` / `task_*.yaml`）を
  同梱できるようになった。ローダーは「拡張の同梱分（ロード順）→ `${CONFIG_ROOT}/tools`」の順に
  YAML を集め、同じ stem は `${CONFIG_ROOT}/tools` 側が丸ごと上書きする（利用者の設定が常に勝つ。
  内容のマージはしない）。拡張どうしで同じ stem を同梱した場合は起動時に fail-fast する。
  同梱 YAML が `type: self` なら同梱ディレクトリの同名 `.py` を読む
- ツール YAML に `enabled: false` と書くとそのツールをロードしなくなった（同梱かどうかを問わない）。
  拡張が同梱したツールを止めるには、`${CONFIG_ROOT}/tools` に同じ stem で `enabled: false` の
  YAML を置く。`enabled` キーが無い、または `false` 以外の値なら従来どおりロードする

- ツール YAML の `type` に import パス（`.` 区切りのモジュール名。例:
  `type: lilla_google_calendar.tools.calendar_get`）を書けるようになった。`.` を含む `type` は
  ツールルートを探索する代わりに `importlib` で解決し、LLM ツール・task ツールの両方で使える。
  インストール済みパッケージ（PyPI 配布の拡張など）がツールを同梱するための経路で、通常の
  import のため相対 import が使え、ディレクトリの検査は行わない（インストール済みパッケージは
  `LILLA_EXTENSIONS` と同じ信頼レベル）。import に失敗した場合はファイルが見つからないときと
  同じく WARNING を出してそのツールだけスキップする。`.` を含まない `type` と `type: self` の
  挙動は従来どおり

- コア組み込みのサンプル LLM ツール `lilla_core.builtin_tools.llm_current_datetime` を追加。
  個人データ・外部サービスへの依存を持たない軽量なツールで、`${CONFIG_ROOT}/tools/` に
  `type: lilla_core.builtin_tools.llm_current_datetime` の YAML を置くと opt-in で有効化できる
  （コアは自動では読み込まない）。import パス指定でコア組み込みツールを使う実例として README に記載

- `lilla.yaml` の `discord.channels` に登録チャンネルのリストを追加。`name`（設定上の
  別名）・`channel_id`（snowflake 文字列）・`mention_optional`（既定 `false`）を並べると、
  `mention_optional: true` のチャンネルではオーナーのメンションなしの発言にも応答する。
  未設定・空リストなら受信動作は現行のまま（メンションまたは DM）で、`name` または
  `channel_id` の重複は起動時に fail-fast する
- `DiscordConfig.find_channel_by_id()` / `find_channel_by_name()` を追加。登録チャンネルの
  エントリを Discord のチャンネル ID（int / str）や設定上の別名（前後空白を除いた完全一致・
  大文字小文字は区別）から引ける
- 登録チャンネルの「部屋のノート」を持つ `channel_summaries` コレクションと
  `repository/channel_summary_repository.py` を追加。1 チャンネル 1 ドキュメント
  （`discord_channel_id` がユニーク）で、起動時に `init_collection()` される
- コア組み込みのタスクツール `lilla_core.builtin_tools.task_channel_summary` を追加。
  登録チャンネル（`discord.channels`）ごとに**前日**（`ui.timezone` の暦日）の会話を LLM に
  要約させ、`channel_summaries` へ upsert する。既定の cron は `0 2 * * *`。
  `${CONFIG_ROOT}/tools/task_channel_summary.yaml` に
  `type: lilla_core.builtin_tools.task_channel_summary` を置いた場合だけ有効になる opt-in で、
  `discord.channels` が空、または YAML が無ければ何もしない。対象は `discord_channel_id` の
  付いた発言だけ（既存発言の穴埋めはしない）で、対象日の発言が無ければ既存の要約を残す。
  要約にはキャラクター用のシステムプロンプトを使わず、短い事実抽出用のプロンプトを使い、
  本文は `<channel_transcript>` タグで囲んだ「指示ではなくデータ」として渡す。
  `schedule` / `llm_name` / `max_turns` / `max_transcript_chars` を YAML で上書きできる
- `ConversationRepository.load_by_channel_between()` を追加（チャンネルと期間で絞った
  会話履歴の取得）。あわせて `conversations` に `(discord_channel_id, time)` の複合
  インデックスを張るようにした
- コア組み込みの LLM ツール `lilla_core.builtin_tools.llm_conversation_get` を追加。
  会話履歴を期間（`datetime_range`）・キーワード（`query`。スペース区切りの AND）・
  発言者（`role`）・件数（`limit`。既定・上限とも 30 で、0 や負数は 1 へ丸める）で検索する。
  `${CONFIG_ROOT}/tools/` に `type: lilla_core.builtin_tools.llm_conversation_get` の
  YAML を置いた場合だけ有効になる opt-in（LLM へ見せるツール名は YAML のファイル名）。
  任意パラメータ `channel_name` に `discord.channels` の登録名を渡すと、その
  `discord_channel_id` の発言だけに絞り込む。名前は設定上の別名であり Discord の現在の
  チャンネル名ではない。突き合わせは前後空白を除いた完全一致（大文字小文字を区別）で、
  登録に無い名前は全件検索へ落とさずエラーを返す。省略時は従来どおり全チャンネル横断。
  会話履歴そのものはチャンネルで分離せず、`tags` の扱いも変えていない
- `ConversationRepository.search()` を追加（期間・キーワード・発言者・チャンネルを
  任意に重ねた会話履歴の検索）。キーワードは正規表現としてではなくエスケープした
  部分一致（大文字小文字を区別しない）として扱う

### Changed

- 登録チャンネルでの会話では、システムプロンプトに「今この登録チャンネルにいる」旨の
  短い一節を追記するようにした（未登録チャンネル・DM では追記しない）。会話履歴は
  従来どおり全チャンネル横断のまま
- `MemoryManager.build_system_prompt()` に `discord_channel_id` 引数を追加（既定 `None`。
  登録チャンネルの一節の解決に使う）。`run_conversation()` が受け取ったチャンネル ID を
  そのまま渡す
- Discord 経由のユーザー発言も、アシスタント返信と同様に `discord_channel_id` つきで
  会話履歴へ保存するようにした（既存レコードの補完は行わない）
- 登録チャンネルでの会話では、その部屋の要約（`channel_summaries`）があればシステム
  プロンプトへ `## Channel Note` として差し込むようにした。`summary_date` を併記し、
  本文は信頼しないコンテキストとして `<channel_note>` タグで囲む（未登録チャンネル・DM には
  出さない）。`load_conversation_history_with_timestamps()` の挙動は変えていない

### Fixed

- task ツールのトリガー種別を、`type` ではなく YAML のファイル名 stem から判定するように
  修正した。`type` に import パス（`type: some_package.tasks.daily_summary`）を書いた
  task ツールが `trigger: other` と判定され、`!runtask` から実行できなかった

## [0.4.0] - 2026-09-13

### Added

- `core/config.py` に `get_section(name, model, config=None)` を追加。拡張が
  `config_models()` で申告したセクション（コア確定のセクションも可）を、申告したモデルの
  型で取り出す。未申告の名前や、値がそのモデルのインスタンスでない場合は `ValueError`
- `Extension` 契約のバージョン `EXTENSION_API_VERSION`（現在 1）と、ロード時に受け付ける
  集合 `SUPPORTED_EXTENSION_API_VERSIONS` を `core/extension.py` に追加。拡張はクラス属性
  `api_version` で自分が書かれたバージョンを宣言でき（既定は現在のバージョン）、受け付けない
  値や整数以外を宣言した拡張は拡張名とバージョンを含むエラーでロード時に fail-fast する。
  契約の互換性ポリシー（何が非破壊で何が破壊的か）を README に明文化した
- `Extension.requires`（クラス属性。依存する拡張の `name` のタプル）を追加。依存先が
  ロードされていない、または `LILLA_EXTENSIONS` で自分より後ろに並んでいる場合はロード時に
  fail-fast する（コアは並べ替えない）。汎用の `validate()` フックは追加しない
- `Extension.required_env_fields()` / `required_tool_context_keys()` を追加。
  `required_config_sections()` と同じ形で、自分では提供しないが読む `EnvConfig` の
  フィールド名 / ツール実行 context のキー名を並べると、誰も提供しておらずコア確定の
  名前でもない場合にロード時に fail-fast する

### Changed

- task ツール（定期実行と `!runtask`）の実行 context にも、拡張の `tool_context_providers()`
  の値が入るようになった。これまでは LLM ツールだけに注入され、task ツールは `discord_client` /
  `now` / `llm_tools`（手動実行時は `params` も）の固定キーしか受け取れなかった。組み立ては
  `core/extension.py` の `build_tool_context()` に一本化し（`services/conversation_service.py`
  からの import は互換のため残す）、両経路で同じ注入モデルになる
- **BREAKING**: コアがツール実行 context へ注入するキー（`client_type` / `llm_tools` /
  `client_state` / `discord_channel_id` / `discord_client` / `now` / `params` / `call_tool` 等）を
  予約キーにし、`tool_context_providers()` で同名を提供する拡張はロード時に fail-fast する
  （これまではコアの注入で静かに上書きされていた）
- **BREAKING**: 会話開始フック（`Extension.conversation_start_hooks()`）の引数を、WebSocket
  クライアント集合（`ws_clients`）から `ConversationContext`（`client_type` / `client_state` /
  `discord_channel_id` / `llm_name`）1 つに変更した。あわせて `run_conversation()` の引数
  `ws_clients` を `client_state` に、ツール実行 context のキー `ws_clients` を `client_state` に
  改名した。コアは `client_state` の中身を解釈せず、クライアント拡張が渡した値をそのまま
  フックとツールへ届ける（WebSocket はコアの概念ではないため）
- **BREAKING**: `Extension.client_prompt_providers()` / `conversation_start_hooks()` の戻り値を
  「`client_type` → 関数 1 つ」から「`client_type` → 関数のリスト」に変更し、同じ `client_type`
  への登録を拡張どうしで排他にせず加算式にした。コアは全拡張分をロード順に連結し、プロンプトは
  空でない戻り値を空行区切りで追記、フックは順に await する（1 件の失敗は後続を止めない）。
  コアの参照 API も `get_client_prompt_providers()` / `get_conversation_start_hooks()`（複数形。
  未登録なら空リスト）に改めた。旧契約のまま関数 1 つを返す拡張はロード時に落ちる。
  `result_deliveries()` は従来どおり排他で、`"discord"` は予約のまま
- **BREAKING**: `Extension.setup()` の引数を位置引数 3 つ（`tools, llm_tools, bot`）から
  `SetupContext` 1 つに変更した。`ctx.tools` / `ctx.llm_tools` / `ctx.bot` で従来と同じ値を、
  `ctx.config` でプロセスの設定（`get_config()` と同じインスタンス）を参照できる。
  今後フィールドを足しても既存の拡張の `setup()` を壊さないための変更。旧シグネチャの拡張は
  起動時に `TypeError` で落ちる
- **BREAKING**: `paths.allowed_tool_paths`（ツール実行パスのホワイトリスト）を撤去した。
  ツールの `.py` は起動時に `paths.tool_root`・拡張の `tool_roots()`・`${CONFIG_ROOT}/tools`
  の中だけで解決され、実行時にファイルパスが新たに解決される経路が無いため、`tool_root` の
  設定ミスに対する重複した検査になっていた。また拡張の `tool_roots()` が自動で許可されず、
  pip で入れた拡張のツールが `site-packages` 配下として静かにスキップされていた。YAML に
  残っていても無視される（`PathsConfig` は既定で未知のキーを捨てる）。代わりに
  `loaders/tool_paths.py` の `resolve_tool_dirs()` / `is_within_tool_dirs()` が、解決後の
  ツールファイルが探索ルートか `${CONFIG_ROOT}/tools` の配下にあることを設定なしで検査する
  （`..` を含む `type` や外を指すシンボリックリンクは読み込まない）。信頼境界は `SECURITY.md`
  に明記した（`CONFIG_ROOT` と各ツールディレクトリは拡張モジュールと同じ信頼レベル）
- `loaders/script_loader.py` の `load_script_function` / `load_script_class` に `tool_dirs`
  引数を追加した。省略時は設定から導いたディレクトリで検査する
- **BREAKING**: `lilla.yaml` の `discord:` セクションで、エラー通知・承認依頼の送信先チャンネルを
  名前ではなく ID で指定するようになった。`error_channel`（チャンネル名）は `error_channel_id`
  （チャンネル ID）へ、`approval_channel`（チャンネル名、既定 `lilla-approval`）は
  `approval_channel_id`（チャンネル ID、既定なし）へそれぞれ置き換え、旧キー名は読まなくなった
  （`extra="ignore"` のため YAML に残っていても無視される）。`core/error_notify.py` /
  `handlers/approval_flow.py` はいずれも `bot.get_channel()` による ID 解決のみを行い、
  複数ギルドに同名チャンネルがあっても意図しないギルドへ送信しないようにするための変更

## [0.3.0] - 2026-09-12

### Added

- 拡張が申告した設定差分をコアが `AppConfig` へ合成するようになった。`Extension.config_models()`
  に「YAML セクション名 → セクションモデル」を、`Extension.env_fields()` に「`EnvConfig` の
  フィールド名 → OS 環境変数名」を返すと、`load_extensions()` が全拡張の申告を 1 つの Pydantic
  モデルへ組み、`get_config().<セクション名>` / `get_config().env.<フィールド名>` で型付きで
  読めるようになる。セクションはモデルが必須フィールドを持てば必須、全フィールドにデフォルトが
  あれば省略可能。`env_fields()` で足すフィールドの型は常に `str | None`（既定値 `None`）
- `Extension.required_config_sections()` を追加。自分では提供しないが読む YAML セクション名を
  並べると、誰も提供しておらずコア確定のセクションでもない場合にロード時 fail-fast する
  （メッセージに要求元の拡張名を含む）。拡張どうしの依存を自動解決する仕組みは持たない
- 同名の YAML セクション・同名の `EnvConfig` フィールドを 2 つの拡張が提供した場合、および
  コア確定の名前と重複した場合はロード時 fail-fast するようになった
- `lilla.yaml` の `ui` に `timezone` を追加。「人間側の今日 / いま」に使うタイムゾーンを
  IANA 名で指定する。未指定なら従来どおり OS のローカルタイムゾーンに従う。空文字や
  `ZoneInfo` が受け付けない名前は、ロケールと違ってフォールバックせず起動時に失敗する

### Changed

- **BREAKING**: ホストが `AppConfig` のサブクラスを手書きし、拡張モジュールの import 副作用で
  `set_config()` して差し替える方式を廃止した。`load_extensions()` が拡張の登録後に必ず設定を
  合成して `set_config()` するため、import 時に差し込んだインスタンスは上書きされる。設定の差分は
  `config_models()` / `env_fields()` から出すこと。あわせて `LILLA_EXTENSIONS` の先頭にホスト
  設定モジュールを置く規約が不要になった（並び順は設定の合成に影響しない）
- **BREAKING**: `llm:` を未記載のまま起動、または `providers` が空、`llm.default` に
  対応する provider が無い `lilla.yaml` では起動できなくなった（`AppConfig()` 構築時に
  `ValidationError`）。初メッセージ受信時まで気付けなかった設定ミスを起動時に検出する
- **BREAKING**: `utils/datetime_utils.py` の `to_jst_date` / `jst_day_end_utc` の基準を
  UTC+9 固定から `ui.timezone` の解決結果へ変更（関数名は互換のため維持）。`local_timezone` /
  `local_now` も同じ解決結果を返す。`JST` 定数だけは `ui.timezone` によらず UTC+9 のまま
- **BREAKING**: 定期タスクのスケジューラのタイムゾーンを `Asia/Tokyo` 固定から
  `ui.timezone` の解決結果へ変更。あわせて crontab 式にも同じタイムゾーンを明示的に渡す
  （`CronTrigger` をインスタンスで渡す場合、スケジューラ側の timezone 設定は
  引き継がれず、これまでは OS のローカルタイムゾーンで解釈されていた）
- 会話履歴の対象期間の「今日」（`services/memory_manager.py`）と、`today` などの相対日付の
  基準日（`tool_support/date_range.py`）が、OS のタイムゾーンではなく `ui.timezone` の
  解決結果に従うようになった
- 上記に伴い、日付や実行時刻を日本時間で固定したいホストは `lilla.yaml` に
  `ui.timezone: Asia/Tokyo` を明示すること（コンテナの OS が UTC の場合、未指定だと
  cron も「今日」も UTC になる）

### Fixed

- `LlmProviderConfig.type` を自由文字列から `Literal["ollama", "openai_compat"]` に変更し、
  typo を起動時の `ValidationError` として検出できるようにした
- `logging.yaml` に `handlers.mongodb` が無い場合に `setup_logging()` が `KeyError` で
  落ちていた問題を修正。mongodb ハンドラが無いときは接続情報を注入せず、
  `root.handlers` / 各 `loggers.*.handlers` からも `mongodb` への参照を除去する
- 不正な cron 式を持つタスクが 1 件あるだけで `on_ready` のスケジューラ登録処理全体が
  失敗していた問題を修正。不正なタスクだけ `WARNING` でスキップし、他の正当なタスクは
  登録したうえでスケジューラを起動する

## [0.2.0]

### Fixed

- `run_conversation` の tool_call ループが `finish_reason == "tool_calls"` のみで
  分岐していたため、OpenAI 互換の一部プロバイダが `tool_calls` 付きで
  `finish_reason: "stop"` を返すケースで呼び出しが無視され、空返信になっていたのを修正。
  判定を `tool_calls` の有無に変更
- tool_call の引数 JSON が壊れている場合に `json.loads` が例外を投げ、会話全体が
  失敗していたのを修正。例外を捕捉し、ツールエラーとして LLM に返すように変更

### Changed

- **BREAKING**: 拡張 API を `register_*` + `LILLA_EXTENSIONS_MODULE` から、Adapter 型の
  `Extension` 基底クラス（`core/extension.py`）+ `LILLA_EXTENSIONS` へ置き換え。
  拡張は `Extension` を継承し、モジュールから `extension` インスタンスを 1 つ export する。
  `LILLA_EXTENSIONS` はカンマ区切りで、1 プロセスに 0 個以上の拡張を読み込める
  （未設定・空ならコア単体起動）
- **BREAKING**: `core/extension_points.py` と `register_*` / `get_*` 系のモジュール関数を削除
- メッセージフックはロード順の連鎖になり、`on_message` が `True` を返した時点で以降を止める。
  例外時はその 1 通の処理を打ち切り、ERROR ログとエラー通知チャンネルへ出す
- 同名コマンドの二重登録は、警告つきの後勝ちから fail-fast へ変更
  （同じハンドラの再登録は許容する）
- ツールの探索ルートを複数持てるようにし、`loaders/tool_paths.py` に解決を集約。
  ローダーが import 時に設定を束縛しないよう修正（`llm_tool_loader` / `task_tool_loader` /
  `script_loader`）

### Added

- `Extension.tool_roots()`: `paths.tool_root` に足すツール探索ディレクトリ
  （`allowed_tool_paths` のホワイトリストは自動で広げない）
- `Extension.command_packages()`: `load_all_commands()` が追加で走査するパッケージ
- 拡張どうしの貢献キー衝突（`name` / ツール context キー / `client_type` / コマンド名 /
  複数ルートの同名ツールファイル）をロード時に fail-fast
- `client_type="discord"` のシステムプロンプトをコア内蔵のデフォルトとして保持
  （拡張が出していればそちらを優先）

## [0.1.0]

初回公開版。

### Added

- Discord bot として単体で起動できるコアランタイム（`lilla_core.bot`）
- Ollama（WakeOnLAN 対応）/ OpenAI 互換 API の複数 LLM プロバイダ切り替え
- tool_call ループと、会話履歴・ユーザーメモ・セッションメモリの管理
- `!` コマンドのプラグイン的な追加の仕組み（`commands/` にファイルを置くだけ）
- APScheduler による定期タスク実行（`task_*.yaml`）
- オーナー以外からのコマンド実行に対する承認フロー（Discord 上で承認/拒否）
- 7 種類の拡張ポイント（`core/extension_points.py`）によるコア非改変でのカスタマイズ
- Pydantic ベースの設定管理（`AppConfig`）と `.env` / `lilla.yaml` の分離
- Discord に見せる文言のロケールカタログ（`ja` / `en`）
- pytest による単体テスト一式
