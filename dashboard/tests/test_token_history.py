import json
from unittest.mock import MagicMock, patch

from token_history import (
    _MAX_ENTRIES,
    _fleet_history_file,
    _read_history,
    _read_jsonl,
    _snapshot_file,
    _snapshot_one_bot,
    _update_fleet_history,
    _write_history,
    collect_token_snapshots,
    get_fleet_token_chart,
    get_sparkline_data,
)


# ===========================================================================
# Helpers
# ===========================================================================
def _create_bot_with_sessions(bots_dir, name, sessions=None):
    """Create a bot dir with optional sessions.json data."""
    bot_dir = bots_dir / name
    sessions_dir = bot_dir / ".openclaw" / "agents" / "main" / "sessions"
    sessions_dir.mkdir(parents=True)
    if sessions is not None:
        (sessions_dir / "sessions.json").write_text(json.dumps(sessions))
    return bot_dir


# ===========================================================================
# _read_history / _write_history
# ===========================================================================
class TestReadWriteHistory:
    def test_empty_when_no_file(self, bot_env):
        assert _read_history("nonexistent") == []

    def test_write_and_read_roundtrip(self, bot_env):
        bots_dir = bot_env["bots_dir"]
        (bots_dir / "bot-a").mkdir()
        entries = [{"ts": "2026-01-01T00:00:00Z", "total": 10, "cumulative": 100}]
        _write_history("bot-a", entries)
        result = _read_history("bot-a")
        assert len(result) == 1
        assert result[0]["total"] == 10

    def test_skips_malformed_lines(self, bot_env):
        bots_dir = bot_env["bots_dir"]
        (bots_dir / "bot-b").mkdir()
        path = _snapshot_file("bot-b")
        path.write_text('{"ts":"t1","total":5}\nBAD LINE\n{"ts":"t2","total":10}\n')
        result = _read_history("bot-b")
        assert len(result) == 2
        assert result[0]["total"] == 5
        assert result[1]["total"] == 10

    def test_retention_prunes_to_max(self, bot_env):
        bots_dir = bot_env["bots_dir"]
        (bots_dir / "bot-c").mkdir()
        entries = [{"ts": f"t{i}", "total": i, "cumulative": i * 10} for i in range(150)]
        _write_history("bot-c", entries)
        result = _read_history("bot-c")
        assert len(result) == _MAX_ENTRIES
        # Should keep the last entries
        assert result[0]["ts"] == f"t{150 - _MAX_ENTRIES}"
        assert result[-1]["ts"] == "t149"


# ===========================================================================
# _snapshot_one_bot
# ===========================================================================
class TestSnapshotOneBot:
    def test_first_snapshot_uses_cumulative_as_delta(self, bot_env):
        bots_dir = bot_env["bots_dir"]
        _create_bot_with_sessions(bots_dir, "bot-x", {
            "s1": {"inputTokens": 100, "outputTokens": 50, "model": "test-model"},
        })
        _snapshot_one_bot("bot-x")
        history = _read_history("bot-x")
        assert len(history) == 1
        assert history[0]["in"] == 100
        assert history[0]["out"] == 50
        assert history[0]["total"] == 150
        assert history[0]["cumulative"] == 150
        assert history[0]["model"] == "test-model"

    def test_delta_computation(self, bot_env):
        bots_dir = bot_env["bots_dir"]
        _create_bot_with_sessions(bots_dir, "bot-y", {
            "s1": {"inputTokens": 200, "outputTokens": 80, "model": "m1"},
        })
        # Write a previous snapshot with cumulative values
        _write_history("bot-y", [
            {"ts": "t0", "in": 100, "out": 50, "total": 150,
             "cumulative": 150, "cum_in": 100, "cum_out": 50, "model": "m1"},
        ])
        _snapshot_one_bot("bot-y")
        history = _read_history("bot-y")
        assert len(history) == 2
        latest = history[-1]
        assert latest["in"] == 100   # 200 - 100
        assert latest["out"] == 30   # 80 - 50
        assert latest["total"] == 130
        assert latest["cumulative"] == 280

    def test_zero_delta_when_no_change(self, bot_env):
        bots_dir = bot_env["bots_dir"]
        _create_bot_with_sessions(bots_dir, "bot-z", {
            "s1": {"inputTokens": 100, "outputTokens": 50},
        })
        _write_history("bot-z", [
            {"ts": "t0", "in": 100, "out": 50, "total": 150,
             "cumulative": 150, "cum_in": 100, "cum_out": 50},
        ])
        _snapshot_one_bot("bot-z")
        history = _read_history("bot-z")
        latest = history[-1]
        assert latest["in"] == 0
        assert latest["out"] == 0
        assert latest["total"] == 0

    def test_negative_delta_clamped_to_zero(self, bot_env):
        """After rollback, cumulative can decrease — delta should be 0."""
        bots_dir = bot_env["bots_dir"]
        _create_bot_with_sessions(bots_dir, "bot-r", {
            "s1": {"inputTokens": 50, "outputTokens": 20},
        })
        _write_history("bot-r", [
            {"ts": "t0", "in": 100, "out": 50, "total": 150,
             "cumulative": 150, "cum_in": 100, "cum_out": 50},
        ])
        _snapshot_one_bot("bot-r")
        history = _read_history("bot-r")
        latest = history[-1]
        assert latest["in"] == 0
        assert latest["out"] == 0
        assert latest["total"] == 0
        assert latest["cumulative"] == 70  # actual current total

    def test_missing_sessions_file(self, bot_env):
        bots_dir = bot_env["bots_dir"]
        # Create bot dir without sessions.json
        (bots_dir / "bot-empty").mkdir()
        _snapshot_one_bot("bot-empty")
        history = _read_history("bot-empty")
        assert len(history) == 1
        assert history[0]["total"] == 0
        assert history[0]["cumulative"] == 0


# ===========================================================================
# collect_token_snapshots
# ===========================================================================
class TestCollectTokenSnapshots:
    def test_parallel_collection(self, bot_env):
        bots_dir = bot_env["bots_dir"]
        _create_bot_with_sessions(bots_dir, "bot-1", {
            "s1": {"inputTokens": 100, "outputTokens": 50},
        })
        _create_bot_with_sessions(bots_dir, "bot-2", {
            "s1": {"inputTokens": 200, "outputTokens": 80},
        })

        # Mock Docker client to return containers
        mock_c1 = MagicMock()
        mock_c1.labels = {"openclaw.name": "bot-1", "openclaw.bot": "true"}
        mock_c2 = MagicMock()
        mock_c2.labels = {"openclaw.name": "bot-2", "openclaw.bot": "true"}

        mock_client = MagicMock()
        mock_client.containers.list.return_value = [mock_c1, mock_c2]

        with patch("token_history.docker_utils._get_client", return_value=mock_client):
            collect_token_snapshots()

        h1 = _read_history("bot-1")
        h2 = _read_history("bot-2")
        assert len(h1) == 1
        assert len(h2) == 1
        assert h1[0]["cumulative"] == 150
        assert h2[0]["cumulative"] == 280


# ===========================================================================
# get_sparkline_data
# ===========================================================================
class TestGetSparklineData:
    def test_returns_ts_and_total(self, bot_env):
        bots_dir = bot_env["bots_dir"]
        (bots_dir / "bot-s").mkdir()
        _write_history("bot-s", [
            {"ts": "t1", "in": 10, "out": 5, "total": 15, "cumulative": 100, "model": "m"},
            {"ts": "t2", "in": 20, "out": 10, "total": 30, "cumulative": 130, "model": "m"},
        ])
        result = get_sparkline_data("bot-s")
        assert result == [{"ts": "t1", "total": 15}, {"ts": "t2", "total": 30}]

    def test_empty_history_returns_empty_list(self, bot_env):
        assert get_sparkline_data("nonexistent-bot") == []


# ===========================================================================
# Fleet token history (hourly aggregation by model)
# ===========================================================================
class TestFleetHistory:
    def test_update_creates_hourly_bucket(self, bot_env):
        entries = [
            {"ts": "t1", "in": 100, "out": 50, "total": 150, "model": "model-a", "bot": "bot-a"},
            {"ts": "t1", "in": 200, "out": 80, "total": 280, "model": "model-b", "bot": "bot-b"},
        ]
        _update_fleet_history(entries)
        result = _read_jsonl(_fleet_history_file())
        assert len(result) == 1
        assert result[0]["bots"]["bot-a"]["total"] == 150
        assert result[0]["bots"]["bot-b"]["total"] == 280
        # Verify get_fleet_token_chart aggregates into models
        chart = get_fleet_token_chart()
        assert chart[0]["models"]["model-a"] == 150
        assert chart[0]["models"]["model-b"] == 280

    def test_accumulates_within_same_hour(self, bot_env):
        entries1 = [{"ts": "t1", "in": 50, "out": 10, "total": 60, "model": "m1", "bot": "b1"}]
        _update_fleet_history(entries1)
        entries2 = [{"ts": "t2", "in": 30, "out": 20, "total": 50, "model": "m1", "bot": "b1"}]
        _update_fleet_history(entries2)
        result = _read_jsonl(_fleet_history_file())
        assert len(result) == 1
        assert result[0]["bots"]["b1"]["total"] == 110

    def test_skips_zero_delta_entries(self, bot_env):
        entries = [
            {"ts": "t1", "in": 0, "out": 0, "total": 0, "model": "m1", "bot": "b1"},
        ]
        _update_fleet_history(entries)
        result = _read_jsonl(_fleet_history_file())
        assert len(result) == 0

    def test_skips_none_entries(self, bot_env):
        entries = [None, {"ts": "t1", "in": 10, "out": 5, "total": 15, "model": "m1", "bot": "b1"}, None]
        _update_fleet_history(entries)
        result = _read_jsonl(_fleet_history_file())
        assert len(result) == 1
        assert result[0]["bots"]["b1"]["total"] == 15

    def test_rbac_filters_by_allowed_bots(self, bot_env):
        """Non-admin users should only see data from their allowed bots."""
        entries = [
            {"ts": "t1", "in": 100, "out": 50, "total": 150, "model": "m1", "bot": "bot-mine"},
            {"ts": "t1", "in": 200, "out": 80, "total": 280, "model": "m1", "bot": "bot-theirs"},
        ]
        _update_fleet_history(entries)
        # Admin sees all
        all_data = get_fleet_token_chart(allowed_bots=None)
        assert all_data[0]["models"]["m1"] == 430
        # Limited user sees only their bot
        filtered = get_fleet_token_chart(allowed_bots={"bot-mine"})
        assert filtered[0]["models"]["m1"] == 150
        # User with no matching bots sees nothing
        empty = get_fleet_token_chart(allowed_bots={"bot-other"})
        assert empty == []

    def test_collect_updates_fleet_history(self, bot_env):
        bots_dir = bot_env["bots_dir"]
        _create_bot_with_sessions(bots_dir, "bot-f1", {
            "s1": {"inputTokens": 100, "outputTokens": 50, "model": "test-model"},
        })

        mock_c1 = MagicMock()
        mock_c1.labels = {"openclaw.name": "bot-f1", "openclaw.bot": "true"}
        mock_client = MagicMock()
        mock_client.containers.list.return_value = [mock_c1]

        with patch("token_history.docker_utils._get_client", return_value=mock_client):
            collect_token_snapshots()

        result = get_fleet_token_chart()
        assert len(result) == 1
        assert "test-model" in result[0]["models"]
        assert result[0]["models"]["test-model"] == 150

    def test_get_fleet_token_chart_empty(self, bot_env):
        assert get_fleet_token_chart() == []
