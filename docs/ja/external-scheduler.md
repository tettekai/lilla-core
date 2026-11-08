# 外部スケジューラとの連携

定期タスクの起動は、既定ではコア内の APScheduler が行います。実行環境の時計が当てに
ならない場合など、時刻の管理を外部のスケジューラ（OS のタスクスケジューラや
Hangfire など）へ任せたいときは、次の 2 つを組み合わせます。

- `lilla.yaml` の `scheduler.enabled: false` で、内部の定期起動だけを止める
- 外部のスケジューラが `GET /api/tasks` でタスクの定義を取得し、時刻になったら
  `POST /api/runtask` を呼ぶ

タスクの時刻と有効・無効は lilla 側（ツール YAML と、ダッシュボードで編集する
[拡張のタスクの上書き](extension-task-override.md)）にだけ置き、外部側では二重に管理しません。

## 内部スケジューラ全体のスイッチ（`scheduler.enabled`）

```yaml
scheduler:
  enabled: false   # 既定は true（節ごと省略してよい）
```

- `false` にすると APScheduler を起動しません。タスクのロード、共有 HTTP サーバー、
  `GET /api/tasks`、`POST /api/runtask`、`!runtask` は止まりません
- 起動時にだけ読みます。変更は再起動後に効きます
- 真偽値だけを受け付けます（`"false"` のような文字列や `0` / `1` は起動時の設定エラー）
- タスクごとの `enabled`（上書き）とは別物です。全体を `false` にしても、各タスクの
  `enabled` はそのまま `GET /api/tasks` に出ます

全体が `false` のとき、ダッシュボードでタスクの上書きを保存すると、反映の結果は
`scheduler_disabled` になります。保存自体は成功しており、次の `GET /api/tasks` から
新しい値が返ります。

## `GET /api/tasks`

[共有 HTTP サーバー](http-server.md) のルートで、Bearer トークンが必要です。

```console
$ curl -sS -H "Authorization: Bearer $TOKEN" http://localhost:8080/api/tasks
```

```json
{
  "scheduler_enabled": false,
  "cron_format": "apscheduler3",
  "tasks": [
    {"name": "task_example", "schedule": "0 22 * * *", "timezone": "Asia/Tokyo", "enabled": true},
    {"name": "task_other", "schedule": "30 3 * * *", "timezone": "Asia/Tokyo", "enabled": false}
  ]
}
```

| 項目 | 内容 |
|------|------|
| `scheduler_enabled` | 設定（`scheduler.enabled`）の値。Discord 接続前でまだ起動していない、といった実行時の状態ではありません。外部側が内部との同時稼働を避ける判断に使えます |
| `cron_format` | `schedule` の書式。今は常に `apscheduler3`（下の「cron の意味」）。将来書式が変わったときに外部側が気付けるように付けています |
| `tasks[].name` | ツール名。そのまま `POST /api/runtask` の `tool` に渡します |
| `tasks[].schedule` | 5 項目の crontab。デフォルトに拡張の上書きを重ねた実効値です |
| `tasks[].timezone` | `schedule` を解釈するタイムゾーン（下の「タイムゾーン」） |
| `tasks[].enabled` | 定期起動するか。上書きで止めたタスクも `false` で一覧に残るため、外部側は登録を外せます |

対象と値の決め方:

- 対象はロード済みの **定期タスク**（`schedule` を持つ task ツール）すべてです。拡張が
  同梱したものだけでなく、コアの組み込みや利用者が `${CONFIG_ROOT}/tools` に置いたものも
  含みます。`schedule` を持たないタスクは含みません
- ツール YAML で `enabled: false` にしたタスクはロードされないため、一覧に出ません。
  YAML を消したタスクも同様です
- 上書きは拡張が同梱したタスクにだけ効きます（[拡張のタスクの上書き](extension-task-override.md)）。
  上書きは呼ぶたびに MongoDB から読むため、内部スケジューラが無効・未起動でも最新の値が返ります
- 実効値の決め方は内部スケジューラ・ダッシュボードと同じ共通処理です。型の不正な上書き・
  crontab として解釈できない `schedule` の上書きは無視してデフォルトを使います
- 個別の `enabled: false` は定期起動を止めるだけで、`POST /api/runtask` での手動実行は
  止めません（全体の `scheduler.enabled: false` も同じ）
- 返すのは上の項目だけで、プロンプト・プロバイダー・送信先などのツール設定は返しません

エラー（どちらも空の一覧では返しません）:

| ステータス | 本文 | 意味 |
|------------|------|------|
| `401` | `{"error": "unauthorized"}` | トークンが無い・違う |
| `503` | `{"error": "overrides_unavailable"}` | 上書きを MongoDB から読めなかった。デフォルト値で埋めて成功扱いにはしません |
| `500` | `{"error": "invalid_schedule", "task": "<ツール名>"}` | そのタスクのデフォルトの crontab が解釈できず、実効スケジュールが決まらない（ツール YAML の誤り） |

外部側は、エラーのときは前回取得した一覧のまま動き続け、ジョブを消さないでください。
`200` で `tasks` が空なのは「定期タスクが 1 つも無い」という正常な結果です。

## cron の意味

`schedule` は APScheduler 3 の `CronTrigger.from_crontab()` がそのまま解釈する 5 項目の
文字列で、普通の cron と同じ意味とは限りません。外部側で使う形への変換は外部側の責任です。

- 並びは `分 時 日 月 曜日`
- **曜日の数字は 0 が月曜**（`0`=月 … `6`=日）。普通の cron（0 が日曜）と 1 日ずれます。
  `mon` / `sun` のような英語名なら普通の cron と同じ曜日です
- 日と曜日を両方指定すると **AND**（両方を満たす日）です。普通の cron の OR とは違います
  （`0 9 1 * mon` は「1 日かつ月曜」）
- `*/5` などの範囲・間隔の書き方は APScheduler の仕様に従います

## タイムゾーン

`timezone` は [タイムゾーン](timezone.md) と同じ規則で決まります。

- `ui.timezone` を書いていれば、その IANA 名（`Asia/Tokyo` など）
- 未指定なら OS のローカルタイムゾーンですが、IANA 名を持たないため、いまの UTC オフセットを
  `+09:00` の形で返します（`JST` のような略称は返しません）。内部のスケジューラも同じ固定
  オフセットで解釈しています。夏時間のある地域では季節で値が変わるため、外部と連携するなら
  `ui.timezone` を書いておくのがおすすめです

## 外部側の運用の前提

- 起動時と、その後は定期的に（例: 1 時間ごと）`GET /api/tasks` で一覧を丸ごと取り直し、
  一覧に無くなったタスクと `enabled: false` のタスクを登録から外します
- 内部（`scheduler.enabled: true`）と外部を同時に動かすと、同じタスクが二重に起動します
- `POST /api/runtask` の `202` は「受け付けた」で、完了ではありません。応答が届かなかった
  ときに再送すると、二重に実行されることがあります。外部側の自動リトライは慎重に
  （再送しない設定を推奨）
- 停止していた間に過ぎた回は遡って実行しない前提です（次の予定から再開）。コアに
  実行履歴や再送のキューはありません
