# プロンプトの読み込み元（source spec）

`prompt.system` / `prompt.conversation` / `prompt.discord`・LLM プロバイダーの `prompt`・
`task_scheduled_llm` / `llm_expert` の `prompt` などは、本文を YAML に直書きせず、
読み込み元を **source spec** で指定します。どれも `lilla_core.utils.resource_loader` の
`load_text_resources` を通り、同じ規則で読みます。

## 書き方

| 書き方 | 読むもの |
|--------|----------|
| `file:<パス>` | 1 ファイル |
| `dir:<パス>` | ディレクトリの **直下** の `.md` / `.txt` を、ファイル名の昇順で結合（再帰しない。ほかの拡張子は無視） |

- リストで複数並べると、書いた順に結合します（各要素の末尾の改行を除き、改行 1 つでつなぎます）
- `file:` / `dir:` の無い値は失敗します
- `${config_root}` は `CONFIG_ROOT` のディレクトリへ展開します
- パスが `$notes` で始まると、ディスクではなく [ノート置き場（notes）](notes.md) から読みます（後述）

```yaml
prompt:
  system:
    - dir:${config_root}/prompt/system
    - file:$notes/memory/core.md
```

## 読むタイミングと、無いときの扱い

- 起動時に本文をキャッシュしません。会話やタスクのシステムプロンプトを組み立てるたびに
  読み直すため、ファイルやノートの書き換えは再起動なしで次の会話・タスクから効きます
- `file:` のファイルが無ければ、その要素を空にします
- `dir:` のディレクトリが無い（またはディレクトリでない）ときは空にします
- その結果プロンプト全体が空になっても、起動は落としません
- 読み込んだ本文は Jinja2 のテンプレートとして展開します（[プロンプトのテンプレート](prompt-template.md)）

## notes から読む

`file:` / `dir:` の後ろが `$notes` か `$notes/` で始まるときは、保存先 `notes`
（[保存先の共通インタフェース](file-storage.md) の `get_file_storage("notes")`）の論理パスとして読みます。
`$notes` は `${config_root}` と同じくプレースホルダで、ディスク上の実パスではありません。

```yaml
prompt:
  system:
    - file:$notes/memory/core.md       # ノート 1 つ
    - dir:$notes/memory/personal       # 直下の .md / .txt をファイル名昇順
```

- プレフィックスの直後に空白を置いても置かなくても同じです（`file: $notes/...`）
- ディスクの指定と混在でき、書いた順に結合します
- 結合の規則（直下の `.md` / `.txt`・ファイル名昇順・非再帰）と、無いときに空にする扱いは
  ディスクの `file:` / `dir:` と同じです
- `$notes` の後ろのパスは notes のパスの規則（[パスの規則](notes.md#パスの規則)）で検証します。
  `..`・先頭の `/`・空の要素（`a//b`）・`.` などは、欠落として黙って飛ばさず **エラー** にします
  （そのプロンプトを使う会話・タスクが失敗します）
- notes のリポジトリを開けないなど読み取りそのものができないとき、保存先 `notes` が無いときは、
  その要素を空にして WARNING を出します（起動や会話は止めません）
- コアが知るのは「`$notes` が保存先 `notes` である」ことだけです。どの拡張がどのパスを
  使うかには関わりません

ノートの中身はそのままシステムプロンプトに入ります。外から取り込んだ文章が混ざるノートを
指すと、毎回の会話に指示が入り込む経路（プロンプトインジェクション）になりうるため、
指すノートの書き手と書き方に注意してください。

テンプレートの中から読むときは、関数 `notes()` も使えます（[プロンプトのテンプレート](prompt-template.md#ノートを読むnotes)）。
source spec はプロンプトの要素を丸ごとノートにするとき、`notes()` は本文の途中に差し込むときや
条件つきで入れるとき（拡張のプロンプト・スキルなど source spec を書けない場所を含む）に使います。

## コードから使う

`load_text_resources` は非同期関数です。

```python
from lilla_core.utils.resource_loader import load_text_resources

text = await load_text_resources(["file:$notes/memory/core.md"])
```

`AppConfig` のプロンプトも非同期関数で読みます（`await get_config().load_system_prompt()` /
`load_conversation_prompt()` / `load_discord_client_prompt()`）。拡張の
`client_prompt_providers()` のプロバイダは、同期関数のほか非同期関数でもよく、中で
`load_text_resources` を await できます。
