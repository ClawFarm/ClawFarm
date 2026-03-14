import json
import tarfile
import time

import pytest

from backup import create_backup, list_backups, prune_scheduled_backups
from tests.helpers import _create_test_bot
from utils import read_meta, write_meta


class TestBackup:
    def test_create_backup_creates_tar_gz(self, bot_env):
        bots_dir = bot_env["bots_dir"]
        _create_test_bot(bots_dir, "mybot")
        write_meta("mybot", {"created_at": "x", "modified_at": "x", "forked_from": None, "backups": []})

        result = create_backup("mybot")
        ts = result["timestamp"]
        tar_path = bots_dir / "mybot" / ".backups" / f"{ts}.tar.gz"
        assert tar_path.exists()
        with tarfile.open(tar_path, "r:gz") as tar:
            names = tar.getnames()
            assert "config.json" in names
            assert "SOUL.md" in names

    def test_create_backup_updates_meta(self, bot_env):
        bots_dir = bot_env["bots_dir"]
        _create_test_bot(bots_dir, "mybot")
        write_meta("mybot", {"created_at": "x", "modified_at": "x", "forked_from": None, "backups": []})

        create_backup("mybot")
        meta = read_meta("mybot")
        assert len(meta["backups"]) == 1
        assert meta["backups"][0]["label"] == "manual"

    def test_backup_size_in_metadata(self, bot_env):
        bots_dir = bot_env["bots_dir"]
        _create_test_bot(bots_dir, "mybot")
        write_meta("mybot", {"created_at": "x", "modified_at": "x", "forked_from": None, "backups": []})

        result = create_backup("mybot")
        assert "size_bytes" in result
        assert result["size_bytes"] > 0
        meta = read_meta("mybot")
        assert meta["backups"][0]["size_bytes"] > 0

    def test_list_backups_returns_entries(self, bot_env):
        bots_dir = bot_env["bots_dir"]
        _create_test_bot(bots_dir, "mybot")
        write_meta("mybot", {"created_at": "x", "modified_at": "x", "forked_from": None, "backups": []})

        create_backup("mybot")
        backups = list_backups("mybot")
        assert len(backups) == 1

    def test_list_backups_empty_when_none(self, bot_env):
        bots_dir = bot_env["bots_dir"]
        _create_test_bot(bots_dir, "mybot")
        write_meta("mybot", {"created_at": "x", "modified_at": "x", "forked_from": None, "backups": []})
        assert list_backups("mybot") == []

    def test_backup_nonexistent_bot_raises(self, bot_env):
        with pytest.raises(FileNotFoundError):
            create_backup("ghost")

    def test_multiple_backups_accumulate(self, bot_env):
        bots_dir = bot_env["bots_dir"]
        _create_test_bot(bots_dir, "mybot")
        write_meta("mybot", {"created_at": "x", "modified_at": "x", "forked_from": None, "backups": []})

        create_backup("mybot", label="first")
        time.sleep(1.1)  # Ensure different timestamp
        create_backup("mybot", label="second")
        backups = list_backups("mybot")
        assert len(backups) == 2
        assert backups[0]["label"] == "first"
        assert backups[1]["label"] == "second"

    def test_backup_to_external_dir(self, bot_env, monkeypatch):
        bots_dir = bot_env["bots_dir"]
        ext_dir = bot_env["bots_dir"].parent / "ext_backups"
        ext_dir.mkdir()
        monkeypatch.setattr("config.BACKUP_DIR", ext_dir)

        _create_test_bot(bots_dir, "mybot")
        write_meta("mybot", {"created_at": "x", "modified_at": "x", "forked_from": None, "backups": []})

        result = create_backup("mybot")
        ts = result["timestamp"]
        assert (ext_dir / "mybot" / f"{ts}.tar.gz").exists()
        # Should NOT be in the bot's own .backups/
        assert not (bots_dir / "mybot" / ".backups" / f"{ts}.tar.gz").exists()


class TestOpenClawStateBackup:
    def _setup_bot_with_openclaw(self, bots_dir, name):
        """Create a bot with a populated .openclaw/ directory."""
        bot_dir = _create_test_bot(bots_dir, name)
        write_meta(name, {"created_at": "x", "modified_at": "x", "forked_from": None, "backups": []})
        oc_dir = bot_dir / ".openclaw"
        oc_dir.mkdir()
        (oc_dir / "workspace").mkdir()
        (oc_dir / "workspace" / "SOUL.md").write_text("pirate soul")
        (oc_dir / "workspace" / "MEMORY.md").write_text("remembers the kraken")
        mem_dir = oc_dir / "workspace" / "memory"
        mem_dir.mkdir()
        (mem_dir / "facts.md").write_text("fact 1")
        (oc_dir / "openclaw.json").write_text(json.dumps({"gateway": {"auth": {"token": "abc123"}}}))
        agents_dir = oc_dir / "agents" / "main" / "sessions"
        agents_dir.mkdir(parents=True)
        (agents_dir / "sess1.json").write_text('{"id":"s1"}')
        return bot_dir

    def _tar_contents(self, bots_dir, name, ts):
        """Extract tar.gz to a temp dir and return the root path."""
        tar_path = bots_dir / name / ".backups" / f"{ts}.tar.gz"
        extract_dir = bots_dir / name / ".backups" / f"{ts}_extracted"
        extract_dir.mkdir()
        with tarfile.open(tar_path, "r:gz") as tar:
            tar.extractall(path=str(extract_dir), filter="data")
        return extract_dir

    def test_backup_includes_openclaw_workspace(self, bot_env):
        bots_dir = bot_env["bots_dir"]
        self._setup_bot_with_openclaw(bots_dir, "stateful")
        result = create_backup("stateful")
        ts = result["timestamp"]
        extracted = self._tar_contents(bots_dir, "stateful", ts)
        backup_oc = extracted / ".openclaw"
        assert (backup_oc / "workspace" / "SOUL.md").read_text() == "pirate soul"
        assert (backup_oc / "workspace" / "MEMORY.md").read_text() == "remembers the kraken"
        assert (backup_oc / "workspace" / "memory" / "facts.md").read_text() == "fact 1"

    def test_backup_includes_openclaw_sessions(self, bot_env):
        bots_dir = bot_env["bots_dir"]
        self._setup_bot_with_openclaw(bots_dir, "stateful")
        result = create_backup("stateful")
        ts = result["timestamp"]
        extracted = self._tar_contents(bots_dir, "stateful", ts)
        backup_oc = extracted / ".openclaw"
        assert (backup_oc / "agents" / "main" / "sessions" / "sess1.json").exists()

    def test_backup_excludes_temp_files(self, bot_env):
        bots_dir = bot_env["bots_dir"]
        bot_dir = self._setup_bot_with_openclaw(bots_dir, "stateful")
        (bot_dir / ".openclaw" / "openclaw.json.bak").write_text("backup")
        (bot_dir / ".openclaw" / "update-check.json").write_text("{}")
        result = create_backup("stateful")
        ts = result["timestamp"]
        extracted = self._tar_contents(bots_dir, "stateful", ts)
        backup_oc = extracted / ".openclaw"
        assert not (backup_oc / "openclaw.json.bak").exists()
        assert not (backup_oc / "update-check.json").exists()

    def test_backup_excludes_logs(self, bot_env):
        bots_dir = bot_env["bots_dir"]
        bot_dir = self._setup_bot_with_openclaw(bots_dir, "stateful")
        logs_dir = bot_dir / ".openclaw" / "logs"
        logs_dir.mkdir()
        (logs_dir / "output.log").write_text("big log file")
        result = create_backup("stateful")
        ts = result["timestamp"]
        extracted = self._tar_contents(bots_dir, "stateful", ts)
        backup_oc = extracted / ".openclaw"
        assert not (backup_oc / "logs").exists()


class TestBackupRetention:
    def test_prune_removes_oldest_scheduled(self, bot_env):
        bots_dir = bot_env["bots_dir"]
        _create_test_bot(bots_dir, "mybot")
        write_meta("mybot", {"created_at": "x", "modified_at": "x", "forked_from": None, "backups": []})

        # Create 5 scheduled backups
        for i in range(5):
            create_backup("mybot", label="scheduled")
            time.sleep(1.1)

        assert len(list_backups("mybot")) == 5
        pruned = prune_scheduled_backups("mybot", keep=2)
        assert pruned == 3
        remaining = list_backups("mybot")
        assert len(remaining) == 2
        # All remaining should be scheduled
        assert all(b["label"] == "scheduled" for b in remaining)

    def test_prune_preserves_manual_backups(self, bot_env):
        bots_dir = bot_env["bots_dir"]
        _create_test_bot(bots_dir, "mybot")
        write_meta("mybot", {"created_at": "x", "modified_at": "x", "forked_from": None, "backups": []})

        create_backup("mybot", label="manual")
        time.sleep(1.1)
        create_backup("mybot", label="scheduled")
        time.sleep(1.1)
        create_backup("mybot", label="scheduled")

        pruned = prune_scheduled_backups("mybot", keep=1)
        assert pruned == 1
        remaining = list_backups("mybot")
        labels = [b["label"] for b in remaining]
        assert "manual" in labels
        assert labels.count("scheduled") == 1

    def test_prune_noop_when_under_limit(self, bot_env):
        bots_dir = bot_env["bots_dir"]
        _create_test_bot(bots_dir, "mybot")
        write_meta("mybot", {"created_at": "x", "modified_at": "x", "forked_from": None, "backups": []})

        create_backup("mybot", label="scheduled")
        pruned = prune_scheduled_backups("mybot", keep=5)
        assert pruned == 0
        assert len(list_backups("mybot")) == 1

    def test_prune_deletes_tar_files(self, bot_env):
        bots_dir = bot_env["bots_dir"]
        _create_test_bot(bots_dir, "mybot")
        write_meta("mybot", {"created_at": "x", "modified_at": "x", "forked_from": None, "backups": []})

        r1 = create_backup("mybot", label="scheduled")
        time.sleep(1.1)
        create_backup("mybot", label="scheduled")

        tar1 = bots_dir / "mybot" / ".backups" / f"{r1['timestamp']}.tar.gz"
        assert tar1.exists()

        prune_scheduled_backups("mybot", keep=1)
        assert not tar1.exists()
