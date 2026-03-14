import time

import pytest

from auth import (
    SESSIONS,
    _bootstrap_admin,
    _check_login_rate,
    _cleanup_expired_sessions,
    _create_session,
    _get_session,
    _grant_bot_to_user,
    _hash_password,
    _invalidate_user_sessions,
    _load_users,
    _record_failed_login,
    _save_users,
    _user_can_access_bot,
    _verify_password,
)


class TestPasswordHashing:
    def test_hash_and_verify(self, auth_env):
        h = _hash_password("secret123")
        assert h != "secret123"
        assert _verify_password("secret123", h) is True

    def test_wrong_password_fails(self, auth_env):
        h = _hash_password("correct")
        assert _verify_password("wrong", h) is False


class TestSessionLifecycle:
    def _make_user(self, auth_env, username="alice", role="user", bots=None):
        users = _load_users()
        users[username] = {
            "password_hash": _hash_password("pass"),
            "role": role,
            "bots": bots or [],
        }
        _save_users(users)

    def test_create_and_get_session(self, auth_env):
        self._make_user(auth_env, "alice")
        token = _create_session("alice")
        session = _get_session(token)
        assert session is not None
        assert session["username"] == "alice"

    def test_invalid_token_returns_none(self, auth_env):
        assert _get_session("bogus-token") is None

    def test_expired_session_returns_none(self, auth_env, monkeypatch):
        monkeypatch.setattr("config.SESSION_TTL", 1)
        self._make_user(auth_env, "alice")
        token = _create_session("alice")
        time.sleep(1.1)
        assert _get_session(token) is None

    def test_deleted_user_invalidates_session(self, auth_env):
        self._make_user(auth_env, "alice")
        token = _create_session("alice")
        # Delete user from file
        users = _load_users()
        del users["alice"]
        _save_users(users)
        assert _get_session(token) is None

    def test_cleanup_expired_sessions(self, auth_env, monkeypatch):
        monkeypatch.setattr("config.SESSION_TTL", 1)
        self._make_user(auth_env, "alice")
        _create_session("alice")
        _create_session("alice")
        time.sleep(1.1)
        removed = _cleanup_expired_sessions()
        assert removed == 2
        assert len(SESSIONS) == 0


class TestRBAC:
    def test_admin_can_access_any_bot(self, auth_env):
        session = {"username": "admin", "role": "admin", "bots": []}
        assert _user_can_access_bot(session, "any-bot") is True

    def test_wildcard_grants_all_access(self, auth_env):
        session = {"username": "alice", "role": "user", "bots": ["*"]}
        assert _user_can_access_bot(session, "any-bot") is True

    def test_specific_bot_access(self, auth_env):
        session = {"username": "alice", "role": "user", "bots": ["bot-a", "bot-b"]}
        assert _user_can_access_bot(session, "bot-a") is True
        assert _user_can_access_bot(session, "bot-c") is False

    def test_empty_bots_denies_access(self, auth_env):
        session = {"username": "alice", "role": "user", "bots": []}
        assert _user_can_access_bot(session, "any-bot") is False

    def test_login_rate_limiting(self, auth_env):
        """Test that 5 failed logins from the same IP triggers 429."""
        ip = "192.168.1.100"
        for _ in range(5):
            _record_failed_login(ip)
        with pytest.raises(Exception) as exc_info:
            _check_login_rate(ip)
        assert exc_info.value.status_code == 429


class TestUserBootstrap:
    def test_bootstrap_creates_admin(self, auth_env, monkeypatch):
        monkeypatch.setenv("ADMIN_PASSWORD", "test-pass-123")
        _bootstrap_admin()
        users = _load_users()
        assert "admin" in users
        assert users["admin"]["role"] == "admin"
        assert _verify_password("test-pass-123", users["admin"]["password_hash"])

    def test_bootstrap_skips_if_users_exist(self, auth_env, monkeypatch):
        monkeypatch.setenv("ADMIN_PASSWORD", "first")
        _bootstrap_admin()
        monkeypatch.setenv("ADMIN_PASSWORD", "second")
        _bootstrap_admin()
        users = _load_users()
        # Password should still be "first" — second bootstrap was skipped
        assert _verify_password("first", users["admin"]["password_hash"])

    def test_bootstrap_generates_random_password(self, auth_env, monkeypatch, capsys):
        monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
        _bootstrap_admin()
        users = _load_users()
        assert "admin" in users
        output = capsys.readouterr().out
        assert "Password:" in output
        assert "Save this" in output


class TestSessionInvalidation:
    def test_invalidate_user_sessions(self, auth_env):
        users = {"alice": {"password_hash": _hash_password("p"), "role": "user", "bots": []}}
        _save_users(users)
        t1 = _create_session("alice")
        t2 = _create_session("alice")
        removed = _invalidate_user_sessions("alice")
        assert removed == 2
        assert _get_session(t1) is None
        assert _get_session(t2) is None

    def test_invalidate_only_target_user(self, auth_env):
        users = {
            "alice": {"password_hash": _hash_password("p"), "role": "user", "bots": []},
            "bob": {"password_hash": _hash_password("p"), "role": "user", "bots": []},
        }
        _save_users(users)
        _create_session("alice")
        bob_token = _create_session("bob")
        _invalidate_user_sessions("alice")
        assert _get_session(bob_token) is not None


class TestGrantBotToUser:
    @pytest.fixture(autouse=True)
    def setup(self, auth_env):
        self.auth_env = auth_env

    def test_grant_adds_bot(self):
        users = {"alice": {"password_hash": _hash_password("p"), "role": "user", "bots": ["bot-a"]}}
        _save_users(users)
        _grant_bot_to_user("alice", "bot-b")
        users = _load_users()
        assert "bot-b" in users["alice"]["bots"]

    def test_grant_noop_for_admin(self):
        _bootstrap_admin()
        _grant_bot_to_user("admin", "some-bot")
        users = _load_users()
        assert "some-bot" not in users["admin"]["bots"]

    def test_grant_noop_for_wildcard(self):
        users = {"alice": {"password_hash": _hash_password("p"), "role": "user", "bots": ["*"]}}
        _save_users(users)
        _grant_bot_to_user("alice", "bot-c")
        users = _load_users()
        assert users["alice"]["bots"] == ["*"]

    def test_grant_noop_if_already_present(self):
        users = {"alice": {"password_hash": _hash_password("p"), "role": "user", "bots": ["bot-a"]}}
        _save_users(users)
        _grant_bot_to_user("alice", "bot-a")
        users = _load_users()
        assert users["alice"]["bots"].count("bot-a") == 1

    def test_grant_noop_for_missing_user(self):
        users = {}
        _save_users(users)
        _grant_bot_to_user("nobody", "bot-a")
        users = _load_users()
        assert "nobody" not in users
