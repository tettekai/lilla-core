"""oauth2_authorization_code_utils の OAuth2 認可コードフロー認証情報ユーティリティのテスト。"""

import time

from lilla_core.utils.oauth2_authorization_code_utils import (
    build_token_credentials,
    has_refresh_token,
    is_access_token_valid,
)


class TestIsAccessTokenValid:
    """is_access_token_valid のテスト。"""

    def test_valid_when_token_and_future_expiry(self):
        """有効なトークンと未来の有効期限なら True。"""
        creds = {
            "access_token": "token",
            "access_token_expires_at": time.time() + 3600,
        }
        assert is_access_token_valid(creds) is True

    def test_invalid_when_none(self):
        """creds が None なら False。"""
        assert is_access_token_valid(None) is False

    def test_invalid_when_expired(self):
        """有効期限が過去なら False。"""
        creds = {
            "access_token": "token",
            "access_token_expires_at": time.time() - 1,
        }
        assert is_access_token_valid(creds) is False

    def test_invalid_when_no_access_token(self):
        """access_token が無ければ False。"""
        creds = {"access_token_expires_at": time.time() + 3600}
        assert is_access_token_valid(creds) is False

    def test_invalid_when_no_expiry(self):
        """有効期限フィールドが無ければ False。"""
        creds = {"access_token": "token"}
        assert is_access_token_valid(creds) is False

    def test_invalid_when_empty_dict(self):
        """空の dict なら False。"""
        assert is_access_token_valid({}) is False


class TestHasRefreshToken:
    """has_refresh_token のテスト。"""

    def test_true_when_present(self):
        """refresh_token があれば True。"""
        assert has_refresh_token({"refresh_token": "r"}) is True

    def test_false_when_none(self):
        """creds が None なら False。"""
        assert has_refresh_token(None) is False

    def test_false_when_missing(self):
        """refresh_token が無ければ False。"""
        assert has_refresh_token({"access_token": "token"}) is False

    def test_false_when_empty_value(self):
        """refresh_token が空文字なら False。"""
        assert has_refresh_token({"refresh_token": ""}) is False


class TestBuildTokenCredentials:
    """build_token_credentials のテスト。"""

    def test_builds_all_fields(self):
        """access_token / refresh_token / 有効期限を組み立てる。"""
        before = int(time.time())
        creds = build_token_credentials(
            {"access_token": "a", "refresh_token": "r", "expires_in": 3600}
        )
        after = int(time.time())

        assert creds["access_token"] == "a"
        assert creds["refresh_token"] == "r"
        assert before + 3600 <= creds["access_token_expires_at"] <= after + 3600

    def test_uses_fallback_when_refresh_token_missing(self):
        """レスポンスに refresh_token が無ければ fallback を使う。"""
        creds = build_token_credentials(
            {"access_token": "a", "expires_in": 0},
            fallback_refresh_token="old",
        )
        assert creds["refresh_token"] == "old"

    def test_uses_fallback_when_refresh_token_empty(self):
        """refresh_token が空文字でも fallback を使う。"""
        creds = build_token_credentials(
            {"access_token": "a", "refresh_token": "", "expires_in": 0},
            fallback_refresh_token="old",
        )
        assert creds["refresh_token"] == "old"

    def test_refresh_token_none_without_fallback(self):
        """refresh_token も fallback も無ければ None。"""
        creds = build_token_credentials({"access_token": "a"})
        assert creds["refresh_token"] is None

    def test_expires_in_defaults_to_zero(self):
        """expires_in が無ければ現在時刻を有効期限とする。"""
        before = int(time.time())
        creds = build_token_credentials({"access_token": "a"})
        after = int(time.time())
        assert before <= creds["access_token_expires_at"] <= after
