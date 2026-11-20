# Common storage interface (FileStorage)

> This page is a translation of the [Japanese original](../ja/file-storage.md). If the two
> differ, the Japanese version is authoritative.

`lilla_core.core.file_storage.FileStorage` is a common interface for storages that let you treat
text files like an OS file system. An extension declares an implementation as "name →
implementation" with `Extension.file_storages()`, and consumers (such as the diary's fair-copy
write-out) look it up by name. Consumers do not import the implementation's module and need not
`requires` the declaring extension.

Two of the extensions bundled with the core declare one (`notes` is a built-in feature that is
always available, while `files` is available only when listed in `LILLA_EXTENSIONS`).

| Name | Extension | Logical path |
|------|-----------|--------------|
| `notes` | [`notes`](notes.md) | A `/`-separated relative path such as `user/memo.md` |
| `files` | [`files`](files.md) | "directory name/relative path" (same as the LLM tools). `read_only` if there is no writable directory |

## For consumers

```python
from lilla_core.core.extension import get_file_storage, list_file_storages
from lilla_core.core.file_storage import FileStorageError

storage = get_file_storage("notes")  # None if absent
if storage is not None:
    try:
        await storage.write("user/memo.md", text, mode="overwrite")
    except FileStorageError as e:
        ...  # str(e) is a message that can be shown to users or the LLM as it is

# For on-screen choices and the like, show only writable storages
choices = [name for name, s in list_file_storages().items() if not s.read_only]
```

The methods are minimal; there is no search, delete or move (some storages make them hard to
provide).

| Method | Meaning |
|--------|---------|
| `read(path) -> str` | Returns the text contents |
| `write(path, content, mode="overwrite") -> FileWriteResult` | `mode` is `create` (fails if it exists) / `overwrite` / `append` (the latter two create it if missing). The result has the normalized `path` and `created` |
| `list(path="") -> list[FileStorageEntry]` | Returns the direct entries sorted by name (not recursive; an empty string is the top level). Each item has `path` / `name` / `type` (`file` / `dir`) / `size` / `modified_time` |
| `exists(path) -> bool` | True if a file or directory exists |
| `read_only` (property) | True for a storage that cannot be written to (default False) |

Failures are told apart by subclasses of `FileStorageError`: `FileStoragePathError` (path
format) / `FileStorageNotFoundError` (missing or inaccessible) / `FileStoragePermissionError`
(cannot write) / `FileStorageExistsError` (already exists with `create`) /
`FileStorageNotTextError` (cannot be read as text, or too large). A wrong `mode` is a
`ValueError`.

## For implementers

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

- A name has the same form as an extension name (`^[a-z0-9][a-z0-9-]*$`), and overlapping names
  between extensions fail at load time. A value that is not a `FileStorage` instance also fails
- `file_storages()` is called when the extension is registered, so do not connect to anything
  external in the constructor. Connect lazily inside each method
- Path validation and access checks are the implementation's job. `mode` can be checked with
  `check_write_mode()`
