"""コア組み込みのサンプル LLM ツール群。

個人データ・外部サービス依存の無い軽量なツールを置く。`${CONFIG_ROOT}/tools/` に
`type: lilla_core.builtin_tools.<module>` の YAML を置くことで opt-in で有効化できる
（コアは自動では読み込まない）。
"""
