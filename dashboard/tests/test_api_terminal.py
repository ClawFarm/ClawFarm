from unittest.mock import MagicMock

import pytest

import config
from auth import (
    _bootstrap_admin,
    _create_session,
    _hash_password,
    _load_users,
    _save_users,
)


class TestWebSocketTerminal:
    """Tests for the /api/bots/{name}/terminal WebSocket endpoint."""

    @pytest.fixture(autouse=True)
    def setup(self, auth_env, monkeypatch):
        import docker as _docker
        from fastapi.testclient import TestClient

        import app as _app

        self.docker = _docker
        self.mock_client = MagicMock()
        monkeypatch.setattr("docker_utils._get_client", lambda: self.mock_client)
        monkeypatch.setenv("ADMIN_PASSWORD", "admin-pass")
        _bootstrap_admin()
        self.app = _app
        self.client = TestClient(_app.app)
        self.auth_env = auth_env

    def _login(self, username="admin", password="admin-pass"):
        """Log in and return the session cookie value."""
        r = self.client.post("/api/auth/login", json={
            "username": username, "password": password,
        })
        return r.cookies.get("cfm_session")

    def _add_user(self, username, password, role="user", bots=None):
        """Create a non-admin user."""
        users = _load_users()
        users[username] = {
            "username": username,
            "password_hash": _hash_password(password),
            "role": role,
            "bots": bots or [],
        }
        _save_users(users)

    def test_no_cookie_closes_4401(self):
        """No cfm_session cookie -> WebSocket closed with code 4401."""
        from starlette.websockets import WebSocketDisconnect
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with self.client.websocket_connect("/api/bots/mybot/terminal"):
                pass  # pragma: no cover
        assert exc_info.value.code == 4401

    def test_invalid_session_closes_4401(self):
        """Invalid/expired session token -> WebSocket closed with code 4401."""
        from starlette.websockets import WebSocketDisconnect
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with self.client.websocket_connect(
                "/api/bots/mybot/terminal",
                cookies={"cfm_session": "bogus-token-xyz"},
            ):
                pass  # pragma: no cover
        assert exc_info.value.code == 4401

    def test_rbac_denied_closes_4403(self):
        """User without access to the bot -> WebSocket closed with code 4403."""
        from starlette.websockets import WebSocketDisconnect
        self._add_user("limited", "pass", role="user", bots=["other-bot"])
        token = _create_session("limited")
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with self.client.websocket_connect(
                "/api/bots/secretbot/terminal",
                cookies={"cfm_session": token},
            ):
                pass  # pragma: no cover
        assert exc_info.value.code == 4403

    def test_auth_disabled_accepts(self, monkeypatch):
        """AUTH_DISABLED=True -> accepts connection, but container not found -> error JSON."""
        monkeypatch.setattr(config, "AUTH_DISABLED", True)
        self.mock_client.containers.get.side_effect = self.docker.errors.NotFound("nope")
        with self.client.websocket_connect("/api/bots/mybot/terminal") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "error"
            assert "not found" in msg["message"].lower()

    def test_container_not_found(self):
        """Valid auth but container doesn't exist -> error JSON and close."""
        token = self._login()
        self.mock_client.containers.get.side_effect = self.docker.errors.NotFound("nope")
        with self.client.websocket_connect(
            "/api/bots/mybot/terminal",
            cookies={"cfm_session": token},
        ) as ws:
            msg = ws.receive_json()
            assert msg == {"type": "error", "message": "Container not found"}

    def test_container_not_running(self):
        """Valid auth but container is stopped -> error JSON and close."""
        token = self._login()
        mc = MagicMock()
        mc.status = "exited"
        self.mock_client.containers.get.return_value = mc
        with self.client.websocket_connect(
            "/api/bots/mybot/terminal",
            cookies={"cfm_session": token},
        ) as ws:
            msg = ws.receive_json()
            assert msg == {"type": "error", "message": "Container is not running"}

    def test_name_sanitization(self):
        """Dangerous characters in name get sanitized before container lookup."""
        token = self._login()
        self.mock_client.containers.get.side_effect = self.docker.errors.NotFound("nope")
        # Name with dots, spaces, and special chars should be sanitized
        with self.client.websocket_connect(
            "/api/bots/My..Bot$$Name/terminal",
            cookies={"cfm_session": token},
        ) as ws:
            msg = ws.receive_json()
            assert msg["type"] == "error"
        # The container lookup should use the sanitized name
        call_args = self.mock_client.containers.get.call_args[0][0]
        assert call_args == "openclaw-bot-my-bot-name"

    def test_invalid_name_closes_4400(self):
        """Names that sanitize to empty (e.g. '!!!') close with 4400."""
        from starlette.websockets import WebSocketDisconnect
        token = self._login()
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with self.client.websocket_connect(
                "/api/bots/!!!/terminal",
                cookies={"cfm_session": token},
            ):
                pass  # pragma: no cover
        assert exc_info.value.code == 4400
