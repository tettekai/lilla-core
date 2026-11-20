"""日記の清書の書き出し設定（`DiarySettings.faircopy_*`）と清書タスク（`task_diary_faircopy`）のテスト。"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from lilla_core.core.extension import Extension
from lilla_core.core.file_storage import (
    FileStorage,
    FileStoragePathError,
    FileWriteResult,
    check_write_mode,
)
from lilla_core.testing import use_extensions, write_minimal_lilla_yaml

from lilla_core.extensions import diary
from lilla_core.extensions.diary.service import render_faircopy_path
from lilla_core.extensions.diary.tools.task_diary_faircopy import DiaryFaircopyTask

_TZ = ZoneInfo("Asia/Tokyo")


class _MemoryStorage(FileStorage):
    """書いた内容をメモリに持つだけの保存先。"""

    def __init__(self, read_only: bool = False, fail: Exception | None = None) -> None:
        """書き込みの可否と、書き込み時に投げる例外を受け取る。"""
        self._read_only = read_only
        self._fail = fail
        self.files: dict[str, str] = {}
        self.writes: list[tuple[str, str, str]] = []

    @property
    def read_only(self) -> bool:
        """読み取り専用なら True。"""
        return self._read_only

    async def read(self, path: str) -> str:
        """書いた内容を返す。"""
        return self.files[path]

    async def write(self, path: str, content: str, mode: str = "overwrite") -> FileWriteResult:
        """内容をメモリに置く（`fail` があれば投げる）。"""
        check_write_mode(mode)
        self.writes.append((path, content, mode))
        if self._fail is not None:
            raise self._fail
        created = path not in self.files
        self.files[path] = content
        return FileWriteResult(path=path, created=created)

    async def list(self, path: str = ""):
        """使わない。"""
        return []

    async def exists(self, path: str) -> bool:
        """書いたことがあれば True。"""
        return path in self.files


class _StorageExtension(Extension):
    """保存先だけを申告する拡張。"""

    name = "memstore"

    def __init__(self, storages: dict[str, FileStorage]) -> None:
        """申告する保存先を受け取る。"""
        self._storages = storages

    def file_storages(self) -> dict:
        """保存先を返す。"""
        return dict(self._storages)


@pytest.fixture
def config_root(tmp_path: Path) -> Path:
    """拡張の節を持たない最小構成の config_root。"""
    write_minimal_lilla_yaml(tmp_path)
    return tmp_path


@pytest.fixture
def storages():
    """notes の代わりの保存先 `notes` と、清書では使わない保存先 `files`。"""
    return {"notes": _MemoryStorage(), "files": _MemoryStorage()}


@pytest.fixture
def registered(config_root, storages):
    """保存先の拡張と `diary` 拡張を登録する。"""
    with use_extensions(_StorageExtension(storages), diary.extension, config_root=config_root):
        yield storages


def _stub_settings_repo(monkeypatch, values: dict) -> MagicMock:
    """保存済みの設定として `values` を返すリポジトリに差し替える。"""
    repo = MagicMock()
    repo.find_one = AsyncMock(return_value={"values": values})
    repo.save = AsyncMock()
    monkeypatch.setattr(
        "lilla_core.repository.extension_settings_repository.get_extension_settings_repo",
        lambda: repo,
    )
    return repo


class TestFaircopyStorageSetting:
    """`faircopy_storage`（notes へ書くかどうかのオンオフ）。"""

    def test_default_is_on(self) -> None:
        """一度も保存していなければオン（notes へ書く）。"""
        assert diary.DiarySettings().faircopy_storage == ["notes"]

    def test_declares_single_static_choice(self) -> None:
        """画面では notes だけのチェックボックスとして出す（ロード済みの保存先は並べない）。"""
        extra = diary.DiarySettings.model_fields["faircopy_storage"].json_schema_extra

        assert extra["widget"] == "checkbox-group"
        assert "choices_from" not in extra
        assert [c["value"] for c in extra["choices"]] == ["notes"]

    def test_widget_declaration_passes_core_check(self) -> None:
        """コアの `widget` 宣言の検証を通り、汎用フォームで扱える項目になる。"""
        from lilla_core.services.extension_settings import describe_fields, validate_widgets

        validate_widgets(diary.DiarySettings)
        field = next(f for f in describe_fields(diary.DiarySettings) if f["name"] == "faircopy_storage")

        assert field["supported"] is True
        assert field["default"] == ["notes"]
        assert [c["value"] for c in field["choices"]] == ["notes"]

    def test_description_says_off_means_not_saved(self) -> None:
        """オフにすると清書が保存されないことを、画面に出る説明で案内する。"""
        description = diary.DiarySettings.model_fields["faircopy_storage"].description

        assert "notes" in description and "diary/" in description
        assert "保存されません" in description
        assert "files" not in description

    def test_empty_list_is_off(self) -> None:
        """空のリスト（オフ）は既定に戻さず、空のまま持つ。"""
        assert diary.DiarySettings(faircopy_storage=[]).faircopy_storage == []

    def test_duplicate_notes_is_on(self) -> None:
        """`notes` が重なっていてもオンの 1 つにまとめる。"""
        assert diary.DiarySettings(faircopy_storage=["notes", "notes"]).faircopy_storage == ["notes"]

    @pytest.mark.parametrize(
        "value",
        [["notes", "files"], ["files"], [" notes "], ["Notes"], ["gone", "a/b", ""]],
    )
    def test_rejects_names_other_than_notes(self, value) -> None:
        """`files` など notes 以外の名前は外さずに拒否する。"""
        with pytest.raises(ValueError, match="only accepts"):
            diary.DiarySettings(faircopy_storage=value)

    @pytest.mark.parametrize(
        ("value", "match"),
        [([3], "strings"), (3, "list"), ("notes", "list"), ("", "list"), (None, "list")],
    )
    def test_rejects_non_list_or_non_string(self, value, match) -> None:
        """リストでない（以前の文字列 1 つ・`None` を含む）・文字列でない要素を含む値は読み替えずに拒否する。"""
        with pytest.raises(ValueError, match=match):
            diary.DiarySettings(faircopy_storage=value)

    @pytest.mark.parametrize(
        ("stored", "expected"),
        [
            ({}, ["notes"]),
            ({"faircopy_storage": []}, []),
            ({"faircopy_storage": ["notes"]}, ["notes"]),
        ],
    )
    async def test_stored_values_load(self, registered, monkeypatch, stored, expected) -> None:
        """保存済みの値（未保存・オフ・オン）をそのまま読む。"""
        from lilla_core.services.extension_settings import load_settings

        _stub_settings_repo(monkeypatch, {"template": "# <<date>>\n", **stored})

        settings = await load_settings("diary", diary.DiarySettings)

        assert settings.faircopy_storage == expected

    @pytest.mark.parametrize("value", ["notes", None, ["files"], ["notes", "files"]])
    async def test_stored_legacy_value_is_reported_as_invalid(
        self, registered, monkeypatch, value
    ) -> None:
        """保存済みの値がリストでない・`files` などを含むなら、読み替えずに「保存値が不正」にする。"""
        from lilla_core.services.extension_settings import (
            InvalidStoredSettingsError,
            load_settings,
        )

        _stub_settings_repo(monkeypatch, {"template": "# <<date>>\n", "faircopy_storage": value})

        with pytest.raises(InvalidStoredSettingsError):
            await load_settings("diary", diary.DiarySettings)

    async def test_saves_empty_list_as_is(self, registered, monkeypatch) -> None:
        """オフで保存すると `[]` を明示して保存する（既定値で上書きしない）。"""
        from lilla_core.services.extension_settings import save_settings

        repo = _stub_settings_repo(monkeypatch, {})

        await save_settings("diary", {"template": "# <<date>>\n", "faircopy_storage": []})

        assert repo.save.await_args.args[1]["faircopy_storage"] == []


class TestFaircopyPathSetting:
    """`faircopy_path`（`diary/` からの相対パス）。"""

    def test_default(self) -> None:
        """既定は `{year}/{month}/{date}.md`。"""
        assert diary.DiarySettings().faircopy_path == "{year}/{month}/{date}.md"

    @pytest.mark.parametrize(
        "value",
        ["{date}.md", " d/{year}/{month}/{date}.md ", "{year}-{month}-{day}-{date}.md", "a.b/{date}.md"],
    )
    def test_accepts_relative_path(self, value: str) -> None:
        """`{date}` を含み、既知のプレースホルダだけの相対パスは受け付ける（前後の空白は除く）。"""
        assert diary.DiarySettings(faircopy_path=value).faircopy_path == value.strip()

    @pytest.mark.parametrize("value", ["diary/{date}.md", "diary/diary/{date}.md"])
    def test_keeps_leading_diary_segment(self, value: str) -> None:
        """先頭の `diary/` も相対パスの一部としてそのまま扱う（読み替えない）。"""
        assert diary.DiarySettings(faircopy_path=value).faircopy_path == value

    @pytest.mark.parametrize(
        ("value", "match"),
        [
            ("", "blank"),
            (5, "blank"),
            ("/{date}.md", "relative"),
            ("diary//{date}.md", "empty segments"),
            ("a//{date}.md", "empty segments"),
            ("{date}/", "empty segments"),
            ("diary/", "empty segments"),
            ("../{date}.md", r"'\.\.'"),
            ("a/../{date}.md", r"'\.\.'"),
            ("diary/../{date}.md", r"'\.\.'"),
            ("./{date}.md", r"'\.\.'"),
            ("a\\{date}.md", "separator"),
            ("today.md", r"\{date\}"),
            ("{when}.md", "unknown placeholder"),
            ("{date.md", "unmatched"),
            ("{{date}}.md", "unmatched"),
        ],
    )
    def test_rejects_path(self, value, match: str) -> None:
        """絶対パス・空の要素・`.` / `..`・バックスラッシュ・`{date}` 無し・不正な波括弧は拒否する。"""
        with pytest.raises(ValueError, match=match):
            diary.DiarySettings(faircopy_path=value)

    async def test_stored_dangerous_path_is_reported_as_invalid(self, registered, monkeypatch) -> None:
        """保存済みの値が危険な形なら、読み取り時も既定へ黙って置き換えず不正として扱う。"""
        from lilla_core.services.extension_settings import (
            InvalidStoredSettingsError,
            load_settings,
        )

        _stub_settings_repo(monkeypatch, {"template": "# <<date>>\n", "faircopy_path": "../{date}.md"})

        with pytest.raises(InvalidStoredSettingsError):
            await load_settings("diary", diary.DiarySettings)


class TestRenderPath:
    """`render_faircopy_path`。"""

    def test_joins_under_diary(self) -> None:
        """`diary/` の下に、プレースホルダを日付で埋めた相対パスを足す。"""
        path = render_faircopy_path("j/{year}/{month}/{day}/{date}.md", date(2026, 3, 7))

        assert path == "diary/j/2026/03/07/2026-03-07.md"

    def test_default(self) -> None:
        """既定のパスは `diary/YYYY/MM/YYYY-MM-DD.md`。"""
        path = render_faircopy_path(diary.DEFAULT_FAIRCOPY_PATH, date(2026, 11, 12))

        assert path == "diary/2026/11/2026-11-12.md"


class TestTask:
    """清書タスクの実行。"""

    @pytest.fixture
    def cfg(self) -> SimpleNamespace:
        """タスクが読む設定（`discord.my_user_id`）。"""
        return SimpleNamespace(discord=SimpleNamespace(my_user_id="42"))

    @pytest.fixture
    def wired(self, registered, cfg, config_root):
        """設定・下書き・通知の依存を差し替える。"""
        state = SimpleNamespace(
            settings=diary.DiarySettings(),
            drafts={"2026-09-27": "# 2026-09-27\nmood: <<moodscore>>\nmemo: <<memo>>\n"},
        )

        async def _load():
            return state.settings

        async def _get(key):
            content = state.drafts.get(key)
            return None if content is None else {"content": content}

        repo = MagicMock()
        repo.get = AsyncMock(side_effect=_get)
        send = AsyncMock()
        notify = AsyncMock()
        conv_repo = MagicMock()
        with patch("lilla_core.extensions.diary.service.load_diary_settings", _load), patch(
            "lilla_core.extensions.diary.repository.get_diary_draft_repo", return_value=repo
        ), patch("lilla_core.extensions.diary.service.local_timezone", return_value=_TZ), patch(
            "lilla_core.services.message_util.send_to_discord", send
        ), patch(
            "lilla_core.repository.conversation_repository.get_conversation_repo",
            return_value=conv_repo,
        ), patch(
            "lilla_core.core.error_notify.notify_error", notify
        ), patch(
            "lilla_core.core.config.get_config", return_value=cfg
        ):
            state.send = send
            state.notify = notify
            state.conv_repo = conv_repo
            state.storages = registered
            state.config_root = config_root
            yield state

    @staticmethod
    def _task(config: dict | None = None) -> DiaryFaircopyTask:
        """既定の YAML（`type` だけ）のタスクを作る。"""
        return DiaryFaircopyTask(config or {"type": "task_diary_faircopy"}, "task_write_diary")

    @staticmethod
    async def _run(task: DiaryFaircopyTask, now: datetime | None = None) -> None:
        """既定の時刻（前日分になる 03:30）でタスクを実行する。"""
        await task.execute(
            {"discord_client": MagicMock(), "now": now or datetime(2026, 9, 28, 3, 30, tzinfo=_TZ)}
        )

    def test_defaults(self) -> None:
        """既定の時刻は 03:30。`schedule` / `target` は YAML で変えられる。"""
        assert self._task().schedule == "30 3 * * *"
        task = self._task({"schedule": "0 5 * * *", "target": "channel:9"})
        assert task.schedule == "0 5 * * *"
        assert task._target == "channel:9"

    async def test_saves_previous_day_before_rollover(self, wired) -> None:
        """区切り（04:00）より前の実行では前日の清書を notes の既定のパスへ上書きで書き、短く報告する。"""
        client = MagicMock()
        now = datetime(2026, 9, 28, 3, 30, tzinfo=_TZ)

        await self._task().execute({"discord_client": client, "now": now})

        assert wired.storages["notes"].writes == [
            (
                "diary/2026/09/2026-09-27.md",
                "# 2026-09-27\nmood: <<moodscore>>\nmemo: -\n",
                "overwrite",
            )
        ]
        assert wired.storages["files"].writes == []
        args = wired.send.await_args.args
        assert args[0] is client
        assert args[1] == "dm:42"
        assert "2026-09-27" in args[2] and "diary/2026/09/2026-09-27.md" in args[2]
        assert "moodscore" in args[2]
        assert args[3] is wired.conv_repo
        wired.notify.assert_not_awaited()

    async def test_leading_diary_segment_is_not_stripped(self, wired) -> None:
        """`diary/{date}.md` は読み替えず、`diary/` の下の `diary/` に書く。"""
        wired.settings = diary.DiarySettings(faircopy_path="diary/{date}.md")

        await self._run(self._task())

        assert list(wired.storages["notes"].files) == ["diary/diary/2026-09-27.md"]

    async def test_no_moodscore_note_when_filled(self, wired) -> None:
        """moodscore が埋まっていれば未記入の一言は付けない。"""
        wired.drafts["2026-09-27"] = "# 2026-09-27\nmood: 4\n"

        await self._run(self._task())

        assert "moodscore" not in wired.send.await_args.args[2]

    async def test_same_day_after_rollover(self, wired) -> None:
        """区切り以降の実行ではその日の清書を書く（パスの形も設定どおり）。"""
        wired.settings = diary.DiarySettings(faircopy_path="j/{year}/{date}.txt")
        wired.drafts["2026-09-28"] = "# today\n"

        await self._run(self._task(), datetime(2026, 9, 28, 21, 0, tzinfo=_TZ))

        assert wired.storages["notes"].files == {"diary/j/2026/2026-09-28.txt": "# today\n"}

    async def test_skips_when_off(self, wired) -> None:
        """オフなら何もしない（通知もしない）。"""
        wired.settings = diary.DiarySettings(faircopy_storage=[])

        await self._run(self._task())

        assert wired.storages["notes"].writes == []
        wired.send.assert_not_awaited()
        wired.notify.assert_not_awaited()

    async def test_skips_without_draft(self, wired) -> None:
        """その日の下書きが無ければ何もしない。"""
        wired.drafts.clear()

        await self._run(self._task())

        assert wired.storages["notes"].writes == []
        wired.send.assert_not_awaited()
        wired.notify.assert_not_awaited()

    async def test_write_failure_notifies_error(self, wired) -> None:
        """書き込みに失敗したら `notify_error` で知らせ、報告は送らない。"""
        wired.storages["notes"]._fail = FileStoragePathError("bad path")

        await self._run(self._task())

        wired.notify.assert_awaited_once()
        assert "diary/2026/09/2026-09-27.md" in wired.notify.await_args.args[1]
        wired.send.assert_not_awaited()

    async def test_unloaded_notes_notifies_error(self, wired) -> None:
        """notes がロードされていなければ、書かずに `notify_error` する。"""
        files = {"files": wired.storages["files"]}
        with use_extensions(
            _StorageExtension(files), diary.extension, config_root=wired.config_root
        ):
            await self._run(self._task())

        wired.notify.assert_awaited_once()
        assert "notes" in wired.notify.await_args.args[1]
        assert wired.storages["files"].writes == []
        wired.send.assert_not_awaited()

    async def test_read_only_notes_notifies_error(self, wired) -> None:
        """notes が読み取り専用なら、書かずに `notify_error` する。"""
        wired.storages["notes"]._read_only = True

        await self._run(self._task())

        wired.notify.assert_awaited_once()
        assert wired.storages["notes"].writes == []
        wired.send.assert_not_awaited()

    async def test_settings_error_notifies(self, wired) -> None:
        """設定を読めなければ `notify_error` する。"""
        from lilla_core.extensions.diary.service import DiaryDraftError

        async def _fail():
            raise DiaryDraftError("db down")

        with patch("lilla_core.extensions.diary.service.load_diary_settings", _fail):
            await self._task().execute({"discord_client": MagicMock()})

        wired.notify.assert_awaited_once()
        wired.send.assert_not_awaited()

    async def test_unexpected_error_does_not_escape(self, wired) -> None:
        """想定外の例外も外へ漏らさず `notify_error` する。"""
        wired.storages["notes"]._fail = RuntimeError("boom")

        await self._run(self._task())

        wired.notify.assert_awaited_once()
        wired.send.assert_not_awaited()
