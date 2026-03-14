import json
from unittest.mock import MagicMock

from bots import _redact_config, get_bot_cron_jobs, get_bot_storage, get_bot_token_usage, get_fleet_stats
from docker_utils import _effective_status
from tests.helpers import _create_test_bot


class TestBotStorage:
    def test_storage_returns_byte_count(self, bot_env):
        bots_dir = bot_env["bots_dir"]
        _create_test_bot(bots_dir, "mybot")
        size = get_bot_storage("mybot")
        assert isinstance(size, int)
        assert size > 0

    def test_storage_returns_zero_for_missing_bot(self, bot_env):
        assert get_bot_storage("nonexistent") == 0

    def test_storage_counts_nested_files(self, bot_env):
        bots_dir = bot_env["bots_dir"]
        _create_test_bot(bots_dir, "nested")
        sub = bots_dir / "nested" / "subdir"
        sub.mkdir()
        (sub / "data.bin").write_bytes(b"x" * 1024)
        size = get_bot_storage("nested")
        assert size >= 1024


class TestBotCronJobs:
    def test_returns_jobs_from_file(self, bot_env):
        bots_dir = bot_env["bots_dir"]
        _create_test_bot(bots_dir, "cronbot")
        cron_dir = bots_dir / "cronbot" / ".openclaw" / "cron"
        cron_dir.mkdir(parents=True)
        (cron_dir / "jobs.json").write_text(json.dumps({
            "version": 1,
            "jobs": [
                {"id": "j1", "name": "daily check", "schedule": "0 9 * * *", "enabled": True}
            ]
        }))
        jobs = get_bot_cron_jobs("cronbot")
        assert len(jobs) == 1
        assert jobs[0]["id"] == "j1"
        assert jobs[0]["schedule"] == "0 9 * * *"

    def test_returns_empty_list_when_no_cron_file(self, bot_env):
        bots_dir = bot_env["bots_dir"]
        _create_test_bot(bots_dir, "nocron")
        assert get_bot_cron_jobs("nocron") == []

    def test_returns_empty_list_for_missing_bot(self, bot_env):
        assert get_bot_cron_jobs("ghost") == []

    def test_returns_empty_list_for_malformed_json(self, bot_env):
        bots_dir = bot_env["bots_dir"]
        _create_test_bot(bots_dir, "badcron")
        cron_dir = bots_dir / "badcron" / ".openclaw" / "cron"
        cron_dir.mkdir(parents=True)
        (cron_dir / "jobs.json").write_text("not json")
        assert get_bot_cron_jobs("badcron") == []


class TestTokenUsage:
    def test_token_usage_no_sessions(self, bot_env):
        bots_dir = bot_env["bots_dir"]
        _create_test_bot(bots_dir, "mybot")
        result = get_bot_token_usage("mybot")
        assert result["total_tokens"] == 0
        assert result["model"] is None

    def test_token_usage_reads_sessions(self, bot_env):
        bots_dir = bot_env["bots_dir"]
        _create_test_bot(bots_dir, "mybot")
        sessions_dir = bots_dir / "mybot" / ".openclaw" / "agents" / "main" / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        sessions_data = {
            "session-1": {
                "inputTokens": 1000, "outputTokens": 200,
                "contextTokens": 262144, "model": "claude-sonnet-4-6",
            },
            "session-2": {
                "inputTokens": 500, "outputTokens": 100,
                "contextTokens": 262144, "model": "claude-sonnet-4-6",
            },
        }
        (sessions_dir / "sessions.json").write_text(json.dumps(sessions_data))
        result = get_bot_token_usage("mybot")
        assert result["input_tokens"] == 1500
        assert result["output_tokens"] == 300
        assert result["total_tokens"] == 1800
        assert result["context_tokens"] == 262144
        assert result["model"] == "claude-sonnet-4-6"


class TestFleetStats:
    def _mock_container(self, name, status="running", port=3001, health_status="healthy"):
        """Create a mock Docker container for fleet stats tests."""
        container = MagicMock()
        container.labels = {"openclaw.name": name, "openclaw.port": str(port)}
        container.name = f"openclaw-bot-{name}"
        container.status = status
        health = {"Status": health_status} if status == "running" else {}
        container.attrs = {
            "State": {"StartedAt": "2026-02-28T10:00:00Z", "Health": health},
            "RestartCount": 0,
        }
        container.reload = MagicMock()
        container.stats.return_value = {
            "cpu_stats": {
                "cpu_usage": {"total_usage": 200000},
                "system_cpu_usage": 10000000,
                "online_cpus": 4,
            },
            "precpu_stats": {
                "cpu_usage": {"total_usage": 100000},
                "system_cpu_usage": 9000000,
            },
            "memory_stats": {"usage": 100 * 1024 * 1024, "limit": 1024 * 1024 * 1024},
            "networks": {"eth0": {"rx_bytes": 5 * 1024 * 1024, "tx_bytes": 2 * 1024 * 1024}},
        }
        return container

    def test_fleet_stats_aggregates_running_bots(self, bot_env, monkeypatch):
        bots_dir = bot_env["bots_dir"]
        _create_test_bot(bots_dir, "bot-a")
        _create_test_bot(bots_dir, "bot-b")

        containers = [
            self._mock_container("bot-a", status="running", port=3001),
            self._mock_container("bot-b", status="exited", port=3002),
        ]
        mock_client = MagicMock()
        mock_client.containers.list.return_value = containers
        monkeypatch.setattr("docker_utils._get_client", lambda: mock_client)

        result = get_fleet_stats()
        assert result["total_bots"] == 2
        assert result["running_bots"] == 1
        assert result["starting_bots"] == 0
        assert result["total_cpu_percent"] > 0
        assert result["total_memory_mb"] > 0
        assert result["total_storage_bytes"] > 0
        assert "total_tokens_used" in result
        assert "tokens_by_model" in result
        assert isinstance(result["tokens_by_model"], dict)

    def test_fleet_stats_starting_bot(self, bot_env, monkeypatch):
        bots_dir = bot_env["bots_dir"]
        _create_test_bot(bots_dir, "bot-a")
        _create_test_bot(bots_dir, "bot-b")

        containers = [
            self._mock_container("bot-a", status="running", port=3001, health_status="healthy"),
            self._mock_container("bot-b", status="running", port=3002, health_status="starting"),
        ]
        mock_client = MagicMock()
        mock_client.containers.list.return_value = containers
        monkeypatch.setattr("docker_utils._get_client", lambda: mock_client)

        result = get_fleet_stats()
        assert result["total_bots"] == 2
        assert result["running_bots"] == 1
        assert result["starting_bots"] == 1
        # Both contribute to resource stats
        assert result["total_cpu_percent"] > 0

    def test_fleet_stats_empty_fleet(self, bot_env, monkeypatch):
        mock_client = MagicMock()
        mock_client.containers.list.return_value = []
        monkeypatch.setattr("docker_utils._get_client", lambda: mock_client)

        result = get_fleet_stats()
        assert result["total_bots"] == 0
        assert result["running_bots"] == 0
        assert result["starting_bots"] == 0
        assert result["total_cpu_percent"] == 0
        assert result["total_storage_bytes"] == 0
        assert result["tokens_by_model"] == {}

    def test_fleet_stats_rbac_filtering(self, bot_env, monkeypatch):
        """allowed_bots filters to only the specified bots."""
        bots_dir = bot_env["bots_dir"]
        _create_test_bot(bots_dir, "bot-a")
        _create_test_bot(bots_dir, "bot-b")

        containers = [
            self._mock_container("bot-a", status="running", port=3001),
            self._mock_container("bot-b", status="running", port=3002),
        ]
        mock_client = MagicMock()
        mock_client.containers.list.return_value = containers
        monkeypatch.setattr("docker_utils._get_client", lambda: mock_client)

        result = get_fleet_stats(allowed_bots={"bot-a"})
        assert result["total_bots"] == 1
        assert result["running_bots"] == 1

    def test_fleet_stats_rbac_none_means_all(self, bot_env, monkeypatch):
        """allowed_bots=None returns all bots (admin behavior)."""
        bots_dir = bot_env["bots_dir"]
        _create_test_bot(bots_dir, "bot-a")
        _create_test_bot(bots_dir, "bot-b")

        containers = [
            self._mock_container("bot-a", status="running", port=3001),
            self._mock_container("bot-b", status="running", port=3002),
        ]
        mock_client = MagicMock()
        mock_client.containers.list.return_value = containers
        monkeypatch.setattr("docker_utils._get_client", lambda: mock_client)

        result = get_fleet_stats(allowed_bots=None)
        assert result["total_bots"] == 2

    def test_fleet_stats_tokens_by_model(self, bot_env, monkeypatch):
        """tokens_by_model aggregates per-model token counts across bots."""
        bots_dir = bot_env["bots_dir"]
        # bot-a uses claude-sonnet-4-6
        _create_test_bot(bots_dir, "bot-a")
        sess_a = bots_dir / "bot-a" / ".openclaw" / "agents" / "main" / "sessions"
        sess_a.mkdir(parents=True, exist_ok=True)
        (sess_a / "sessions.json").write_text(json.dumps({
            "s1": {"inputTokens": 1000, "outputTokens": 500, "model": "claude-sonnet-4-6"},
        }))
        # bot-b uses gpt-4o
        _create_test_bot(bots_dir, "bot-b")
        sess_b = bots_dir / "bot-b" / ".openclaw" / "agents" / "main" / "sessions"
        sess_b.mkdir(parents=True, exist_ok=True)
        (sess_b / "sessions.json").write_text(json.dumps({
            "s1": {"inputTokens": 2000, "outputTokens": 1000, "model": "gpt-4o"},
        }))

        containers = [
            self._mock_container("bot-a", status="running", port=3001),
            self._mock_container("bot-b", status="running", port=3002),
        ]
        mock_client = MagicMock()
        mock_client.containers.list.return_value = containers
        monkeypatch.setattr("docker_utils._get_client", lambda: mock_client)

        result = get_fleet_stats()
        assert result["tokens_by_model"]["claude-sonnet-4-6"] == 1500
        assert result["tokens_by_model"]["gpt-4o"] == 3000
        assert result["total_tokens_used"] == 4500


class TestEffectiveStatus:
    def test_running_healthy(self):
        c = MagicMock()
        c.status = "running"
        c.attrs = {"State": {"Health": {"Status": "healthy"}}}
        c.reload = MagicMock()
        assert _effective_status(c) == "running"

    def test_running_starting(self):
        c = MagicMock()
        c.status = "running"
        c.attrs = {"State": {"Health": {"Status": "starting"}}}
        c.reload = MagicMock()
        assert _effective_status(c) == "starting"

    def test_running_unhealthy(self):
        c = MagicMock()
        c.status = "running"
        c.attrs = {"State": {"Health": {"Status": "unhealthy"}}}
        c.reload = MagicMock()
        assert _effective_status(c) == "unhealthy"

    def test_running_no_health(self):
        """Backward compat: containers without healthcheck."""
        c = MagicMock()
        c.status = "running"
        c.attrs = {"State": {"StartedAt": "2026-02-28T10:00:00Z"}}
        c.reload = MagicMock()
        assert _effective_status(c) == "running"

    def test_exited(self):
        c = MagicMock()
        c.status = "exited"
        c.attrs = {"State": {}}
        assert _effective_status(c) == "exited"


class TestRedactConfig:
    def test_redacts_api_keys(self):
        config = {
            "models": {"providers": {"default": {"apiKey": "secret123", "baseUrl": "http://x"}}},
            "tools": {"web": {"search": {"provider": "brave", "apiKey": "brave-secret"}}},
        }
        redacted = _redact_config(config)
        assert redacted["models"]["providers"]["default"]["apiKey"] == "***"
        assert redacted["tools"]["web"]["search"]["apiKey"] == "***"
        # Original not mutated
        assert config["models"]["providers"]["default"]["apiKey"] == "secret123"

    def test_redacts_gateway_token(self):
        config = {"gateway": {"auth": {"token": "super-secret"}}}
        redacted = _redact_config(config)
        assert redacted["gateway"]["auth"]["token"] == "***"
