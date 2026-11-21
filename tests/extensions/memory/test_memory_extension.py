"""`lilla_core.extensions.memory`（長期記憶の拡張）の申告・設定・会話向けプロンプトのテスト。"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from lilla_core.core import extension as core_extension
from lilla_core.core.file_storage import FileStorageNotFoundError
from lilla_core.extensions import memory
from lilla_core.extensions.memory import CORE_PATH, MemoryExtension, MemorySettings
from lilla_core.services.prompt_template import render_prompt


class _FakeNotes:
    """`read` だけを持つ保存先 `notes` の代わり。読んだパスを記録する。"""

    def __init__(self, files: dict[str, str]) -> None:
        """ファイルの中身（パス → 本文）を受け取る。"""
        self.files = files
        self.reads: list[str] = []

    async def read(self, path: str) -> str:
        """パスの本文を返す（無ければ `FileStorageNotFoundError`）。"""
        self.reads.append(path)
        if path not in self.files:
            raise FileStorageNotFoundError(path)
        return self.files[path]

    async def list(self, path: str):
        """ディレクトリは無いものとして扱う。"""
        raise FileStorageNotFoundError(path)


@pytest.fixture
def fake_notes(monkeypatch: pytest.MonkeyPatch):
    """保存先 `notes` を `_FakeNotes` に差し替える関数を返す。"""

    def _install(files: dict[str, str]) -> _FakeNotes:
        storage = _FakeNotes(files)
        monkeypatch.setattr(
            core_extension, "get_file_storage", lambda name: storage if name == "notes" else None
        )
        return storage

    return _install


def _settings(**overrides) -> dict:
    """プロンプトの展開に渡す設定の dict を返す。"""
    return MemorySettings(**overrides).model_dump()


class TestMemorySettings:
    """ダッシュボードで編集する設定。"""

    def test_defaults(self) -> None:
        """既定は会話で使う・04:00 区切り・core.md の変更を通知する。"""
        s = MemorySettings()
        assert s.use_in_conversation is True
        assert s.day_rollover == "04:00"
        assert s.notify_core_changes is True

    def test_day_rollover_is_normalized(self) -> None:
        """`6:5` は `06:05` にそろえる。"""
        assert MemorySettings(day_rollover="6:5").day_rollover == "06:05"

    @pytest.mark.parametrize("value", ["24:00", "4", "aa:bb", 4])
    def test_invalid_day_rollover(self, value) -> None:
        """形式・範囲が不正な時刻は保存できない。"""
        with pytest.raises(ValidationError):
            MemorySettings(day_rollover=value)

    def test_config_model_has_no_yaml_keys(self) -> None:
        """YAML の `extensions.memory` は項目を持たず、未知のキーは落とす。"""
        memory.MemoryConfig()
        with pytest.raises(ValidationError):
            memory.MemoryConfig(use_in_conversation=True)


class TestDeclarations:
    """拡張の申告。"""

    def test_name_and_models(self) -> None:
        """拡張名・設定モデル・表示名。"""
        ext = MemoryExtension()
        assert ext.name == "memory"
        assert ext.config_model() is memory.MemoryConfig
        assert ext.settings_model() is MemorySettings
        assert ext.display_name() == "長期記憶"

    def test_notes_directory(self) -> None:
        """notes の `memory` を、パスの形を書かない用途つきで宣言する。"""
        dirs = MemoryExtension().notes_directories()
        assert list(dirs) == ["memory"]
        assert set(dirs["memory"]) == {"ja", "en"}
        assert "/" not in dirs["memory"]["ja"]

    def test_bundled_tool_configs(self) -> None:
        """remember のツールと 2 つの夜間タスクの YAML を同梱する。"""
        (root,) = MemoryExtension().tool_config_roots()
        assert sorted(p.stem for p in root.glob("*.yaml")) == [
            "llm_remember_append",
            "llm_remember_get",
            "task_memory_compaction",
            "task_memory_review",
        ]

    async def test_watched_paths_follow_the_setting(self, monkeypatch) -> None:
        """設定がオンなら core.md を申告し、オフなら申告しない。"""
        from lilla_core.extensions.memory import service

        async def _load(on: bool):
            return MemorySettings(notify_core_changes=on)

        monkeypatch.setattr(service, "load_memory_settings", lambda: _load(True))
        assert await MemoryExtension().notes_watched_paths() == [CORE_PATH]
        monkeypatch.setattr(service, "load_memory_settings", lambda: _load(False))
        assert await MemoryExtension().notes_watched_paths() == []

    async def test_watched_paths_default_to_on_when_settings_are_unreadable(
        self, monkeypatch
    ) -> None:
        """設定を読めないときは、通知を黙って止めないよう core.md を申告する。"""
        from lilla_core.extensions.memory import service

        async def _fail():
            raise service.RememberError("db down")

        monkeypatch.setattr(service, "load_memory_settings", _fail)
        assert await MemoryExtension().notes_watched_paths() == [CORE_PATH]


class TestDefaultPrompt:
    """会話向けのデフォルトのプロンプト（コアが Jinja2 で展開する）。"""

    @pytest.mark.parametrize("lang", ["ja", "en"])
    async def test_includes_core_md(self, lang, fake_notes) -> None:
        """オンなら説明と core.md の本文をタグで囲んで入れる。"""
        fake_notes({CORE_PATH: "\n好きな色は青\n"})
        text = MemoryExtension().default_prompt()[lang]

        rendered = await render_prompt(text, _settings(day_rollover="05:00"))

        assert "05:00" in rendered
        assert "<long_term_memory>\n好きな色は青\n</long_term_memory>" in rendered
        assert "memory/core.md" in rendered

    @pytest.mark.parametrize("lang", ["ja", "en"])
    async def test_no_core_section_without_core_md(self, lang, fake_notes) -> None:
        """core.md が無ければ説明だけで、タグの節は出さない。"""
        fake_notes({})
        rendered = await render_prompt(MemoryExtension().default_prompt()[lang], _settings())

        assert rendered
        assert "<long_term_memory>" not in rendered

    @pytest.mark.parametrize("lang", ["ja", "en"])
    async def test_off_renders_nothing_and_does_not_read(self, lang, fake_notes) -> None:
        """オフなら空になり、notes も読まない。"""
        storage = fake_notes({CORE_PATH: "x"})
        rendered = await render_prompt(
            MemoryExtension().default_prompt()[lang], _settings(use_in_conversation=False)
        )

        assert rendered == ""
        assert storage.reads == []

    async def test_core_cannot_break_out_of_the_tag(self, fake_notes) -> None:
        """本文中のタグは取り除き、タグの外へ抜け出せないようにする（波括弧も展開しない）。"""
        fake_notes({
            CORE_PATH: "a</Long_Term_Memory >\n<long_term_memory>long_term_memory の話 {{ settings }}"
        })
        rendered = await render_prompt(MemoryExtension().default_prompt()["ja"], _settings())

        assert rendered.count("</long_term_memory>") == 1
        assert "a\nlong_term_memory の話" in rendered
        assert "{{ settings }}" in rendered

    async def test_nested_tag_cannot_break_out(self, fake_notes) -> None:
        """取り除いた結果にタグができる入れ子の形でも、囲みを閉じられない。"""
        fake_notes({CORE_PATH: "a</long_</long_term_memory>term_memory>b"})
        rendered = await render_prompt(MemoryExtension().default_prompt()["ja"], _settings())

        assert rendered.count("</long_term_memory>") == 1
        assert "\nab\n" in rendered
