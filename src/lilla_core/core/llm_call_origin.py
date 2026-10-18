"""LLM 呼び出しの「入口」を `contextvars` で受け渡す。

使用量の記録（`services/llm_usage.py`）は「どこからの呼び出しか」を知りたいが、
`chat_to_llm` などの公開関数の引数は増やしたくない。そこで会話・定期タスク・
ツール実行の各入口がこのモジュールの `llm_call_origin()` で文脈を積み、
LLM クライアントは送信時に `get_llm_call_origin()` で読む。

`contextvars` は `asyncio.create_task()` で子タスクへコピーされるため、入口で
積んだ値はその中で起きた LLM 呼び出し（ツール経由の入れ子を含む）にそのまま届く。
"""
from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

#: 入口がどれにも当てはまらないときのラベル
UNKNOWN_ENTRY = "unknown"


@dataclass(frozen=True)
class LlmCallOrigin:
    """LLM 呼び出しの入口を表す文脈。

    各フィールドは入口が分かったものだけ埋まる（入れ子になれば外側の値を引き継ぐ）。
    メッセージ本文やツールの引数は持たない。
    """

    #: 会話のクライアント種別（`run_conversation` の `client_type`。`"discord"` / `"task"` など）
    client_type: str | None = None
    #: 会話が行われている Discord チャンネルの ID
    discord_channel_id: int | None = None
    #: 実行中の定期タスク（`trigger="task"` のツール）の YAML stem
    task_name: str | None = None
    #: 実行中の LLM ツールの YAML stem（入れ子なら最も内側）
    tool_name: str | None = None
    #: 上のどれにも当てはまらない入口の名前（`"selftest"` / `"toolresult"` など）
    source: str | None = None

    def entry(self) -> str:
        """集計に使う入口のラベルを返す。

        優先順は「ツール → タスク → source → 会話のクライアント種別」で、最も内側に
        近いものを使う（会話中に `llm_expert` が呼んだ LLM は `tool:llm_expert` に数える）。
        どれも無ければ `UNKNOWN_ENTRY`。
        """
        if self.tool_name:
            return f"tool:{self.tool_name}"
        if self.task_name:
            return f"task:{self.task_name}"
        if self.source:
            return self.source
        if self.client_type:
            return f"conversation:{self.client_type}"
        return UNKNOWN_ENTRY


_current_origin: ContextVar[LlmCallOrigin] = ContextVar(
    "lilla_llm_call_origin", default=LlmCallOrigin()
)


def get_llm_call_origin() -> LlmCallOrigin:
    """現在の文脈に積まれている LLM 呼び出しの入口を返す（未設定なら空の入口）。"""
    return _current_origin.get()


@contextmanager
def llm_call_origin(**fields) -> Iterator[LlmCallOrigin]:
    """`with` の中だけ、入口のフィールドを上書きした文脈にする。

    渡したフィールドのうち値が `None` のものは無視し、外側の値を残す
    （呼び出し元が「分からない」項目で外側の情報を消さないため）。

    Args:
        **fields: `LlmCallOrigin` のフィールド名と値。

    Yields:
        上書き後の入口。

    Raises:
        TypeError: `LlmCallOrigin` に無いフィールド名を渡した場合。
    """
    updates = {key: value for key, value in fields.items() if value is not None}
    origin = dataclasses.replace(_current_origin.get(), **updates)
    token = _current_origin.set(origin)
    try:
        yield origin
    finally:
        _current_origin.reset(token)
