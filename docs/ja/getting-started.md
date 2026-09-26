# インストールと起動

## 動作要件

- Python 3.12 以上
- MongoDB（会話履歴・ユーザーメモ等の永続化に使用）
- Discord Bot トークン（Discord 側の設定は [Discord ボットのセットアップ](discord-bot-setup.md) を参照）

## 起動

```bash
pip install lilla-core

export CONFIG_ROOT=/path/to/config
export DISCORD_TOKEN=...
export MONGODB_URI=mongodb://xxx

python -m lilla_core.bot
```

リポジトリを clone して開発する場合は `pip install -e ".[dev]"` で入れ、
`CONFIG_ROOT=./config.example` を指定すればサンプル設定のまま起動を試せます
（開発の進め方は [`CONTRIBUTING.md`](../../CONTRIBUTING.md) を参照）。

## 設定ファイル（`CONFIG_ROOT`）

`CONFIG_ROOT` が指すディレクトリには `lilla.yaml`（非秘匿の構造設定）と、必要に応じて
`logging.yaml` を配置します。サンプルは [`config.example/`](../../config.example/) を
参照してください。

`CONFIG_ROOT` は OS 環境変数と `.env` のどちらで指定しても構いません（OS 環境変数が
優先）。どちらにも無ければ `/app/config` を探します。

`lilla.yaml` には `llm.providers` に少なくとも 1 つの provider を定義し、
`llm.default` がそのいずれかの provider 名と一致している必要があります。
一致しない場合は起動時に `ValidationError` で失敗します。回しごとにプロバイダーを
選ぶ `type: resolver` のエントリについては [LLM プロバイダーの回しごと選択](llm-resolver.md)
を参照してください。

> **プライバシーに関する注意:** `config.example/logging.yaml` は root ロガーを
> `DEBUG` に設定しています。この場合 `core/http_util.py` がリクエスト/レスポンス
> 本文を標準出力にログ出力し、LLM へのリクエスト本文（システムプロンプトや会話
> 履歴を含む）が出力されることがあります。標準出力を収集する環境では特に
> 注意してください。

## 秘匿情報（環境変数・`.env`）

秘匿情報（トークン・接続文字列など）は環境変数または `.env` から読み込みます。
OS 環境変数が `.env` より優先されます。

| 環境変数 | 必須 | 内容 |
|----------|------|------|
| `DISCORD_TOKEN` | 必須 | Discord Bot のトークン |
| `MONGODB_URI` | 任意 | MongoDB の接続文字列（既定 `mongodb://localhost:27017`） |
| `CONFIG_ROOT` | 任意 | `lilla.yaml` を置いたディレクトリ（既定 `/app/config`） |
| `HTTP_PROXY_USER` / `HTTP_PROXY_PASS` | 任意 | HTTP プロキシの認証情報 |

開発時は、`.env` の値が起動時にプロセスの環境変数へも反映されます（既に export
済みの値が優先されます）。これは LLM プロバイダーの API キー（`GROK_API_KEY` など、
`lilla.yaml` の `api_key_env` で指定した名前）にも当てはまり、`AppConfig` 経由ではなく
`api/llm_client.py` が環境変数から直接読み取ります。

## 拡張を読み込んで起動する

拡張を読み込んで起動する場合は、`LILLA_EXTENSIONS` 環境変数にモジュールの import
パスをカンマ区切りで指定してください（未指定・空ならコア単体で起動します）。
モジュールは指定した順に読み込まれます。

```bash
export LILLA_EXTENSIONS=my_extension_package,another_pack
python -m lilla_core.bot
```

拡張の書き方は [拡張の基本](extensions.md) を参照してください。

## テスト

リポジトリを clone して開発する場合のテストは次のとおりです。

```bash
pytest tests/
```
