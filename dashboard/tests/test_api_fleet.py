from unittest.mock import MagicMock

import pytest

import config
from auth import _bootstrap_admin, _hash_password, _load_users, _save_users
from tests.helpers import _create_test_bot


class TestFunctionalEndpointsAPI:
    """Functional tests for config, fleet stats, and bot list endpoints."""

    @pytest.fixture(autouse=True)
    def setup(self, bot_env, auth_env, monkeypatch):
        from fastapi.testclient import TestClient

        import app as _app
        monkeypatch.setattr(config, "AUTH_DISABLED", True)
        self.mock_client = MagicMock()
        monkeypatch.setattr("docker_utils._get_client", lambda: self.mock_client)
        self.client = TestClient(_app.app)
        self.bot_env = bot_env
        self._app = _app
        self.monkeypatch = monkeypatch

    def test_config_returns_settings(self):
        self.monkeypatch.setattr(config, "PORTAL_URL", "https://farm.example.com")
        self.monkeypatch.setattr(config, "CADDY_PORT", 8443)
        self.monkeypatch.setattr(config, "TLS_MODE", "internal")
        r = self.client.get("/api/config")
        assert r.status_code == 200
        data = r.json()
        assert data["portal_url"] == "https://farm.example.com"
        assert data["caddy_port"] == 8443
        assert data["tls_mode"] == "internal"

    def test_config_null_portal(self):
        self.monkeypatch.setattr(config, "PORTAL_URL", "")
        r = self.client.get("/api/config")
        assert r.status_code == 200
        assert r.json()["portal_url"] is None

    def test_templates_list(self):
        r = self.client.get("/api/templates")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)
        names = [t["name"] for t in data]
        assert "default" in names

    def test_fleet_stats_structure(self):
        """Fleet stats returns expected keys with mocked empty fleet."""
        self.mock_client.containers.list.return_value = []
        r = self.client.get("/api/fleet/stats")
        assert r.status_code == 200
        data = r.json()
        assert data["total_bots"] == 0
        assert "running_bots" in data
        assert "total_memory_mb" in data
        assert "total_tokens_used" in data
        assert data["tokens_by_model"] == {}

    def test_fleet_stats_with_bots(self):
        """Fleet stats aggregates across running bots."""
        mc = MagicMock()
        mc.status = "running"
        mc.labels = {"openclaw.name": "a", "openclaw.port": "3001"}
        mc.attrs = {
            "State": {"Health": {"Status": "healthy"}, "StartedAt": "2025-01-01T00:00:00Z"},
        }
        mc.stats.return_value = {
            "cpu_stats": {"cpu_usage": {"total_usage": 100}, "system_cpu_usage": 1000, "online_cpus": 1},
            "precpu_stats": {"cpu_usage": {"total_usage": 50}, "system_cpu_usage": 500},
            "memory_stats": {"usage": 1048576, "limit": 8388608},
            "networks": {},
        }
        self.mock_client.containers.list.return_value = [mc]
        r = self.client.get("/api/fleet/stats")
        assert r.status_code == 200
        data = r.json()
        assert data["total_bots"] == 1
        assert data["running_bots"] == 1

    def test_list_bots_empty(self):
        self.mock_client.containers.list.return_value = []
        r = self.client.get("/api/bots")
        assert r.status_code == 200
        assert r.json() == []

    def test_list_bots_returns_data(self):
        mc = MagicMock()
        mc.status = "running"
        mc.name = "openclaw-bot-test"
        mc.labels = {"openclaw.name": "test", "openclaw.port": "3001", "openclaw.bot": "true"}
        mc.attrs = {"State": {"Health": {"Status": "healthy"}, "StartedAt": "2026-02-28T10:00:00Z"}}
        self.mock_client.containers.list.return_value = [mc]
        r = self.client.get("/api/bots")
        assert r.status_code == 200
        bots = r.json()
        assert len(bots) == 1
        assert bots[0]["name"] == "test"
        assert bots[0]["status"] == "running"
        assert bots[0]["uptime_seconds"] > 0
        assert bots[0]["started_at"] == "2026-02-28T10:00:00Z"

    def test_list_bots_rbac_filtering(self):
        """Non-admin users only see bots they have access to."""
        self.monkeypatch.setattr(config, "AUTH_DISABLED", False)
        # Set up auth with a limited user
        self.monkeypatch.setenv("ADMIN_PASSWORD", "admin-pass")
        _bootstrap_admin()
        users = _load_users()
        users["alice"] = {"password_hash": _hash_password("alice-pass"), "role": "user", "bots": ["allowed-bot"]}
        _save_users(users)
        # Login as alice
        self.client.post("/api/auth/login", json={"username": "alice", "password": "alice-pass"})
        # Mock containers with two bots
        bots = []
        for name in ["allowed-bot", "secret-bot"]:
            mc = MagicMock()
            mc.status = "running"
            mc.name = f"openclaw-bot-{name}"
            mc.labels = {"openclaw.bot": "true", "openclaw.name": name, "openclaw.port": "3001"}
            mc.attrs = {"State": {"Health": {"Status": "healthy"}}}
            bots.append(mc)
        self.mock_client.containers.list.return_value = bots
        r = self.client.get("/api/bots")
        assert r.status_code == 200
        names = [b["name"] for b in r.json()]
        assert "allowed-bot" in names
        assert "secret-bot" not in names

    def test_fleet_stats_rbac_filtering(self):
        """Non-admin users only see fleet stats for their bots."""
        self.monkeypatch.setattr(config, "AUTH_DISABLED", False)
        self.monkeypatch.setenv("ADMIN_PASSWORD", "admin-pass")
        _bootstrap_admin()
        users = _load_users()
        users["bob"] = {"password_hash": _hash_password("bob-pass"), "role": "user", "bots": ["my-bot"]}
        _save_users(users)
        self.client.post("/api/auth/login", json={"username": "bob", "password": "bob-pass"})
        # Mock two containers -- bob can only see "my-bot"
        bots_dir = self.bot_env["bots_dir"]
        _create_test_bot(bots_dir, "my-bot")
        _create_test_bot(bots_dir, "secret-bot")
        containers = []
        for name in ["my-bot", "secret-bot"]:
            mc = MagicMock()
            mc.status = "running"
            mc.name = f"openclaw-bot-{name}"
            mc.labels = {"openclaw.name": name, "openclaw.port": "3001", "openclaw.bot": "true"}
            mc.attrs = {"State": {"Health": {"Status": "healthy"}, "StartedAt": "2025-01-01T00:00:00Z"}}
            mc.stats.return_value = {
                "cpu_stats": {"cpu_usage": {"total_usage": 100}, "system_cpu_usage": 1000, "online_cpus": 1},
                "precpu_stats": {"cpu_usage": {"total_usage": 50}, "system_cpu_usage": 500},
                "memory_stats": {"usage": 1048576, "limit": 8388608},
                "networks": {},
            }
            containers.append(mc)
        self.mock_client.containers.list.return_value = containers
        r = self.client.get("/api/fleet/stats")
        assert r.status_code == 200
        data = r.json()
        # Bob should only see stats for 1 bot (my-bot), not 2
        assert data["total_bots"] == 1
        assert data["running_bots"] == 1


class TestHealthEndpoint:
    @pytest.fixture(autouse=True)
    def setup(self, auth_env, monkeypatch):
        from fastapi.testclient import TestClient

        from app import app
        monkeypatch.setenv("ADMIN_PASSWORD", "admin-pass")
        _bootstrap_admin()
        self.client = TestClient(app)

    def test_health_returns_200_without_auth(self):
        """Health endpoint works without authentication."""
        r = self.client.get("/api/health")
        assert r.status_code == 200
        assert r.json() == {"ok": True}
