# 拡張のテストの書き方

`lilla_core.testing` は、拡張リポジトリがそれぞれ書くことになる「自分の拡張を登録し、
設定を合成し、テストが終わったらプロセスの状態を元へ戻す」というセットアップを
肩代わりします。本番依存だけで動き、`pytest` を import するのは
`lilla_core.testing.pytest_plugin` だけです。

## pytest プラグイン

このプラグインは `pytest11` entry point による自動登録を **しません**（利用側の既存
conftest と黙って干渉しないためです）。ルートの `conftest.py` で opt-in してください。

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

fixture は 2 つ（どちらも function scope）です。

- `lilla_config_root`: `tmp_path` に最小構成の `lilla.yaml` を書き、`CONFIG_ROOT` を
  そこへ向けます。`DISCORD_TOKEN` が環境に無ければダミー値も入れます。戻り値は
  そのディレクトリです
- `lilla_extensions`: `register(*extensions) -> AppConfig` を返します。拡張を登録し、
  `lilla_config_root` を `CONFIG_ROOT` として設定を合成し、`set_config()` まで行って
  合成結果を返します（以降 `get_config()` が合成モデルを返します）。teardown で
  すべて元へ戻り、`register` を複数回呼んだ場合は後入れ先出しで戻します

## ヘルパーを直接使う

pytest が無い環境や、もっと細かく制御したい場合はヘルパーを直接使えます。

```python
from lilla_core.testing import use_extensions, write_minimal_lilla_yaml


def test_section(tmp_path):
    write_minimal_lilla_yaml(
        tmp_path, extra={"extensions": {"habits": {"channel": "habits-test"}}}
    )

    with use_extensions(extension, config_root=tmp_path) as cfg:
        assert cfg.extensions.habits.channel == "habits-test"
```

`use_extensions()` は入るときに現在の登録・設定インスタンス・`CONFIG_ROOT` を退避し、
抜けるときに（本体で例外が出ても）3 つとも元へ戻します。
`write_minimal_lilla_yaml(directory, *, my_user_id=..., llm_name=..., extra=...)` は
コアが必須にしている項目（`discord.my_user_id` と、`llm.default` に対応する
`llm.providers.<名前>`。あわせて日付が OS のタイムゾーンに左右されないよう
`ui.timezone: Asia/Tokyo`）を書き、`extra` を同じネストで深いマージして重ねます
（拡張が必須にしているセクション用）。戻り値は書き出したパスです。
