"""tool_response_ex.py（`ToolResponseEx`）のテスト。"""
from lilla_core.tool_support.tool_response_ex import ToolResponseEx


class TestToolResponseExConstruction:
    """生成とアンラップ。"""

    def test_of_creates_instance(self):
        """of() が dict をラップしたインスタンスを返すこと。"""
        response = {"success": True, "data": {}}
        assert isinstance(ToolResponseEx.of(response), ToolResponseEx)

    def test_unwrap_returns_same_object(self):
        """unwrap() が元の dict をそのまま（同一オブジェクトで）返すこと。"""
        response = {"success": True, "data": {"a": 1}}
        assert ToolResponseEx.of(response).unwrap() is response


class TestToolResponseExSuccess:
    """`success` プロパティ。"""

    def test_true_when_success(self):
        """success=True なら True を返すこと。"""
        assert ToolResponseEx.of({"success": True}).success is True

    def test_false_when_failure(self):
        """success=False なら False を返すこと。"""
        assert ToolResponseEx.of({"success": False}).success is False

    def test_false_when_key_missing(self):
        """success キーが無ければ False を返すこと。"""
        assert ToolResponseEx.of({}).success is False

    def test_truthy_value_is_coerced_to_bool(self):
        """真偽値以外でも bool に変換して返すこと。"""
        assert ToolResponseEx.of({"success": 1}).success is True
        assert ToolResponseEx.of({"success": None}).success is False


class TestToolResponseExError:
    """`error` プロパティ。"""

    def test_returns_error_message(self):
        """error のメッセージをそのまま返すこと。"""
        assert ToolResponseEx.of({"error": "HTTP 500"}).error == "HTTP 500"

    def test_returns_none_when_missing(self):
        """error キーが無ければ None を返すこと。"""
        assert ToolResponseEx.of({"success": True}).error is None


class TestToolResponseExData:
    """`data()` によるデータ取得。"""

    def test_returns_whole_data(self):
        """引数なしなら data 全体を返すこと。"""
        data = {"items": [1, 2]}
        assert ToolResponseEx.of({"success": True, "data": data}).data() == data

    def test_returns_empty_dict_when_data_missing(self):
        """data が無ければ空 dict を返すこと。"""
        assert ToolResponseEx.of({"success": True}).data() == {}

    def test_returns_empty_dict_when_data_is_none(self):
        """data が None でも空 dict を返すこと。"""
        assert ToolResponseEx.of({"success": False, "data": None}).data() == {}

    def test_returns_value_for_key(self):
        """キー指定で data[key] を返すこと。"""
        r = ToolResponseEx.of({"success": True, "data": {"count": 3}})
        assert r.data("count") == 3

    def test_returns_default_for_missing_key(self):
        """キーが無ければ default を返すこと。"""
        r = ToolResponseEx.of({"success": True, "data": {"count": 3}})
        assert r.data("missing", "fallback") == "fallback"

    def test_returns_default_when_value_is_none(self):
        """キーはあるが値が None の場合も default を返すこと。"""
        r = ToolResponseEx.of({"success": True, "data": {"count": None}})
        assert r.data("count", 0) == 0

    def test_default_is_none_when_omitted(self):
        """default を省略したキー不在は None を返すこと。"""
        r = ToolResponseEx.of({"success": True, "data": {}})
        assert r.data("missing") is None

    def test_falsy_value_is_returned_as_is(self):
        """None 以外の falsy な値（0 など）はそのまま返すこと。"""
        r = ToolResponseEx.of({"success": True, "data": {"count": 0}})
        assert r.data("count", 99) == 0


class TestToolResponseExMapData:
    """`map_data()` による data の差し替え。"""

    def test_transforms_data_on_success(self):
        """成功時は transform(data) で data を差し替えた dict を返すこと。"""
        r = ToolResponseEx.of({"success": True, "tool_name": "t", "data": {"n": 1}})
        result = r.map_data(lambda d: {"n": d["n"] + 1})
        assert result == {"success": True, "tool_name": "t", "data": {"n": 2}}

    def test_keeps_other_keys_on_success(self):
        """成功時も data 以外のキーは保持されること。"""
        r = ToolResponseEx.of(
            {"success": True, "tool_name": "t", "error": None, "data": {}}
        )
        result = r.map_data(lambda d: {"added": True})
        assert result["tool_name"] == "t"
        assert result["error"] is None

    def test_returns_original_on_failure(self):
        """失敗時は transform を呼ばず元の dict をそのまま返すこと。"""
        response = {"success": False, "error": "HTTP 500", "data": None}
        r = ToolResponseEx.of(response)

        def _fail(_data):
            raise AssertionError("transform must not be called on failure")

        assert r.map_data(_fail) is response

    def test_does_not_mutate_original_on_success(self):
        """成功時に元の dict を書き換えないこと。"""
        response = {"success": True, "data": {"n": 1}}
        ToolResponseEx.of(response).map_data(lambda d: {"n": 99})
        assert response["data"] == {"n": 1}

    def test_transform_receives_empty_dict_when_data_missing(self):
        """data が無い成功レスポンスでは transform に空 dict が渡ること。"""
        received = {}

        def _transform(data):
            received["arg"] = data
            return {"ok": True}

        ToolResponseEx.of({"success": True}).map_data(_transform)
        assert received["arg"] == {}


class TestToolResponseExRealWorldShape:
    """`tool_support/tool_result.py` が返す形をそのまま扱えること。"""

    def test_wraps_tool_success_result(self):
        """tool_success の戻り値を success / data で読めること。"""
        from lilla_core.tool_support.tool_result import tool_success

        r = ToolResponseEx.of(tool_success("get_events", "ok", {"count": 2}))
        assert r.success is True
        assert r.error is None
        assert r.data("count") == 2

    def test_wraps_tool_error_result(self):
        """tool_error の戻り値を success / error で読めること。"""
        from lilla_core.tool_support.tool_result import tool_error

        r = ToolResponseEx.of(tool_error("get_events", "ng", "HTTP 500"))
        assert r.success is False
        assert r.error == "HTTP 500"
        assert r.data() == {}
