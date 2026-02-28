import copy
import json
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app import (
    allocate_port,
    create_backup,
    deep_merge,
    duplicate_bot,
    ensure_meta,
    fork_bot,
    generate_config,
    list_backups,
    read_meta,
    rollback_to_backup,
    sanitize_name,
    write_bot_files,
    write_meta,
)


# ===========================================================================
# Helpers — set up template + bots dirs for filesystem tests
# ===========================================================================
@pytest.fixture
def bot_env(monkeypatch, tmp_path):
    """Standard test environment with template and bots dirs."""
    template_dir = tmp_path / "template"
    template_dir.mkdir()
    bots_dir = tmp_path / "bots"
    bots_dir.mkdir()

    template = {
        "gateway": {"port": 3000},
        "llm": {"provider": "", "baseUrl": "", "model": ""},
        "compaction": {"enabled": True, "maxMessages": 50},
    }
    (template_dir / "config.template.json").write_text(json.dumps(template))
    (template_dir / "SOUL.md").write_text("default soul")

    monkeypatch.setattr("app.TEMPLATE_DIR", template_dir)
    monkeypatch.setattr("app.BOTS_DIR", bots_dir)
    monkeypatch.setenv("LLM_BASE_URL", "http://10.0.0.1:8000/v1")
    monkeypatch.setenv("LLM_MODEL", "test-model")

    return {"template_dir": template_dir, "bots_dir": bots_dir}


def _create_test_bot(bots_dir, name, config=None, soul="test soul"):
    """Helper to create a bot directory with files for testing."""
    bot_dir = bots_dir / name
    bot_dir.mkdir(parents=True, exist_ok=True)
    cfg = config or {"llm": {"baseUrl": "http://x", "model": "m"}, "gateway": {"port": 3000}}
    (bot_dir / "config.json").write_text(json.dumps(cfg))
    (bot_dir / "SOUL.md").write_text(soul)
    return bot_dir


# ===========================================================================
# Name Sanitization (tests 1–5)
# ===========================================================================
class TestSanitizeName:
    def test_lowercase_and_strip_special(self):
        assert sanitize_name("My_Bot.Name!@#123") == "my-bot-name-123"

    def test_collapse_consecutive_hyphens(self):
        assert sanitize_name("bot---name") == "bot-name"

    def test_truncate_to_48(self):
        result = sanitize_name("a" * 100)
        assert len(result) == 48

    def test_path_traversal_rejected(self):
        result = sanitize_name("../etc/passwd")
        assert result == "etc-passwd"
        assert ".." not in result
        assert "/" not in result

    @pytest.mark.parametrize("name", ["!!!", "---", ""])
    def test_empty_raises(self, name):
        with pytest.raises(ValueError):
            sanitize_name(name)


# ===========================================================================
# Deep Merge (tests 6–8)
# ===========================================================================
class TestDeepMerge:
    def test_flat_key_override(self):
        result = deep_merge({"a": 1, "b": 2}, {"b": 3})
        assert result == {"a": 1, "b": 3}

    def test_nested_recursive_merge(self):
        base = {"x": {"a": 1, "b": 2}}
        override = {"x": {"b": 3, "c": 4}}
        result = deep_merge(base, override)
        assert result == {"x": {"a": 1, "b": 3, "c": 4}}

    def test_override_not_mutated(self):
        base = {"a": 1}
        override = {"b": {"nested": [1, 2]}}
        override_copy = copy.deepcopy(override)
        result = deep_merge(base, override)
        result["b"]["nested"].append(3)
        assert override == override_copy


# ===========================================================================
# Config Generation + File Writing (tests 9–13)
# ===========================================================================
class TestConfigGeneration:
    def test_llm_fields_populated(self, bot_env):
        config = generate_config("test")
        assert config["llm"]["baseUrl"] == "http://10.0.0.1:8000/v1"
        assert config["llm"]["model"] == "test-model"

    def test_soul_written_to_workspace(self, bot_env):
        config = generate_config("mybot")
        write_bot_files("mybot", config, soul="custom soul text")
        soul_path = bot_env["bots_dir"] / "mybot" / "SOUL.md"
        assert soul_path.exists()
        assert soul_path.read_text() == "custom soul text"

    def test_default_soul_when_blank(self, bot_env):
        config = generate_config("mybot")
        write_bot_files("mybot", config, soul="")
        soul_path = bot_env["bots_dir"] / "mybot" / "SOUL.md"
        assert soul_path.read_text() == "default soul"

    def test_extra_config_merges(self, bot_env):
        config = generate_config("mybot", extra_config={"compaction": {"maxMessages": 100}})
        assert config["compaction"]["maxMessages"] == 100
        assert config["compaction"]["enabled"] is True

    def test_workspace_dir_created(self, bot_env):
        config = generate_config("newbot")
        write_bot_files("newbot", config)
        bots_dir = bot_env["bots_dir"]
        assert (bots_dir / "newbot").is_dir()
        assert (bots_dir / "newbot" / "config.json").exists()
        assert (bots_dir / "newbot" / "SOUL.md").exists()


# ===========================================================================
# Port Allocation (tests 14–15)
# ===========================================================================
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
        monkeypatch.setattr("app._get_client", lambda: mock_client)

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
        monkeypatch.setattr("app._get_client", lambda: mock_client)

        with pytest.raises(RuntimeError):
            allocate_port()


# ===========================================================================
# Metadata (tests 16–20)
# ===========================================================================
class TestMetadata:
    def test_write_and_read_meta(self, bot_env):
        bots_dir = bot_env["bots_dir"]
        _create_test_bot(bots_dir, "testbot")
        meta = {"created_at": "2026-01-01T00:00:00Z", "forked_from": None, "backups": []}
        write_meta("testbot", meta)
        loaded = read_meta("testbot")
        assert loaded["created_at"] == "2026-01-01T00:00:00Z"
        assert loaded["forked_from"] is None

    def test_read_meta_missing_returns_empty(self, bot_env):
        assert read_meta("nonexistent") == {}

    def test_ensure_meta_creates_if_missing(self, bot_env):
        bots_dir = bot_env["bots_dir"]
        _create_test_bot(bots_dir, "oldbot")
        meta = ensure_meta("oldbot")
        assert "created_at" in meta
        assert meta["forked_from"] is None
        assert meta["backups"] == []
        assert (bots_dir / "oldbot" / ".meta.json").exists()

    def test_ensure_meta_preserves_existing(self, bot_env):
        bots_dir = bot_env["bots_dir"]
        _create_test_bot(bots_dir, "testbot")
        original = {"created_at": "2020-01-01T00:00:00Z", "forked_from": "parent",
                     "modified_at": "2020-01-01T00:00:00Z", "backups": []}
        write_meta("testbot", original)
        loaded = ensure_meta("testbot")
        assert loaded["forked_from"] == "parent"
        assert loaded["created_at"] == "2020-01-01T00:00:00Z"

    def test_write_bot_files_creates_meta(self, bot_env):
        config = generate_config("newbot")
        write_bot_files("newbot", config)
        bots_dir = bot_env["bots_dir"]
        assert (bots_dir / "newbot" / ".meta.json").exists()
        meta = json.loads((bots_dir / "newbot" / ".meta.json").read_text())
        assert "created_at" in meta
        assert meta["forked_from"] is None


# ===========================================================================
# Backup (tests 21–26)
# ===========================================================================
class TestBackup:
    def test_create_backup_copies_files(self, bot_env):
        bots_dir = bot_env["bots_dir"]
        _create_test_bot(bots_dir, "mybot")
        write_meta("mybot", {"created_at": "x", "modified_at": "x", "forked_from": None, "backups": []})

        result = create_backup("mybot")
        ts = result["timestamp"]
        backup_dir = bots_dir / "mybot" / ".backups" / ts
        assert (backup_dir / "config.json").exists()
        assert (backup_dir / "SOUL.md").exists()

    def test_create_backup_updates_meta(self, bot_env):
        bots_dir = bot_env["bots_dir"]
        _create_test_bot(bots_dir, "mybot")
        write_meta("mybot", {"created_at": "x", "modified_at": "x", "forked_from": None, "backups": []})

        create_backup("mybot")
        meta = read_meta("mybot")
        assert len(meta["backups"]) == 1
        assert meta["backups"][0]["label"] == "manual"

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


# ===========================================================================
# Rollback (tests 27–31)
# ===========================================================================
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


# ===========================================================================
# Duplicate (tests 32–36)
# ===========================================================================
class TestDuplicate:
    def _mock_launch(self, monkeypatch):
        """Mock _launch_container to avoid Docker."""
        def fake_launch(name, bot_dir):
            return {"name": name, "status": "created", "port": 3001, "container_name": f"openclaw-bot-{name}"}
        monkeypatch.setattr("app._launch_container", fake_launch)

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


# ===========================================================================
# Fork (tests 37–40)
# ===========================================================================
class TestFork:
    def _mock_launch(self, monkeypatch):
        def fake_launch(name, bot_dir):
            return {"name": name, "status": "created", "port": 3001, "container_name": f"openclaw-bot-{name}"}
        monkeypatch.setattr("app._launch_container", fake_launch)

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
