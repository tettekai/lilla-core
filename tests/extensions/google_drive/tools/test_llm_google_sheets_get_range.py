"""`lilla_core.extensions.google_drive` 同梱の `tools/llm_google_sheets_get_range.py` のテスト。"""
from __future__ import annotations

import pytest

from lilla_core.extensions.google_drive.tool_support import NOT_FOUND_MESSAGE


@pytest.fixture
def tool(load_tool):
    """ツール本体をロードする。"""
    return load_tool("llm_google_sheets_get_range")


class TestSchema:
    """SCHEMA の構造。"""

    def test_function_name_and_parameters(self, tool) -> None:
        """関数名は `google_sheets_get_range` で、`spreadsheet_id` と `range` が必須。"""
        function = tool.SCHEMA["function"]
        assert function["name"] == "google_sheets_get_range"
        assert set(function["parameters"]["required"]) == {"spreadsheet_id", "range"}


class TestGetRange:
    """範囲の読み取り。"""

    async def test_reads_whitelisted_spreadsheet(self, tool, fake_drive) -> None:
        """ホワイトリスト配下のスプレッドシートの値を返す。"""
        result = await tool.execute({"spreadsheet_id": "ro-sheet", "range": " シート1!A1:B2 "}, {})

        assert result["success"] is True
        assert result["data"] == {
            "spreadsheet_id": "ro-sheet",
            "name": "家計簿",
            "range": "シート1!A1:B2",
            "values": [["a", "b"], ["c", "d"]],
            "truncated": False,
        }
        fake_drive.get_sheet_values.assert_awaited_once_with("ro-sheet", "シート1!A1:B2")

    @pytest.mark.parametrize("spreadsheet_id", ["out-sheet", "ro-note", "missing"])
    async def test_hidden_spreadsheets_look_missing(
        self, tool, fake_drive, spreadsheet_id: str
    ) -> None:
        """ホワイトリスト外・スプレッドシート以外・不在は同じエラーで、値は取らない。"""
        result = await tool.execute({"spreadsheet_id": spreadsheet_id, "range": "A1"}, {})

        assert result["error"] == NOT_FOUND_MESSAGE
        fake_drive.get_sheet_values.assert_not_awaited()

    @pytest.mark.parametrize(
        "input_",
        [
            {"spreadsheet_id": "a/b", "range": "A1"},
            {"spreadsheet_id": "ro-sheet", "range": ""},
            {"spreadsheet_id": "ro-sheet"},
        ],
    )
    async def test_rejects_invalid_input(self, tool, fake_drive, input_: dict) -> None:
        """ID の形式違い・範囲の指定漏れは API を呼ばずに拒否する。"""
        result = await tool.execute(input_, {})

        assert result["success"] is False
        fake_drive.get_metadata.assert_not_awaited()

    async def test_truncates_rows(self, tool, fake_drive, drive_config) -> None:
        """セルの文字数の合計が `max_read_chars` を超える行から先は切り詰める。"""
        drive_config.max_read_chars = 5
        fake_drive.get_sheet_values.return_value = {
            "range": "A1:B3",
            "values": [["ab", "c"], ["de"], ["f"]],
        }

        result = await tool.execute({"spreadsheet_id": "ro-sheet", "range": "A1:B3"}, {})

        assert result["data"]["values"] == [["ab", "c"], ["de"]]
        assert result["data"]["truncated"] is True

    async def test_empty_range(self, tool, fake_drive) -> None:
        """値の無い範囲は `values` が空のリストになる。"""
        fake_drive.get_sheet_values.return_value = {"range": "A1:B2"}

        result = await tool.execute({"spreadsheet_id": "ro-sheet", "range": "A1:B2"}, {})

        assert result["data"]["values"] == []

    async def test_bad_range_reports_google_message(self, tool, fake_drive, http_error) -> None:
        """範囲の書き間違い（400）は Google のメッセージを添えて返す。"""
        fake_drive.get_sheet_values.side_effect = http_error(
            400, '{"error": {"message": "Unable to parse range: Z"}}'
        )

        result = await tool.execute({"spreadsheet_id": "ro-sheet", "range": "Z"}, {})

        assert result["success"] is False
        assert "Unable to parse range: Z" in result["error"]
