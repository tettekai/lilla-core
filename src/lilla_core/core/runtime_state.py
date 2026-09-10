"""実行時に一時的に上書きされる状態を保持するモジュール。

現状は以下を扱う：
- `!model` で切り替える LLM プロバイダー名
- `!disable_tools` / `!enable_tools` で切り替えるツール無効化フラグ

いずれもプロセス内メモリだけで保持し、MongoDB 等への永続化は行わない
（Bot を再起動すると上書きは消え、デフォルト状態に戻る）。

Bot 全体でグローバルに 1 状態のみを持つ（チャンネル / DM ごとの状態は持たない）。
"""
from __future__ import annotations

_active_llm_name: str | None = None


def get_active_llm_name() -> str | None:
    """現在の上書き LLM プロバイダー名を返す。上書きされていなければ None を返す。

    Returns:
        上書き中のプロバイダー名。未設定（デフォルトのまま）なら None。
    """
    return _active_llm_name


def set_active_llm_name(name: str) -> None:
    """上書きする LLM プロバイダー名を設定する。

    名前の妥当性検証（`llm.providers` に存在するか）は呼び出し側の責務とする。

    Args:
        name: `lilla.yaml` の `llm.providers` に定義されたプロバイダー名。
    """
    global _active_llm_name
    _active_llm_name = name


def reset_active_llm_name() -> None:
    """LLM プロバイダー名の上書きを解除し、`llm.default` の設定に戻す。"""
    global _active_llm_name
    _active_llm_name = None


_tools_disabled: bool = False


def is_tools_disabled() -> bool:
    """通常会話でツールを無効化しているかどうかを返す。"""
    return _tools_disabled


def set_tools_disabled(disabled: bool) -> None:
    """通常会話のツール無効化フラグを設定する。"""
    global _tools_disabled
    _tools_disabled = disabled
