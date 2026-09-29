"""コア組み込みのサンプル LLM ツール群。

個人データ・外部サービス依存の無い軽量なツールを置く。このディレクトリは
`loaders/tool_paths.py` が常に最後のツール探索ルートとして足すため、
`${CONFIG_ROOT}/tools/` に `type: <module>`（ファイル名）の YAML を置くことで opt-in で
有効化できる（コアは自動では読み込まない）。従来の `type: lilla_core.builtin_tools.<module>`
（import パス）も引き続き使える。
"""
