"""日記の清書を notes の `diary/` の下へ書く既定タスク（stem は `task_write_diary`）。

清書の本文は `diary.service.get_faircopy()` で決まった処理で作れるため、LLM は通さず
コードで直接書く。書き先は常に保存先 `notes`（コアの `get_file_storage("notes")` で引いた
`FileStorage`）で、パスは `diary/` にダッシュボードの設定 `DiarySettings.faircopy_path`
（`diary/` からの相対パス）を足したもの。書くかどうかは `DiarySettings.faircopy_storage`
（`["notes"]` ならオン、`[]` ならオフ）で決める（notes の実装は import しない。パスの検証・
書ける場所の検査は notes の実装側でも効く）。

処理:
1. 設定を読む。オフ（`faircopy_storage` が空）なら何もしない（INFO ログだけ）
2. 対象日を決める（日付の区切り時刻より前なら前日）
3. 清書を作る（その日の下書きが無ければ何もしない）
4. notes へ書く（同じ日は上書き）
5. 書けたら `target` へ短く報告する（`<<moodscore>>` が未記入ならその旨も添える）。書けなかったら
   `notify_error`

YAML の項目（すべて省略可能）:

- `schedule` … crontab（`ui.timezone` で解釈）。既定は `30 3 * * *`
- `target` … 報告の送り先。既定は `dm:{DISCORD_MY_USER_ID}`
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: 既定の実行時刻（日付の切り替わり 04:00 より前なので前日分になる）。
DEFAULT_SCHEDULE = "30 3 * * *"

#: 報告の送り先の既定値（`discord.my_user_id` への DM）。
DEFAULT_TARGET = "dm:{DISCORD_MY_USER_ID}"

_MY_USER_ID_PLACEHOLDER = "{DISCORD_MY_USER_ID}"


class DiaryFaircopyTask:
    """日記の清書を notes の `diary/` の下へ書く定期タスク。"""

    def __init__(self, config: dict, name: str) -> None:
        """YAML 設定からタスクを組み立てる。

        Args:
            config: タスクツールの YAML 設定（`_yaml_path` 付き）。
            name: YAML のファイル名 stem（ツール名）。
        """
        self._config = dict(config or {})
        self.name = name
        self.description = self._config.get(
            "description", "Save the diary fair copy under diary/ in notes."
        )
        self.category = self._config.get("category", "diary")
        self.schedule = self._config.get("schedule") or DEFAULT_SCHEDULE
        self._target = self._config.get("target") or DEFAULT_TARGET

    def _resolve_target(self) -> str:
        """報告の送り先を解決する（`{DISCORD_MY_USER_ID}` を `discord.my_user_id` に置換）。"""
        from lilla_core.core.config import get_config

        return str(self._target).replace(
            _MY_USER_ID_PLACEHOLDER, str(get_config().discord.my_user_id)
        )

    async def execute(self, context: dict) -> None:
        """清書を notes へ書き、結果を報告する。例外は外へ漏らさない。

        Args:
            context: タスク実行コンテキスト。`discord_client` / `now` を参照する。
        """
        from lilla_core.core.error_notify import notify_error

        context = context or {}
        try:
            await self._run(context)
        except Exception as e:  # 1 回の失敗でスケジューラ側のジョブを騒がせない
            await notify_error(context.get("discord_client"), f"[{self.name}]", e)

    async def _run(self, context: dict) -> None:
        """設定の読み取りから保存・報告までを行う。

        Args:
            context: タスク実行コンテキスト。
        """
        from lilla_core.core.error_notify import notify_error
        from lilla_core.ui.messages import t

        from lilla_core.extensions.diary.service import (
            DiaryDraftError,
            current_writable_date,
            day_rollover,
            get_faircopy,
            load_diary_settings,
            render_faircopy_path,
        )

        bot = context.get("discord_client")
        try:
            settings = await load_diary_settings()
        except DiaryDraftError as e:
            await notify_error(bot, t("diary.faircopy.settings_error"), e)
            return
        if not settings.faircopy_storage:
            logger.info("[%s] faircopy_storage is off. Skipping", self.name)
            return

        d = current_writable_date(day_rollover(settings), context.get("now"))
        try:
            faircopy = await get_faircopy(d)
        except DiaryDraftError:
            logger.info("[%s] no diary draft for %s. Skipping", self.name, d.isoformat())
            return

        path = render_faircopy_path(settings.faircopy_path, d)
        written = await self._write(bot, path, d.isoformat(), faircopy.content)
        if written is not None:
            await self._report(bot, d.isoformat(), written, faircopy.unresolved)

    async def _write(self, bot, path: str, date_text: str, content: str) -> str | None:
        """notes へ清書を書く。失敗は `notify_error` して `None` を返す。

        Args:
            bot: Discord クライアント。
            path: notes 上のパス（`diary/` から始まる）。
            date_text: 対象日（`YYYY-MM-DD`）。
            content: 清書の本文。

        Returns:
            書いたファイルの論理パス。書けなかったら `None`。
        """
        from lilla_core.core.error_notify import notify_error
        from lilla_core.core.extension import get_file_storage
        from lilla_core.ui.messages import t

        from lilla_core.extensions.diary import FAIRCOPY_STORAGE

        storage = get_file_storage(FAIRCOPY_STORAGE)
        if storage is None:
            await notify_error(
                bot,
                t("diary.faircopy.storage_missing", storage=FAIRCOPY_STORAGE),
                f"file storage '{FAIRCOPY_STORAGE}' is not loaded",
            )
            return None
        try:
            if storage.read_only:
                await notify_error(
                    bot,
                    t("diary.faircopy.storage_read_only", storage=FAIRCOPY_STORAGE),
                    f"file storage '{FAIRCOPY_STORAGE}' is read-only",
                )
                return None
            result = await storage.write(path, content, "overwrite")
        except Exception as e:  # 想定外の例外も通知して外へ漏らさない
            await notify_error(
                bot,
                t("diary.faircopy.write_failed", date=date_text, storage=FAIRCOPY_STORAGE, path=path),
                e,
            )
            return None
        logger.info(
            "[%s] saved the diary fair copy for %s to %s:%s",
            self.name, date_text, FAIRCOPY_STORAGE, result.path,
        )
        return result.path

    async def _report(self, bot, date_text: str, path: str, unresolved: list[str]) -> None:
        """保存できたことを `target` へ短く報告する（送信の失敗はログだけ）。

        Args:
            bot: Discord クライアント。
            date_text: 対象日（`YYYY-MM-DD`）。
            path: 書いたファイルの notes 上の論理パス。
            unresolved: 清書で埋めずに残した穴の名前。
        """
        from lilla_core.repository.conversation_repository import get_conversation_repo
        from lilla_core.services.message_util import send_to_discord
        from lilla_core.ui.messages import t

        from lilla_core.extensions.diary import FAIRCOPY_STORAGE

        message = t("diary.faircopy.saved", date=date_text, storage=FAIRCOPY_STORAGE, path=path)
        if "moodscore" in unresolved:
            message += "\n" + t("diary.faircopy.moodscore_missing")
        if bot is None:
            logger.warning("[%s] no discord client. Report not sent", self.name)
            return
        try:
            await send_to_discord(bot, self._resolve_target(), message, get_conversation_repo())
        except Exception as e:
            logger.warning("[%s] failed to send the report: %s", self.name, e)
