import json
from unittest.mock import MagicMock

import pytest

from bots import allocate_port, create_bot, duplicate_bot, fork_bot
from tests.helpers import _create_test_bot
from utils import read_meta, write_meta


class TestPortAllocation:
    def test_skips_used_ports(self, monkeypatch):
        monkeypatch.setenv("BOT_PORT_START", "3001")
        monkeypatch.setenv("BOT_PORT_END", "3005")

        mock_containers = []
        for port in [3001, 3002, 3004]:
            c = MagicMock()
            c.labels = {"openclaw.bot": "true", "openclaw.port": str(port)}
            mock_containers.append(c)

        mock_client = MagicMock()
        mock_client.containers.list.return_value = mock_containers
        monkeypatch.setattr("docker_utils._get_client", lambda: mock_client)

        assert allocate_port() == 3003

    def test_exhausted_raises(self, monkeypatch):
        monkeypatch.setenv("BOT_PORT_START", "3001")
        monkeypatch.setenv("BOT_PORT_END", "3003")

        mock_containers = []
        for port in [3001, 3002, 3003]:
            c = MagicMock()
            c.labels = {"openclaw.bot": "true", "openclaw.port": str(port)}
            mock_containers.append(c)

        mock_client = MagicMock()
        mock_client.containers.list.return_value = mock_containers
        monkeypatch.setattr("docker_utils._get_client", lambda: mock_client)

        with pytest.raises(RuntimeError):
            allocate_port()


class TestDuplicate:
    def _mock_launch(self, monkeypatch):
        """Mock _launch_container to avoid Docker."""
        def fake_launch(name, bot_dir, **kwargs):
            return {"name": name, "status": "created", "port": 3001, "container_name": f"openclaw-bot-{name}"}
        monkeypatch.setattr("bots._launch_container", fake_launch)

    def test_duplicate_copies_actual_config(self, bot_env, monkeypatch):
        self._mock_launch(monkeypatch)
        bots_dir = bot_env["bots_dir"]
        original_config = {"llm": {"baseUrl": "http://custom", "model": "custom-model"}, "custom_key": True}
        _create_test_bot(bots_dir, "original", config=original_config, soul="original soul")
        write_meta("original", {"created_at": "x", "modified_at": "x", "forked_from": None, "backups": []})

        duplicate_bot("original", "copy")

        copied_config = json.loads((bots_dir / "copy" / "config.json").read_text())
        assert copied_config["custom_key"] is True
        assert copied_config["llm"]["baseUrl"] == "http://custom"

    def test_duplicate_copies_soul(self, bot_env, monkeypatch):
        self._mock_launch(monkeypatch)
        bots_dir = bot_env["bots_dir"]
        _create_test_bot(bots_dir, "original", soul="unique soul content")
        write_meta("original", {"created_at": "x", "modified_at": "x", "forked_from": None, "backups": []})

        duplicate_bot("original", "copy")
        assert (bots_dir / "copy" / "SOUL.md").read_text() == "unique soul content"

    def test_duplicate_does_not_track_lineage(self, bot_env, monkeypatch):
        self._mock_launch(monkeypatch)
        bots_dir = bot_env["bots_dir"]
        _create_test_bot(bots_dir, "original")
        write_meta("original", {"created_at": "x", "modified_at": "x", "forked_from": None, "backups": []})

        duplicate_bot("original", "copy")
        meta = read_meta("copy")
        assert meta["forked_from"] is None

    def test_duplicate_target_exists_raises(self, bot_env, monkeypatch):
        self._mock_launch(monkeypatch)
        bots_dir = bot_env["bots_dir"]
        _create_test_bot(bots_dir, "original")
        _create_test_bot(bots_dir, "existing")

        with pytest.raises(FileExistsError):
            duplicate_bot("original", "existing")

    def test_duplicate_source_missing_raises(self, bot_env, monkeypatch):
        self._mock_launch(monkeypatch)
        with pytest.raises(FileNotFoundError):
            duplicate_bot("ghost", "copy")


class TestFork:
    def _mock_launch(self, monkeypatch):
        def fake_launch(name, bot_dir, **kwargs):
            return {"name": name, "status": "created", "port": 3001, "container_name": f"openclaw-bot-{name}"}
        monkeypatch.setattr("bots._launch_container", fake_launch)

    def test_fork_copies_config_and_soul(self, bot_env, monkeypatch):
        self._mock_launch(monkeypatch)
        bots_dir = bot_env["bots_dir"]
        _create_test_bot(bots_dir, "parent", config={"key": "val"}, soul="parent soul")
        write_meta("parent", {"created_at": "x", "modified_at": "x", "forked_from": None, "backups": []})

        fork_bot("parent", "child")

        assert json.loads((bots_dir / "child" / "config.json").read_text())["key"] == "val"
        assert (bots_dir / "child" / "SOUL.md").read_text() == "parent soul"

    def test_fork_records_lineage(self, bot_env, monkeypatch):
        self._mock_launch(monkeypatch)
        bots_dir = bot_env["bots_dir"]
        _create_test_bot(bots_dir, "parent")
        write_meta("parent", {"created_at": "x", "modified_at": "x", "forked_from": None, "backups": []})

        result = fork_bot("parent", "child")
        assert result["forked_from"] == "parent"

        meta = read_meta("child")
        assert meta["forked_from"] == "parent"

    def test_fork_target_exists_raises(self, bot_env, monkeypatch):
        self._mock_launch(monkeypatch)
        bots_dir = bot_env["bots_dir"]
        _create_test_bot(bots_dir, "parent")
        _create_test_bot(bots_dir, "existing")

        with pytest.raises(FileExistsError):
            fork_bot("parent", "existing")

    def test_fork_chain_preserves_immediate_parent(self, bot_env, monkeypatch):
        self._mock_launch(monkeypatch)
        bots_dir = bot_env["bots_dir"]
        _create_test_bot(bots_dir, "grandparent")
        write_meta("grandparent", {"created_at": "x", "modified_at": "x", "forked_from": None, "backups": []})

        fork_bot("grandparent", "parent")
        fork_bot("parent", "child")

        child_meta = read_meta("child")
        assert child_meta["forked_from"] == "parent"  # Not grandparent


class TestWorkspaceCopy:
    def _mock_launch(self, monkeypatch):
        def fake_launch(name, bot_dir, **kwargs):
            return {"name": name, "status": "created", "port": 3001, "container_name": f"openclaw-bot-{name}"}
        monkeypatch.setattr("bots._launch_container", fake_launch)

    def test_duplicate_copies_workspace(self, bot_env, monkeypatch):
        self._mock_launch(monkeypatch)
        bots_dir = bot_env["bots_dir"]
        src = _create_test_bot(bots_dir, "original", soul="pirate soul")
        write_meta("original", {"created_at": "x", "modified_at": "x", "forked_from": None, "backups": []})
        ws = src / ".openclaw" / "workspace"
        ws.mkdir(parents=True)
        (ws / "SOUL.md").write_text("pirate soul")
        (ws / "MEMORY.md").write_text("knows treasure locations")

        duplicate_bot("original", "copy")

        dst_ws = bots_dir / "copy" / ".openclaw" / "workspace"
        assert (dst_ws / "SOUL.md").read_text() == "pirate soul"
        assert (dst_ws / "MEMORY.md").read_text() == "knows treasure locations"

    def test_fork_copies_workspace(self, bot_env, monkeypatch):
        self._mock_launch(monkeypatch)
        bots_dir = bot_env["bots_dir"]
        src = _create_test_bot(bots_dir, "parent", soul="parent soul")
        write_meta("parent", {"created_at": "x", "modified_at": "x", "forked_from": None, "backups": []})
        ws = src / ".openclaw" / "workspace"
        ws.mkdir(parents=True)
        (ws / "SOUL.md").write_text("parent soul")
        (ws / "IDENTITY.md").write_text("I am the parent")
        mem = ws / "memory"
        mem.mkdir()
        (mem / "history.md").write_text("past events")

        fork_bot("parent", "child")

        dst_ws = bots_dir / "child" / ".openclaw" / "workspace"
        assert (dst_ws / "SOUL.md").read_text() == "parent soul"
        assert (dst_ws / "IDENTITY.md").read_text() == "I am the parent"
        assert (dst_ws / "memory" / "history.md").read_text() == "past events"

    def test_duplicate_without_workspace_still_works(self, bot_env, monkeypatch):
        """Duplicate works even when source has no .openclaw/workspace/."""
        self._mock_launch(monkeypatch)
        bots_dir = bot_env["bots_dir"]
        _create_test_bot(bots_dir, "original", soul="plain soul")
        write_meta("original", {"created_at": "x", "modified_at": "x", "forked_from": None, "backups": []})

        result = duplicate_bot("original", "copy")
        assert result["name"] == "copy"
        assert (bots_dir / "copy" / "SOUL.md").read_text() == "plain soul"


class TestCreateBotNameCollision:
    def test_create_bot_name_collision(self, bot_env, monkeypatch):
        """Test that creating a bot with an existing name raises ValueError."""
        bots_dir = bot_env["bots_dir"]
        # Create a bot directory to simulate existing bot
        (bots_dir / "existing-bot").mkdir()
        monkeypatch.setattr("docker_utils._get_client", lambda: MagicMock())
        with pytest.raises(ValueError, match="already exists"):
            create_bot("existing-bot")
