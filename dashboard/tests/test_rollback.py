import json
import time

import pytest

from backup import create_backup, rollback_to_backup
from tests.helpers import _create_test_bot
from utils import read_meta, write_meta


class TestRollback:
    def test_rollback_restores_files(self, bot_env):
        bots_dir = bot_env["bots_dir"]
        _create_test_bot(bots_dir, "mybot", config={"version": "v1"}, soul="soul v1")
        write_meta("mybot", {"created_at": "x", "modified_at": "x", "forked_from": None, "backups": []})

        result = create_backup("mybot")
        ts = result["timestamp"]

        # Overwrite current files
        (bots_dir / "mybot" / "config.json").write_text(json.dumps({"version": "v2"}))
        (bots_dir / "mybot" / "SOUL.md").write_text("soul v2")

        time.sleep(1.1)
        rollback_to_backup("mybot", ts)

        restored_config = json.loads((bots_dir / "mybot" / "config.json").read_text())
        assert restored_config["version"] == "v1"
        assert (bots_dir / "mybot" / "SOUL.md").read_text() == "soul v1"

    def test_rollback_auto_backups_current_state(self, bot_env):
        bots_dir = bot_env["bots_dir"]
        _create_test_bot(bots_dir, "mybot")
        write_meta("mybot", {"created_at": "x", "modified_at": "x", "forked_from": None, "backups": []})

        result = create_backup("mybot")
        ts = result["timestamp"]

        time.sleep(1.1)
        rollback_to_backup("mybot", ts)

        meta = read_meta("mybot")
        labels = [b["label"] for b in meta["backups"]]
        assert "pre-rollback" in labels

    def test_rollback_updates_modified_at(self, bot_env):
        bots_dir = bot_env["bots_dir"]
        _create_test_bot(bots_dir, "mybot")
        write_meta("mybot", {"created_at": "2020-01-01T00:00:00Z",
                              "modified_at": "2020-01-01T00:00:00Z",
                              "forked_from": None, "backups": []})

        result = create_backup("mybot")
        time.sleep(1.1)
        rollback_to_backup("mybot", result["timestamp"])

        meta = read_meta("mybot")
        assert meta["modified_at"] != "2020-01-01T00:00:00Z"

    def test_rollback_invalid_timestamp_raises(self, bot_env):
        bots_dir = bot_env["bots_dir"]
        _create_test_bot(bots_dir, "mybot")
        write_meta("mybot", {"created_at": "x", "modified_at": "x", "forked_from": None, "backups": []})

        with pytest.raises(ValueError):
            rollback_to_backup("mybot", "99999999T999999")

    def test_rollback_nonexistent_bot_raises(self, bot_env):
        with pytest.raises(FileNotFoundError):
            rollback_to_backup("ghost", "20260101T000000")

    def test_rollback_from_old_directory_backup(self, bot_env):
        """Backward compat: rollback works from old directory-based backups."""
        bots_dir = bot_env["bots_dir"]
        _create_test_bot(bots_dir, "mybot", config={"version": "v1"}, soul="soul v1")
        write_meta("mybot", {"created_at": "x", "modified_at": "x", "forked_from": None, "backups": []})

        # Manually create an old-style directory backup
        ts = "20250101T000000"
        backup_dir = bots_dir / "mybot" / ".backups" / ts
        backup_dir.mkdir(parents=True)
        (backup_dir / "config.json").write_text(json.dumps({"version": "v1"}))
        (backup_dir / "SOUL.md").write_text("soul v1")

        meta = read_meta("mybot")
        meta["backups"].append({"timestamp": ts, "created_at": "x", "label": "manual"})
        write_meta("mybot", meta)

        # Modify current state
        (bots_dir / "mybot" / "config.json").write_text(json.dumps({"version": "v2"}))

        rollback_to_backup("mybot", ts)

        restored = json.loads((bots_dir / "mybot" / "config.json").read_text())
        assert restored["version"] == "v1"

    def test_rollback_from_external_backup(self, bot_env, monkeypatch):
        """Rollback works when backup is in external BACKUP_DIR."""
        bots_dir = bot_env["bots_dir"]
        ext_dir = bots_dir.parent / "ext_backups"
        ext_dir.mkdir()
        monkeypatch.setattr("config.BACKUP_DIR", ext_dir)

        _create_test_bot(bots_dir, "mybot", config={"version": "v1"}, soul="soul v1")
        write_meta("mybot", {"created_at": "x", "modified_at": "x", "forked_from": None, "backups": []})

        result = create_backup("mybot")
        ts = result["timestamp"]

        # Modify current state
        (bots_dir / "mybot" / "config.json").write_text(json.dumps({"version": "v2"}))

        time.sleep(1.1)
        rollback_to_backup("mybot", ts)

        restored = json.loads((bots_dir / "mybot" / "config.json").read_text())
        assert restored["version"] == "v1"


class TestOpenClawStateRollback:
    def _setup_bot_with_openclaw(self, bots_dir, name, soul="original soul", memory="original memory"):
        bot_dir = _create_test_bot(bots_dir, name, soul=soul)
        write_meta(name, {"created_at": "x", "modified_at": "x", "forked_from": None, "backups": []})
        oc_dir = bot_dir / ".openclaw"
        oc_dir.mkdir()
        (oc_dir / "workspace").mkdir()
        (oc_dir / "workspace" / "SOUL.md").write_text(soul)
        (oc_dir / "workspace" / "MEMORY.md").write_text(memory)
        (oc_dir / "openclaw.json").write_text(json.dumps({
            "gateway": {"auth": {"token": "tok-original"}},
            "models": {"mode": "merge"},
        }))
        return bot_dir

    def test_rollback_restores_workspace(self, bot_env):
        bots_dir = bot_env["bots_dir"]
        self._setup_bot_with_openclaw(bots_dir, "mybot", soul="v1 soul", memory="v1 memory")
        result = create_backup("mybot")
        ts = result["timestamp"]

        # Modify the workspace
        ws = bots_dir / "mybot" / ".openclaw" / "workspace"
        (ws / "SOUL.md").write_text("v2 soul")
        (ws / "MEMORY.md").write_text("v2 memory")

        time.sleep(1.1)
        rollback_to_backup("mybot", ts)

        assert (ws / "SOUL.md").read_text() == "v1 soul"
        assert (ws / "MEMORY.md").read_text() == "v1 memory"

    def test_rollback_preserves_gateway_token(self, bot_env):
        bots_dir = bot_env["bots_dir"]
        self._setup_bot_with_openclaw(bots_dir, "mybot")
        result = create_backup("mybot")
        ts = result["timestamp"]

        # Change the gateway token (simulating a new token generated after backup)
        oc_json = bots_dir / "mybot" / ".openclaw" / "openclaw.json"
        cfg = json.loads(oc_json.read_text())
        cfg["gateway"]["auth"]["token"] = "tok-new"
        oc_json.write_text(json.dumps(cfg))

        time.sleep(1.1)
        rollback_to_backup("mybot", ts)

        restored = json.loads(oc_json.read_text())
        assert restored["gateway"]["auth"]["token"] == "tok-new"  # Preserved, NOT reverted

    def test_rollback_without_openclaw_still_works(self, bot_env):
        """Rollback should work even if the backup has no .openclaw/ dir."""
        bots_dir = bot_env["bots_dir"]
        _create_test_bot(bots_dir, "mybot", config={"version": "v1"}, soul="old soul")
        write_meta("mybot", {"created_at": "x", "modified_at": "x", "forked_from": None, "backups": []})

        result = create_backup("mybot")
        ts = result["timestamp"]

        (bots_dir / "mybot" / "config.json").write_text(json.dumps({"version": "v2"}))

        time.sleep(1.1)
        rollback_to_backup("mybot", ts)

        restored = json.loads((bots_dir / "mybot" / "config.json").read_text())
        assert restored["version"] == "v1"
