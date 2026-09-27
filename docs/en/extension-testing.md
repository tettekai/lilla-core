# Testing an extension

> This page is a translation of the [Japanese original](../ja/extension-testing.md). If
> the two differ, the Japanese version is authoritative.

`lilla_core.testing` carries the setup an extension repository would otherwise
rewrite for itself: register the extension, compose the config, and put the process
back the way it was afterwards. It only uses the production dependencies — `pytest` is
imported by `lilla_core.testing.pytest_plugin` alone.

## The pytest plugin

The plugin is **not** registered through a `pytest11` entry point, so it cannot
interfere silently with an existing conftest. Opt in from the root `conftest.py`:

```python
# conftest.py
pytest_plugins = ["lilla_core.testing.pytest_plugin"]
```

```python
# test_my_extension.py
from my_package import extension


def test_config_section_is_composed(lilla_extensions):
    cfg = lilla_extensions(extension)

    assert cfg.extensions.my_section.value == "default"
```

Two fixtures come with it, both function-scoped:

- `lilla_config_root` writes a minimal `lilla.yaml` into `tmp_path`, points
  `CONFIG_ROOT` at it, and sets a dummy `DISCORD_TOKEN` when one is not already in the
  environment. It returns that directory.
- `lilla_extensions` returns `register(*extensions) -> AppConfig`, which registers the
  extensions, composes the config against `lilla_config_root`, calls `set_config()` (so
  `get_config()` returns the composed model), and returns it. Everything is unwound at
  teardown, last in first out if `register` was called more than once.

## Using the helpers directly

Without pytest — or when a test needs finer control — use the helpers directly:

```python
from lilla_core.testing import use_extensions, write_minimal_lilla_yaml


def test_section(tmp_path):
    write_minimal_lilla_yaml(
        tmp_path, extra={"extensions": {"habits": {"channel": "habits-test"}}}
    )

    with use_extensions(extension, config_root=tmp_path) as cfg:
        assert cfg.extensions.habits.channel == "habits-test"
```

`use_extensions()` saves the current registration, the current config instance and
`CONFIG_ROOT` on the way in, and restores all three on the way out even if the body
raises. `write_minimal_lilla_yaml(directory, *, my_user_id=..., llm_name=..., extra=...)`
writes the sections the core requires (`discord.my_user_id`, `llm.default` and the
matching `llm.providers.<name>`, plus `ui.timezone: Asia/Tokyo` so dates do not depend
on the OS timezone) and deep-merges `extra` on top for the sections an extension makes
mandatory. It returns the path it wrote.
