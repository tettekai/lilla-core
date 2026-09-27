# 設定の合成

拡張は自分が足す設定を申告し、コアが起動時にすべての申告から 1 つの Pydantic
モデルを組みます。`AppConfig` のサブクラスを手書きして、モジュールの import 副作用
として `set_config()` で差し替える方式はもう契約に含みません。合成したインスタンスが
拡張の差し込みを上書きするため、`LILLA_EXTENSIONS` の並び順は設定に影響しません。

```python
class GoogleConfig(BaseModel):
    client_id: str | None = None
    redirect_uri: str = "http://localhost/google-callback"


class GoogleOAuthExtension(Extension):
    name = "google-oauth"

    def config_model(self):
        return GoogleConfig

    def env_fields(self):
        return {"google_client_secret": "GOOGLE_CLIENT_SECRET"}
```

## 節名と置き場所

1 つの拡張が足せるセクションのモデルは **1 つだけ** で、節名は自分では書きません。
キーは `name` のハイフンをアンダースコアに置き換えたものです（`google-oauth` →
`google_oauth`。ハイフンの無い名前はそのまま）。複数の設定のまとまりを持ちたいときは、
その 1 つのモデルの子として並べてください。拡張のセクションは `lilla.yaml` の
トップレベルではなく、コア確定の `extensions:` の下に置きます。コアが後からトップレベルに
節を足しても、拡張の名前と衝突しません。

```yaml
dashboard:
  port: 8765
extensions:
  google_oauth:
    client_id: ...
```

## 読み出し

これで `get_config().extensions.google_oauth.client_id` と
`get_config().env.google_client_secret` がプロセス全体から読めるようになります
（トップレベルの `get_config().google_oauth` は作りません）。
`get_config()` の型は基底の `AppConfig` なので、
型検査や補完のためにセクションをそのモデルの型で受け取りたいときは `get_section()` を
使ってください。

```python
from lilla_core.core.config import get_section

client_id = get_section("google-oauth", GoogleConfig).client_id
```

引数には拡張の `name` を渡します（節名をそのまま渡しても構いません）。探すのは
`extensions:` の下だけです（コア確定の節は `get_config().ui` のように直接
読みます）。セクションが申告されていない場合や、値が渡したモデルのインスタンスでない
場合は `ValueError` になります。名前の綴りを間違えても静かに空を返すことはありません。

## 合成の規則

- セクションは、モデルが必須フィールドを 1 つでも持てば **必須**、そうでなければ
  省略可能になります。必須セクションが `lilla.yaml` に無ければ起動時に落ちます
- `extensions:` の下に未知のキーがあれば起動時に落ちます。外した拡張の節が払い残しの
  まま気付かれずに残ることを防ぐためです。拡張が 0 個なら `extensions` は空です
- 申告した節を `extensions:` ではなくトップレベルに書いた場合も起動時に落ちます
  （そのままだと無視され、モデルの既定値のまま気付かずに動いてしまうため）。
  コア確定の節と同じ名前のトップレベルキーはコアのものなので対象外です
- 合成される env フィールドの型は常に `str | None`（既定値 `None`）です。契約が型を
  運ばないため、必須フィールドや文字列以外の秘匿情報はこの経路では表現できません
- `config_model()` は pydantic の `BaseModel` のサブクラスか `None` を返します。それ以外は
  ロード時に失敗します。拡張名は一意でアンダースコアを含まないため、2 つの拡張が同じ
  キーを導くことはありません。数字で始まる名前の拡張は、キーが識別子にならないため
  モデルを申告できません。キーは名前空間が別なので、コア確定の節と同じでも構いません
- 同じ env フィールド名を 2 つの拡張が提供したら fail-fast します。コア確定の env
  フィールド名は予約済みです
- 他の拡張のセクションを読むときは、その拡張に `requires` で依存し
  （[拡張どうしの依存](extensions.md#拡張どうしの依存)）、
  `cfg.extensions.<相手のキー>` を読みます。そのための別の申告はありません。
  `required_env_fields()` と `required_tool_context_keys()` は引き続き、自分では提供しないが
  読む `cfg.env` のフィールドとツール context のキーを並べます。誰も提供していなければ
  ロードに失敗し、要求した拡張の名前を示します（コア確定の env フィールドとツール
  context のキーは、書いても常に満たされます）
