# Local file read / write (official extension pack)

> This page is a translation of the [Japanese original](../ja/files.md). If the two
> differ, the Japanese version is authoritative.

The `files` extension in the official extension pack lets the LLM list, read, write, and search
text files on the local file system. Only directories whitelisted in `lilla.yaml` (and
everything under them) are accessible. Settings and error handling follow the
[Google Drive extension](google.md).

| import path | `name` | Role |
|-------------|--------|------|
| `lilla_core.extensions.files` | `files` | LLM tools to list, read, write, and search text files (UTF-8) under the whitelisted directories. Depends on no other extension |

## Enabling

```bash
export LILLA_EXTENSIONS=files
# Same as the full path: export LILLA_EXTENSIONS=lilla_core.extensions.files
```

There is no ordering constraint when listing it together with other extensions.

## Settings

```yaml
extensions:
  files:
    # Directories that may only be read.
    read_only_dirs:
      - /srv/lilla/docs
      # UNC paths and mount points work as-is, as long as the OS can open them as normal paths.
      # - '\\nas\share\manuals'
    # Directories that may be read and written.
    writable_dirs:
      - /srv/lilla/notes
      # Use a mapping to change the name the LLM uses (default: the last path component).
      - path: /home/me/Documents/lilla
        name: mydocs
    # Maximum number of characters file_read returns at once (default 100000).
    max_read_chars: 100000
```

| Setting | Description |
|---------|-------------|
| `extensions.files.read_only_dirs` | List of directories that may only be read (empty by default) |
| `extensions.files.writable_dirs` | List of directories that may be read and written (empty by default) |
| `extensions.files.max_read_chars` | Maximum number of characters `llm_file_read` returns at once (default `100000`) |

- Each item is either a path string or a mapping with `path` and `name`. Paths must be absolute
  (relative paths, blank strings, and URL forms such as `smb://` fail at startup). Whether the
  path currently exists is not checked at startup (a directory mounted later is fine)
- If both lists are empty, the tools deny every access (the bot still starts)
- Listing the same directory in both lists fails at startup
- Whitelisted directories may be nested. The allowed operation is decided by the deepest
  directory containing the target (put a `read_only_dirs` directory inside a `writable_dirs`
  directory and everything under it becomes read-only)
- If the section remains while the extension is not loaded, the core treats it as a leftover and
  fails at startup

### Paths exchanged with the LLM

A tool's `path` has the form **"directory name/relative path inside it"** (e.g. `notes/2026/todo.md`).
The name is `name` if given, otherwise the last component of the path (`notes` for
`/srv/lilla/notes`). Duplicate names fail at startup, so tell them apart with `name`. A path
without a last component (such as `/`) also needs `name`.

- Both `/` and `\` are accepted as separators; returned paths always use `/`
- Passing an empty string to `llm_file_list` returns the list of accessible directories (names)
- A `path` returned by a tool can be passed to the other tools as-is

## Tools

The tool implementations live in the `files` extension, and the YAML files go in the user's
`${CONFIG_ROOT}/tools/` (the extension does not bundle YAML). Only place the ones you use (to keep
it read-only, do not place `llm_file_write.yaml`; the write tool is then not visible to the LLM).

```yaml
# ${CONFIG_ROOT}/tools/llm_file_list.yaml
type: llm_file_list
```

```yaml
# ${CONFIG_ROOT}/tools/llm_file_read.yaml
type: llm_file_read
```

```yaml
# ${CONFIG_ROOT}/tools/llm_file_write.yaml
type: llm_file_write
```

```yaml
# ${CONFIG_ROOT}/tools/llm_file_search.yaml
type: llm_file_search
```

| Tool | Description |
|------|-------------|
| `llm_file_list` | Returns the direct entries of `path` sorted by name (not recursive). Each item has `path` / `name` / `type` (`file` / `dir`) / `size` (files only) / `modified_time` (UTC) / `writable`. `pattern` (a glob such as `*.md`) filters by name. More than 500 entries are truncated with `truncated: true` |
| `llm_file_read` | Returns UTF-8 text (a leading BOM is removed). `offset` (characters, default 0) and `limit` (default and maximum: `max_read_chars`) read from the middle. When more remains, returns `truncated: true` and `next_offset`. Files with a NUL byte in the first 8 KB or that are not valid UTF-8 (Shift_JIS, etc.) are not returned; an error saying they cannot be read as text is returned instead. Files over 50 MB are not read |
| `llm_file_write` | Writes `content` as UTF-8 (no BOM) only under `writable_dirs` (newlines are not converted). `mode` is `create` (fails if it exists) / `overwrite` (replaces the whole file; creates it if missing) / `append` (appends; creates it if missing). With `append`, nothing is appended if the start of the existing file cannot be read as UTF-8 text (Shift_JIS, binary, etc.), so encodings do not get mixed. Missing parent directories are created. `created` in the result tells whether the file was newly created |
| `llm_file_search` | Searches text files under `path` (a directory or a file; empty string for everything) for lines containing `query` and returns `path` / `line` (line number) / `text` (case-insensitive). Binary files, non-UTF-8 files, files over 5 MB, and files the OS fails to read are silently skipped (with an empty string, missing directories are skipped too). More than 50 matching lines or 2,000 scanned files are truncated with `truncated: true` |

## Safety

- Paths containing `..`, absolute paths, drive names, or NUL are not accepted
- Checks use the real path (after following symbolic links), and any access outside the
  directory selected by the leading name is denied (including escaping through a symbolic link
  under it, and escaping into another whitelisted directory). `llm_file_list` hides entries that
  point outside, and `llm_file_search` does not read files that point outside nor follow links
  to directories
- Writes to read-only directories (including nested read-only directories) are denied
- Unexpected exceptions (such as running out of disk space) are not returned to the LLM as the
  exception text; a generic message is returned instead and the error goes to the ERROR log and
  the Discord error channel (`discord.error_channel_id`). Expected errors such as "does not exist"
  or "denied by the OS" return only a short message
- There are no tools for deleting, moving, or renaming

### Caveats

- The content of files read is passed to the LLM as-is. If you whitelist a directory others can
  write to, text inside it may steer the LLM's behavior (prompt injection). Especially when
  combined with writable directories, only whitelist directories whose content you trust
- Races where a link is swapped between the check and the read / write (TOCTOU) are not
  prevented. Only let trusted people write to the whitelisted directories
- Writes simply overwrite; there is no conflict detection or versioning. Use a directory with
  backups or version control if you need them
- Files are read and written with the OS permissions of the bot process. Apart from the
  whitelist, restrict the process's permissions to what is needed
