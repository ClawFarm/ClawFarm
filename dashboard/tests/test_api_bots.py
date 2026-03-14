import json
from unittest.mock import MagicMock

import pytest

import config
from backup import create_backup
from tests.helpers import _create_test_bot
from utils import write_meta


class TestBotLifecycleAPI:
    """Integration tests for bot lifecycle endpoints via HTTP."""

    @pytest.fixture(autouse=True)
    def setup(self, bot_env, auth_env, monkeypatch):
        import docker as _docker
        from fastapi.testclient import TestClient

        import app as _app
        monkeypatch.setattr(config, "AUTH_DISABLED", True)
        self.mock_client = MagicMock()
        monkeypatch.setattr("docker_utils._get_client", lambda: self.mock_client)
        monkeypatch.setattr("caddy._sync_caddy_config_async", lambda: None)
        monkeypatch.setattr("caddy._sync_caddy_config", lambda: None)
        self.client = TestClient(_app.app)
        self.bot_env = bot_env
        self.docker = _docker

    def _mock_container(self, status="running", healthy=True):
        c = MagicMock()
        c.status = status
        c.name = "openclaw-bot-test"
        c.labels = {"openclaw.name": "test", "openclaw.port": "3001"}
        if healthy:
            c.attrs = {"State": {"Health": {"Status": "healthy"}}}
        else:
            c.attrs = {"State": {}}
        return c

    def test_start_bot_success(self):
        mc = self._mock_container()
        self.mock_client.containers.get.return_value = mc
        r = self.client.post("/api/bots/test/start")
        assert r.status_code == 200
        mc.start.assert_called_once()
        assert r.json()["name"] == "test"

    def test_start_bot_not_found(self):
        self.mock_client.containers.get.side_effect = self.docker.errors.NotFound("nope")
        r = self.client.post("/api/bots/test/start")
        assert r.status_code == 404

    def test_stop_bot_success(self):
        mc = self._mock_container()
        self.mock_client.containers.get.return_value = mc
        r = self.client.post("/api/bots/test/stop")
        assert r.status_code == 200
        mc.stop.assert_called_once()
        assert r.json()["status"] == "stopped"

    def test_stop_bot_not_found(self):
        self.mock_client.containers.get.side_effect = self.docker.errors.NotFound("nope")
        r = self.client.post("/api/bots/test/stop")
        assert r.status_code == 404

    def test_restart_bot_success(self):
        mc = self._mock_container()
        self.mock_client.containers.get.return_value = mc
        r = self.client.post("/api/bots/test/restart")
        assert r.status_code == 200
        mc.restart.assert_called_once()

    def test_restart_bot_not_found(self):
        self.mock_client.containers.get.side_effect = self.docker.errors.NotFound("nope")
        r = self.client.post("/api/bots/test/restart")
        assert r.status_code == 404

    def test_delete_bot_success(self):
        bots_dir = self.bot_env["bots_dir"]
        _create_test_bot(bots_dir, "victim")
        mc = self._mock_container()
        self.mock_client.containers.get.return_value = mc
        self.mock_client.networks.get.side_effect = self.docker.errors.NotFound("nope")
        r = self.client.delete("/api/bots/victim")
        assert r.status_code == 200
        assert r.json()["deleted"] == "victim"
        assert not (bots_dir / "victim").exists()

    def test_delete_bot_no_container(self):
        """Delete succeeds even if container doesn't exist (cleans up dir)."""
        bots_dir = self.bot_env["bots_dir"]
        _create_test_bot(bots_dir, "orphan")
        self.mock_client.containers.get.side_effect = self.docker.errors.NotFound("nope")
        self.mock_client.networks.get.side_effect = self.docker.errors.NotFound("nope")
        r = self.client.delete("/api/bots/orphan")
        assert r.status_code == 200

    def test_logs_success(self):
        mc = self._mock_container()
        mc.logs.return_value = b"line1\nline2\nline3"
        self.mock_client.containers.get.return_value = mc
        r = self.client.get("/api/bots/test/logs")
        assert r.status_code == 200
        assert "line1" in r.json()["logs"]

    def test_logs_not_found(self):
        self.mock_client.containers.get.side_effect = self.docker.errors.NotFound("nope")
        r = self.client.get("/api/bots/test/logs")
        assert r.status_code == 404


class TestFilesystemReadAPI:
    """Integration tests for meta, detail, stats endpoints."""

    @pytest.fixture(autouse=True)
    def setup(self, bot_env, auth_env, monkeypatch):
        import docker as _docker
        from fastapi.testclient import TestClient

        import app as _app
        monkeypatch.setattr(config, "AUTH_DISABLED", True)
        self.mock_client = MagicMock()
        monkeypatch.setattr("docker_utils._get_client", lambda: self.mock_client)
        self.client = TestClient(_app.app)
        self.bot_env = bot_env
        self.docker = _docker

    def test_meta_success(self):
        bots_dir = self.bot_env["bots_dir"]
        _create_test_bot(bots_dir, "alpha")
        write_meta("alpha", {"created_at": "2025-01-01"})
        r = self.client.get("/api/bots/alpha/meta")
        assert r.status_code == 200
        assert r.json()["created_at"] == "2025-01-01"

    def test_meta_not_found(self):
        r = self.client.get("/api/bots/nonexistent/meta")
        assert r.status_code == 404

    def test_detail_success(self):
        bots_dir = self.bot_env["bots_dir"]
        bot_dir = _create_test_bot(bots_dir, "bravo")
        # Create .openclaw structure
        oc_dir = bot_dir / ".openclaw"
        oc_dir.mkdir()
        (oc_dir / "openclaw.json").write_text(json.dumps({"agents": {}}))
        ws = oc_dir / "workspace"
        ws.mkdir()
        (ws / "SOUL.md").write_text("brave soul")
        # Mock Docker container lookup
        mc = MagicMock()
        mc.status = "running"
        mc.name = "openclaw-bot-bravo"
        mc.labels = {"openclaw.port": "3001"}
        mc.attrs = {"State": {"Health": {"Status": "healthy"}}}
        mc.stats.return_value = {
            "cpu_stats": {"cpu_usage": {"total_usage": 100}, "system_cpu_usage": 1000, "online_cpus": 1},
            "precpu_stats": {"cpu_usage": {"total_usage": 50}, "system_cpu_usage": 500},
            "memory_stats": {"usage": 1048576, "limit": 8388608},
            "networks": {},
        }
        self.mock_client.containers.get.return_value = mc
        r = self.client.get("/api/bots/bravo/detail")
        assert r.status_code == 200
        data = r.json()
        assert data["name"] == "bravo"
        assert "config" in data
        assert "stats" in data

    def test_detail_no_container(self):
        """Detail still returns data when container is missing (filesystem only)."""
        bots_dir = self.bot_env["bots_dir"]
        bot_dir = _create_test_bot(bots_dir, "ghost")
        oc_dir = bot_dir / ".openclaw"
        oc_dir.mkdir()
        (oc_dir / "openclaw.json").write_text(json.dumps({"agents": {}}))
        self.mock_client.containers.get.side_effect = self.docker.errors.NotFound("nope")
        r = self.client.get("/api/bots/ghost/detail")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "not_found"

    def test_stats_success(self):
        mc = MagicMock()
        mc.status = "running"
        mc.attrs = {
            "State": {"Health": {"Status": "healthy"}, "StartedAt": "2025-01-01T00:00:00Z"},
            "RestartCount": 2,
        }
        mc.stats.return_value = {
            "cpu_stats": {"cpu_usage": {"total_usage": 200}, "system_cpu_usage": 2000, "online_cpus": 2},
            "precpu_stats": {"cpu_usage": {"total_usage": 100}, "system_cpu_usage": 1000},
            "memory_stats": {"usage": 2097152, "limit": 16777216},
            "networks": {"eth0": {"rx_bytes": 1024, "tx_bytes": 2048}},
        }
        self.mock_client.containers.get.return_value = mc
        r = self.client.get("/api/bots/test/stats")
        assert r.status_code == 200
        data = r.json()
        assert "cpu_percent" in data
        assert "memory_mb" in data
        assert data["restart_count"] == 2

    def test_stats_not_found(self):
        self.mock_client.containers.get.side_effect = self.docker.errors.NotFound("nope")
        r = self.client.get("/api/bots/test/stats")
        assert r.status_code == 404


class TestCRUDEndpointsAPI:
    """Integration tests for create/duplicate/fork/backup/rollback via HTTP."""

    @pytest.fixture(autouse=True)
    def setup(self, bot_env, auth_env, monkeypatch):
        import docker as _docker
        from fastapi.testclient import TestClient

        import app as _app
        monkeypatch.setattr(config, "AUTH_DISABLED", True)
        self.mock_client = MagicMock()
        monkeypatch.setattr("docker_utils._get_client", lambda: self.mock_client)
        monkeypatch.setattr("caddy._sync_caddy_config_async", lambda: None)
        monkeypatch.setattr("caddy._sync_caddy_config", lambda: None)
        # Mock _launch_container to avoid real Docker calls
        self._launched = []

        def fake_launch(name, bot_dir, **kwargs):
            self._launched.append(name)
            return {"name": name, "status": "running", "port": 3001}
        monkeypatch.setattr("bots._launch_container", fake_launch)
        self.client = TestClient(_app.app)
        self.bot_env = bot_env
        self.docker = _docker

    def test_create_bot_success(self):
        r = self.client.post("/api/bots", json={"name": "newbot"})
        assert r.status_code == 200
        assert r.json()["name"] == "newbot"
        assert "newbot" in self._launched

    def test_create_bot_bad_name(self):
        r = self.client.post("/api/bots", json={"name": ""})
        assert r.status_code == 400

    def test_create_bot_duplicate_name(self):
        bots_dir = self.bot_env["bots_dir"]
        (bots_dir / "existing").mkdir()
        r = self.client.post("/api/bots", json={"name": "existing"})
        assert r.status_code == 400

    def test_create_bot_with_template(self):
        r = self.client.post("/api/bots", json={"name": "templated", "template": "default"})
        assert r.status_code == 200

    def test_duplicate_success(self):
        bots_dir = self.bot_env["bots_dir"]
        _create_test_bot(bots_dir, "original")
        r = self.client.post("/api/bots/original/duplicate", json={"new_name": "clone"})
        assert r.status_code == 200
        assert r.json()["name"] == "clone"

    def test_duplicate_source_missing(self):
        r = self.client.post("/api/bots/missing/duplicate", json={"new_name": "clone"})
        assert r.status_code == 404

    def test_duplicate_target_exists(self):
        bots_dir = self.bot_env["bots_dir"]
        _create_test_bot(bots_dir, "src")
        _create_test_bot(bots_dir, "dst")
        r = self.client.post("/api/bots/src/duplicate", json={"new_name": "dst"})
        assert r.status_code == 409

    def test_fork_success(self):
        bots_dir = self.bot_env["bots_dir"]
        _create_test_bot(bots_dir, "parent")
        r = self.client.post("/api/bots/parent/fork", json={"new_name": "child"})
        assert r.status_code == 200
        assert r.json()["name"] == "child"

    def test_fork_source_missing(self):
        r = self.client.post("/api/bots/nope/fork", json={"new_name": "child"})
        assert r.status_code == 404

    def test_fork_target_exists(self):
        bots_dir = self.bot_env["bots_dir"]
        _create_test_bot(bots_dir, "p")
        _create_test_bot(bots_dir, "c")
        r = self.client.post("/api/bots/p/fork", json={"new_name": "c"})
        assert r.status_code == 409

    def test_backup_success(self):
        bots_dir = self.bot_env["bots_dir"]
        bot_dir = _create_test_bot(bots_dir, "backed")
        oc = bot_dir / ".openclaw"
        oc.mkdir()
        (oc / "openclaw.json").write_text("{}")
        r = self.client.post("/api/bots/backed/backup")
        assert r.status_code == 200
        assert "timestamp" in r.json()

    def test_backup_bot_missing(self):
        r = self.client.post("/api/bots/ghost/backup")
        assert r.status_code == 404

    def test_list_backups_success(self):
        bots_dir = self.bot_env["bots_dir"]
        bot_dir = _create_test_bot(bots_dir, "listed")
        oc = bot_dir / ".openclaw"
        oc.mkdir()
        (oc / "openclaw.json").write_text("{}")
        # Create a backup first
        create_backup("listed")
        r = self.client.get("/api/bots/listed/backups")
        assert r.status_code == 200
        assert len(r.json()) >= 1

    def test_list_backups_bot_missing(self):
        r = self.client.get("/api/bots/ghost/backups")
        assert r.status_code == 404

    def test_rollback_success(self):
        bots_dir = self.bot_env["bots_dir"]
        bot_dir = _create_test_bot(bots_dir, "rollme")
        oc = bot_dir / ".openclaw"
        oc.mkdir()
        (oc / "openclaw.json").write_text("{}")
        ws = oc / "workspace"
        ws.mkdir()
        (ws / "SOUL.md").write_text("original")
        backup = create_backup("rollme")
        ts = backup["timestamp"]
        # Modify after backup
        (ws / "SOUL.md").write_text("changed")
        # Mock container restart
        mc = MagicMock()
        mc.status = "running"
        mc.attrs = {"State": {"Health": {"Status": "healthy"}}}
        self.mock_client.containers.get.return_value = mc
        r = self.client.post("/api/bots/rollme/rollback", json={"timestamp": ts})
        assert r.status_code == 200
        assert r.json()["status"] == "running"

    def test_rollback_bad_timestamp(self):
        bots_dir = self.bot_env["bots_dir"]
        bot_dir = _create_test_bot(bots_dir, "rollme2")
        oc = bot_dir / ".openclaw"
        oc.mkdir()
        (oc / "openclaw.json").write_text("{}")
        r = self.client.post("/api/bots/rollme2/rollback", json={"timestamp": "9999-01-01T00:00:00"})
        assert r.status_code == 404

    def test_approve_devices_trusted_proxy(self, monkeypatch):
        """In compose mode, approve-devices is a no-op."""
        monkeypatch.setenv("HOST_BOTS_DIR", "/host/bots")
        r = self.client.post("/api/bots/anybot/approve-devices")
        assert r.status_code == 200
        assert r.json()["approved"] == 0
