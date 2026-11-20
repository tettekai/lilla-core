# 保存先の共通インタフェース（FileStorage）

`lilla_core.core.file_storage.FileStorage` は、テキストファイルを OS のファイルシステムの
ように扱える保存先の共通インタフェースです。拡張は実装を `Extension.file_storages()` で
「名前 → 実装」として申告し、利用側（日記の清書の書き出しなど）は名前で引いて使います。
利用側は実装のモジュールを import せず、申告した拡張を `requires` しなくて構いません。

コアに同梱する拡張では次の 2 つが申告しています（`notes` は標準機能で常に使え、`files` は `LILLA_EXTENSIONS` に並べたときだけ使えます）。

| 名前 | 拡張 | 論理パス |
|------|------|----------|
| `notes` | [`notes`](notes.md) | `user/memo.md` のような `/` 区切りの相対パス |
| `files` | [`files`](files.md) | 「ディレクトリの名前/相対パス」（LLM ツールと同じ）。書き込み可能なディレクトリが無ければ `read_only` |

## 使う側

```python
from lilla_core.core.extension import get_file_storage, list_file_storages
from lilla_core.core.file_storage import FileStorageError

storage = get_file_storage("notes")  # 無ければ None
if storage is not None:
    try:
        await storage.write("user/memo.md", text, mode="overwrite")
    except FileStorageError as e:
        ...  # str(e) は利用者や LLM へそのまま見せてよい文言

# 画面の選択肢などには、書き込める保存先だけを出す
choices = [name for name, s in list_file_storages().items() if not s.read_only]
```

メソッドは最小限で、検索・削除・移動は持ちません（保存先によっては作りにくいため）。

| メソッド | 内容 |
|----------|------|
| `read(path) -> str` | テキストの内容を返す |
| `write(path, content, mode="overwrite") -> FileWriteResult` | `mode` は `create`（既にあれば失敗）/ `overwrite` / `append`（後 2 つは無ければ作る）。結果は正規化後の `path` と `created` |
| `list(path="") -> list[FileStorageEntry]` | 直下のエントリを名前順に返す（再帰しない。空文字は最上位）。各要素は `path` / `name` / `type`（`file` / `dir`）/ `size` / `modified_time` |
| `exists(path) -> bool` | ファイルかディレクトリがあれば True |
| `read_only`（プロパティ） | 書き込めない保存先なら True（既定 False） |

失敗は `FileStorageError` の派生で分かります: `FileStoragePathError`（パスの形式）/
`FileStorageNotFoundError`（不在・アクセス不可）/ `FileStoragePermissionError`（書き込めない）/
`FileStorageExistsError`（`create` で既存）/ `FileStorageNotTextError`（テキストとして読めない・
大きすぎる）。`mode` の誤りは `ValueError` です。

## 実装する側

```python
from lilla_core.core.file_storage import FileStorage


class MyStorage(FileStorage):
    async def read(self, path): ...
    async def write(self, path, content, mode="overwrite"): ...
    async def list(self, path=""): ...
    async def exists(self, path): ...


class MyExtension(Extension):
    name = "my-storage"

    def file_storages(self):
        return {"my-storage": MyStorage()}
```

- 名前は拡張名と同じ形（`^[a-z0-9][a-z0-9-]*$`）で、拡張どうしで重なればロード時に落ちます。
  値が `FileStorage` のインスタンスでない場合も落ちます
- `file_storages()` は拡張の登録時に呼ばれるため、コンストラクタで外部へ接続しないでください。
  接続は各メソッドの中で遅延して行います
- パスの検証とアクセスの判定は実装側が受け持ちます。`mode` は `check_write_mode()` で確かめられます
