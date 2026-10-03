"""lilla_core.scripts.issue_client_token のテスト。

トークン発行スクリプトの引数解析・上書き確認・保存内容を検証する。
DB へは接続せず、リポジトリをモックに差し替えて確認する。
"""
from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock, MagicMock

import pytest

from lilla_core.repository.client_token_repository import DEFAULT_LABEL


@pytest.fixture
def mock_repo() -> MagicMock:
    """コアの ClientTokenRepository のモック。"""
    repo = MagicMock()
    repo.ensure_indexes = AsyncMock()
    repo.exists_by_label = AsyncMock(return_value=False)
    repo.replace = AsyncMock()
    return repo


@pytest.fixture
def issue_script(mock_repo: MagicMock, monkeypatch: pytest.MonkeyPatch):
    """スクリプトをロードし、リポジトリ取得をモックへ差し替える。

    ``pyproject.toml`` の ``pythonpath = ["src"]`` により ``lilla_core.scripts``
    をそのまま import できる。
    """
    import lilla_core.scripts.issue_client_token as loaded

    monkeypatch.setattr(loaded, "get_client_token_repo", lambda: mock_repo)
    return loaded


class TestParseArgs:
    def test_default_label_matches_repository_default(self, issue_script) -> None:
        """--label 省略時は default になる。"""
        args = issue_script.parse_args([])

        assert args.label == DEFAULT_LABEL == "default"
        assert args.yes is False

    def test_label_can_be_overridden(self, issue_script) -> None:
        """--label で発行対象を指定できる。"""
        assert issue_script.parse_args(["--label", "other-client"]).label == "other-client"

    def test_yes_flag_sets_assume_yes(self, issue_script) -> None:
        """-y / --yes で上書き確認をスキップする指定ができる。"""
        assert issue_script.parse_args(["-y"]).yes is True
        assert issue_script.parse_args(["--yes"]).yes is True


class TestConfirmOverwrite:
    @pytest.mark.parametrize("answer", ["y", "Y", "yes", "YES", " y "])
    def test_returns_true_for_yes(
        self, issue_script, monkeypatch: pytest.MonkeyPatch, answer: str
    ) -> None:
        """y / yes（大文字小文字・前後空白を問わず）なら True を返す。"""
        monkeypatch.setattr("builtins.input", lambda _prompt: answer)

        assert issue_script.confirm_overwrite("default") is True

    @pytest.mark.parametrize("answer", ["n", "N", "no", "", "あ"])
    def test_returns_false_for_anything_else(
        self, issue_script, monkeypatch: pytest.MonkeyPatch, answer: str
    ) -> None:
        """y / yes 以外はすべて False を返す（既定は中止）。"""
        monkeypatch.setattr("builtins.input", lambda _prompt: answer)

        assert issue_script.confirm_overwrite("default") is False

    def test_returns_false_when_stdin_unavailable(
        self, issue_script, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """標準入力が閉じている場合は誤って上書きしないよう False を返す。"""
        def _raise(_prompt):
            raise EOFError

        monkeypatch.setattr("builtins.input", _raise)

        assert issue_script.confirm_overwrite("default") is False


class TestIssueToken:
    async def test_creates_token_when_none_exists(self, issue_script, mock_repo) -> None:
        """既存トークンが無ければ確認なしで発行する。"""
        token = await issue_script.issue_token("default", assume_yes=False)

        assert token
        mock_repo.ensure_indexes.assert_awaited_once()
        mock_repo.replace.assert_awaited_once()

    async def test_stores_sha256_hash_not_plain_token(self, issue_script, mock_repo) -> None:
        """DB には平文ではなく SHA-256 ハッシュを保存する。"""
        token = await issue_script.issue_token("default", assume_yes=False)

        stored_hash, stored_label = mock_repo.replace.await_args[0]
        assert stored_hash == hashlib.sha256(token.encode("utf-8")).hexdigest()
        assert stored_hash != token
        assert stored_label == "default"

    async def test_generated_tokens_are_unique(self, issue_script, mock_repo) -> None:
        """実行ごとに異なるランダムトークンを生成する。"""
        first = await issue_script.issue_token("default", assume_yes=False)
        second = await issue_script.issue_token("default", assume_yes=False)

        assert first != second

    async def test_prompts_when_existing_token(
        self, issue_script, mock_repo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """既存トークンがある場合は上書き確認プロンプトを出す。"""
        mock_repo.exists_by_label.return_value = True
        prompts: list[str] = []
        monkeypatch.setattr("builtins.input", lambda prompt: prompts.append(prompt) or "y")

        token = await issue_script.issue_token("default", assume_yes=False)

        assert token
        assert len(prompts) == 1
        assert "default" in prompts[0]

    async def test_replaces_existing_token_when_confirmed(
        self, issue_script, mock_repo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """上書きを承諾すると同じ label の古いレコードを置き換えて保存する。"""
        mock_repo.exists_by_label.return_value = True
        monkeypatch.setattr("builtins.input", lambda _prompt: "y")

        await issue_script.issue_token("default", assume_yes=False)

        mock_repo.replace.assert_awaited_once()
        assert mock_repo.replace.await_args[0][1] == "default"

    async def test_aborts_when_overwrite_declined(
        self, issue_script, mock_repo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """上書きを拒否すると None を返し、DB を書き換えない。"""
        mock_repo.exists_by_label.return_value = True
        monkeypatch.setattr("builtins.input", lambda _prompt: "n")

        token = await issue_script.issue_token("default", assume_yes=False)

        assert token is None
        mock_repo.replace.assert_not_awaited()

    async def test_assume_yes_skips_prompt(
        self, issue_script, mock_repo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--yes 指定時は既存トークンがあってもプロンプトを出さない。"""
        mock_repo.exists_by_label.return_value = True

        def _fail(_prompt):
            raise AssertionError("プロンプトが表示された")

        monkeypatch.setattr("builtins.input", _fail)

        assert await issue_script.issue_token("default", assume_yes=True)
        mock_repo.replace.assert_awaited_once()


class TestMain:
    async def test_prints_plain_token_once_and_returns_0(
        self, issue_script, mock_repo, capsys: pytest.CaptureFixture
    ) -> None:
        """成功時は平文トークンを表示して 0 を返す。"""
        exit_code = await issue_script.main([])

        assert exit_code == 0
        stored_hash = mock_repo.replace.await_args[0][0]
        out = capsys.readouterr().out
        # 平文トークンはインデントされた単独行として 1 度だけ表示される
        printed = [line.strip() for line in out.splitlines() if line.startswith("  ")]
        assert len(printed) == 1
        assert hashlib.sha256(printed[0].encode("utf-8")).hexdigest() == stored_hash
        assert "一度しか表示されません" in out

    async def test_returns_1_when_aborted(
        self, issue_script, mock_repo, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """上書きを拒否した場合は 1 を返し、中止メッセージを表示する。"""
        mock_repo.exists_by_label.return_value = True
        monkeypatch.setattr("builtins.input", lambda _prompt: "n")

        exit_code = await issue_script.main([])

        assert exit_code == 1
        assert "中止しました" in capsys.readouterr().out

    async def test_uses_label_from_args(self, issue_script, mock_repo) -> None:
        """--label の値が保存とメッセージに反映される。"""
        await issue_script.main(["--label", "other-client"])

        assert mock_repo.replace.await_args[0][1] == "other-client"
