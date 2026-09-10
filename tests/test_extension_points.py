"""`lilla_core.core.extension_points` の登録・取得 API のテスト。

モジュールレベルの状態（`_extra_startup_repos` / `_message_hook` /
`_startup_tasks` / `_result_deliveries` / `_client_prompt_providers` /
`_conversation_start_hooks` / `_tool_context_providers`）はテスト間で
漏れるため、
`@pytest.fixture(autouse=True)` で毎回リセットする。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_MODULE_PATH = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "lilla_core"
    / "core"
    / "extension_points.py"
)
_MODULE_NAME = "_lilla_real_extension_points_for_test"


def _load_module():
    """`extension_points.py` をファイルから独立ロードする。"""
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, _MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


_ep = _load_module()


@pytest.fixture(autouse=True)
def _reset_state():
    """モジュールレベルの登録リスト/フックを毎テスト初期化する。"""
    _ep._extra_startup_repos.clear()
    _ep._message_hook = None
    _ep._startup_tasks.clear()
    _ep._result_deliveries.clear()
    _ep._client_prompt_providers.clear()
    _ep._conversation_start_hooks.clear()
    _ep._tool_context_providers.clear()
    yield
    _ep._extra_startup_repos.clear()
    _ep._message_hook = None
    _ep._startup_tasks.clear()
    _ep._result_deliveries.clear()
    _ep._client_prompt_providers.clear()
    _ep._conversation_start_hooks.clear()
    _ep._tool_context_providers.clear()


class TestStartupRepoRegistration:
    """`register_startup_repo` / `get_extra_startup_repos` の挙動。"""

    def test_registered_factory_is_returned(self) -> None:
        def factory():
            return "repo_a"

        _ep.register_startup_repo(factory)
        assert _ep.get_extra_startup_repos() == [factory]

    def test_multiple_registrations_accumulate_in_order(self) -> None:
        def fa():
            return "a"

        def fb():
            return "b"

        _ep.register_startup_repo(fa)
        _ep.register_startup_repo(fb)
        assert _ep.get_extra_startup_repos() == [fa, fb]

    def test_returned_list_is_a_copy(self) -> None:
        """呼び出し側の書き換えで内部状態が壊れないこと。"""

        def factory():
            return "a"

        _ep.register_startup_repo(factory)
        got = _ep.get_extra_startup_repos()
        got.append("garbage")  # type: ignore[arg-type]
        assert _ep.get_extra_startup_repos() == [factory]


class TestMessageHookRegistration:
    """`register_message_hook` / `get_message_hook` の挙動。"""

    async def test_default_hook_returns_false(self) -> None:
        """未登録時はデフォルトフックが返り、常に False を返す。"""
        hook = _ep.get_message_hook()
        # デフォルトフックは呼び出しても副作用なく False
        result = await hook(object())
        assert result is False

    async def test_registered_hook_is_returned(self) -> None:
        mock_hook = AsyncMock(return_value=True)
        _ep.register_message_hook(mock_hook)
        got = _ep.get_message_hook()
        assert got is mock_hook

    async def test_last_registration_wins(self) -> None:
        """複数回登録した場合、最後の登録が有効になる。"""
        first = AsyncMock(return_value=False)
        second = AsyncMock(return_value=True)
        _ep.register_message_hook(first)
        _ep.register_message_hook(second)
        assert _ep.get_message_hook() is second


class TestStartupTaskRegistration:
    """`register_startup_task` / `get_startup_tasks` の挙動。"""

    def test_registered_task_is_returned(self) -> None:
        async def task(tools, llm_tools, bot) -> None:
            return None

        _ep.register_startup_task(task)
        assert _ep.get_startup_tasks() == [task]

    def test_multiple_tasks_accumulate_in_order(self) -> None:
        async def ta(tools, llm_tools, bot) -> None:
            return None

        async def tb(tools, llm_tools, bot) -> None:
            return None

        _ep.register_startup_task(ta)
        _ep.register_startup_task(tb)
        assert _ep.get_startup_tasks() == [ta, tb]

    def test_returned_list_is_a_copy(self) -> None:
        async def task(tools, llm_tools, bot) -> None:
            return None

        _ep.register_startup_task(task)
        got = _ep.get_startup_tasks()
        got.append("garbage")  # type: ignore[arg-type]
        assert _ep.get_startup_tasks() == [task]


class TestResultDeliveryRegistration:
    """`register_result_delivery` / `get_result_delivery` の挙動。"""

    def test_unregistered_client_type_returns_none(self) -> None:
        """未登録の client_type は None を返す（呼び出し側がフォールバックする）。"""
        assert _ep.get_result_delivery("lilla-client") is None

    def test_registered_fn_is_returned(self) -> None:
        fn = AsyncMock()
        _ep.register_result_delivery("lilla-client", fn)
        assert _ep.get_result_delivery("lilla-client") is fn

    def test_last_registration_wins(self) -> None:
        """同じ client_type を二度登録した場合は後勝ち（合成しない）。"""
        first = AsyncMock()
        second = AsyncMock()
        _ep.register_result_delivery("lilla-client", first)
        _ep.register_result_delivery("lilla-client", second)
        assert _ep.get_result_delivery("lilla-client") is second

    def test_client_types_are_independent(self) -> None:
        """client_type ごとに独立して登録・取得できる。"""
        a = AsyncMock()
        b = AsyncMock()
        _ep.register_result_delivery("lilla-client", a)
        _ep.register_result_delivery("other-client", b)
        assert _ep.get_result_delivery("lilla-client") is a
        assert _ep.get_result_delivery("other-client") is b


class TestClientPromptProviderRegistration:
    """`register_client_prompt_provider` / `get_client_prompt_provider` の挙動。"""

    def test_unregistered_client_type_returns_none(self) -> None:
        """未登録の client_type は None を返す（呼び出し側は何も付与しない）。"""
        assert _ep.get_client_prompt_provider("lilla-client") is None

    def test_registered_provider_is_returned(self) -> None:
        provider = MagicMock(return_value="プロンプト")
        _ep.register_client_prompt_provider("discord", provider)
        assert _ep.get_client_prompt_provider("discord") is provider

    def test_last_registration_wins(self) -> None:
        """同じ client_type を二度登録した場合は後勝ち（合成しない）。"""
        first = MagicMock(return_value="1")
        second = MagicMock(return_value="2")
        _ep.register_client_prompt_provider("discord", first)
        _ep.register_client_prompt_provider("discord", second)
        assert _ep.get_client_prompt_provider("discord") is second

    def test_client_types_are_independent(self) -> None:
        """client_type ごとに独立して登録・取得できる。"""
        a = MagicMock(return_value="a")
        b = MagicMock(return_value="b")
        _ep.register_client_prompt_provider("discord", a)
        _ep.register_client_prompt_provider("lilla-client", b)
        assert _ep.get_client_prompt_provider("discord") is a
        assert _ep.get_client_prompt_provider("lilla-client") is b

    def test_provider_is_reevaluated_on_each_call(self) -> None:
        """プロバイダは呼び出しのたびに評価される（レジストリは値をキャッシュしない）。

        プロンプトは「呼ぶたびにファイルから読み直す」設計なので、取得側が
        戻り値を握り続けないことを、戻り値を変えるモックで固定する。
        （実ファイルの書き換えまでは行わない）
        """
        provider = MagicMock(side_effect=["1回目", "2回目"])
        _ep.register_client_prompt_provider("discord", provider)

        assert _ep.get_client_prompt_provider("discord")() == "1回目"
        assert _ep.get_client_prompt_provider("discord")() == "2回目"
        assert provider.call_count == 2


class TestConversationStartHookRegistration:
    """`register_conversation_start_hook` / `get_conversation_start_hook` の挙動。"""

    def test_unregistered_client_type_returns_none(self) -> None:
        """未登録の client_type は None を返す（呼び出し側は何もしない）。"""
        assert _ep.get_conversation_start_hook("lilla-client") is None

    def test_registered_hook_is_returned(self) -> None:
        hook = AsyncMock()
        _ep.register_conversation_start_hook("lilla-client", hook)
        assert _ep.get_conversation_start_hook("lilla-client") is hook

    def test_last_registration_wins(self) -> None:
        """同じ client_type を二度登録した場合は後勝ち（合成しない）。"""
        first = AsyncMock()
        second = AsyncMock()
        _ep.register_conversation_start_hook("lilla-client", first)
        _ep.register_conversation_start_hook("lilla-client", second)
        assert _ep.get_conversation_start_hook("lilla-client") is second

    def test_client_types_are_independent(self) -> None:
        """client_type ごとに独立して登録・取得できる。"""
        a = AsyncMock()
        b = AsyncMock()
        _ep.register_conversation_start_hook("lilla-client", a)
        _ep.register_conversation_start_hook("other-client", b)
        assert _ep.get_conversation_start_hook("lilla-client") is a
        assert _ep.get_conversation_start_hook("other-client") is b
        assert _ep.get_conversation_start_hook("discord") is None


class TestToolContextProviderRegistration:
    """`register_tool_context_provider` / `get_tool_context_providers` の挙動。

    他の拡張ポイントが「1 件を引く」形なのに対し、これは
    `build_tool_context()` が列挙して使うため全件を dict で返す。
    """

    def test_returns_empty_dict_when_nothing_registered(self) -> None:
        """未登録なら空 dict を返す（コア単体起動でも安全に列挙できる）。"""
        assert _ep.get_tool_context_providers() == {}

    def test_registered_provider_is_returned(self) -> None:
        provider = MagicMock(return_value="client")
        _ep.register_tool_context_provider("obsidian_client", provider)
        assert _ep.get_tool_context_providers() == {"obsidian_client": provider}

    def test_multiple_registrations_are_all_listed(self) -> None:
        """複数登録した場合はすべて列挙される。"""
        a = MagicMock()
        b = MagicMock()
        c = MagicMock()
        _ep.register_tool_context_provider("obsidian_client", a)
        _ep.register_tool_context_provider("bitbucket_client", b)
        _ep.register_tool_context_provider("current_media_repo", c)
        assert _ep.get_tool_context_providers() == {
            "obsidian_client": a,
            "bitbucket_client": b,
            "current_media_repo": c,
        }

    def test_last_registration_wins(self) -> None:
        """同じ name を二度登録した場合は後勝ち（合成しない）。"""
        first = MagicMock()
        second = MagicMock()
        _ep.register_tool_context_provider("obsidian_client", first)
        _ep.register_tool_context_provider("obsidian_client", second)
        assert _ep.get_tool_context_providers() == {"obsidian_client": second}

    def test_returned_dict_is_a_copy(self) -> None:
        """呼び出し側の書き換えで内部状態が壊れないこと。"""
        provider = MagicMock()
        _ep.register_tool_context_provider("obsidian_client", provider)

        got = _ep.get_tool_context_providers()
        got["obsidian_client"] = "garbage"  # type: ignore[assignment]
        got["extra"] = "garbage"  # type: ignore[assignment]

        assert _ep.get_tool_context_providers() == {"obsidian_client": provider}
