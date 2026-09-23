import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# `import lilla_core` が src/ から解決されるように追加
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
# tools.shared など tools/ 配下のパッケージを import できるようにプロジェクトルートを追加
sys.path.insert(0, str(Path(__file__).parent.parent))

# `AppConfig.env` の必須フィールド（DISCORD_TOKEN）が未設定ならダミー値を入れる。
os.environ.setdefault("DISCORD_TOKEN", "dummy-token-for-testing")

# YAML 由来の必須セクション（discord.my_user_id）を解決できるよう、最小構成の
# lilla.yaml を置いた fixtures ディレクトリを CONFIG_ROOT に指定する。
# 開発者のシェルに CONFIG_ROOT が設定されていると実ファイルの設定を読んでしまうため、
# setdefault ではなく常にフィクスチャ側で上書きする（元の値はテスト終了後に復元する）。
_FIXTURE_CONFIG_ROOT = str(Path(__file__).parent / "fixtures" / "config_root")
_ORIGINAL_CONFIG_ROOT = os.environ.get("CONFIG_ROOT")
os.environ["CONFIG_ROOT"] = _FIXTURE_CONFIG_ROOT

# aiohttp を先に import しておく。
# `discord` 等と異なり本物が必要なパッケージのため（詳細はモック方針ドキュメント参照）。
import aiohttp  # noqa: F401, E402


@pytest.fixture(scope="session", autouse=True)
def _restore_shell_config_root():
    """テストセッション終了後、シェル由来の CONFIG_ROOT を元の状態へ復元する。"""
    yield
    if _ORIGINAL_CONFIG_ROOT is None:
        os.environ.pop("CONFIG_ROOT", None)
    else:
        os.environ["CONFIG_ROOT"] = _ORIGINAL_CONFIG_ROOT


_INTERNAL_PACKAGES = frozenset({
    "api", "commands", "handlers", "repository", "services", "loaders", "lilla_core"
})
# テストファイル間でモックが汚染しやすいサードパーティパッケージも対象に含める
_THIRD_PARTY_MOCK_PACKAGES = frozenset({"discord", "motor", "pymongo", "bson"})


class _CleaningModule(pytest.Module):
    """モジュールのインポート直前にファーストパーティ MagicMock エントリを sys.modules から除去するカスタム Module。

    pytest_collect_file フックと異なり、_getobj() は各モジュールのインポート時に呼ばれるため、
    テストファイルのインポート順に合わせてクリーンアップが確実に実行される。
    """

    def _getobj(self):
        _cleanup_packages = _INTERNAL_PACKAGES | _THIRD_PARTY_MOCK_PACKAGES
        to_remove = [
            key for key, val in list(sys.modules.items())
            if key.split(".")[0] in _cleanup_packages and isinstance(val, MagicMock)
        ]
        for key in to_remove:
            del sys.modules[key]
        return super()._getobj()


def pytest_pycollect_makemodule(module_path, parent):
    """各テストモジュールのコレクタとして _CleaningModule を返す。"""
    return _CleaningModule.from_parent(parent, path=module_path)


@pytest.fixture
def make_extension():
    """テスト用の `Extension` インスタンスを組み立てるファクトリを返す。

    キーワード引数はメソッド名で、値が callable ならそのままメソッドとして
    差し込み、そうでなければ「その値を返すメソッド」として差し込む。クラス
    （`config_model=SampleSectionConfig` など）は callable でも値として扱う::

        ext = make_extension("pack", client_prompt_providers={"discord": [provider]})
        ext = make_extension("pack", on_message=AsyncMock(return_value=True))
    """
    from lilla_core.core.extension import Extension

    def _const(value):
        return lambda *args, **kwargs: value

    def _make(name: str = "test-extension", **contributions):
        ext = Extension()
        ext.name = name
        for method_name, value in contributions.items():
            is_method = callable(value) and not isinstance(value, type)
            setattr(ext, method_name, value if is_method else _const(value))
        return ext

    return _make


@pytest.fixture
def use_extensions():
    """拡張の登録をテスト内だけに閉じ込めるヘルパーを返す。

    登録内容はプロセス全体で共有されるモジュール状態のため、テスト終了時に
    元の内容へ戻す。
    """
    from lilla_core.core import extension

    saved = extension.get_extensions()

    def _use(*extensions) -> None:
        extension.set_extensions(list(extensions))

    yield _use
    extension.set_extensions(saved)
