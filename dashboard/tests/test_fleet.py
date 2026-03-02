import copy
import json
import tarfile
import time
from unittest.mock import MagicMock

import pytest
from app import (
    SESSIONS,
    _bootstrap_admin,
    _build_tls_config,
    _check_login_rate,
    _cleanup_expired_sessions,
    _create_session,
    _effective_status,
    _get_session,
    _grant_bot_to_user,
    _hash_password,
    _invalidate_user_sessions,
    _load_users,
    _login_attempts,
    _login_lock,
    _record_failed_login,
    _redact_config,
    _resolve_template,
    _save_users,
    _sync_caddy_config,
    _user_can_access_bot,
    _verify_password,
    allocate_port,
    create_backup,
    create_bot,
    deep_merge,
    duplicate_bot,
    ensure_meta,
    fork_bot,
    generate_config,
    get_bot_cron_jobs,
    get_bot_storage,
    get_bot_token_usage,
    get_fleet_stats,
    list_backups,
    list_templates,
    prune_scheduled_backups,
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

    # New template structure: template_dir/default/openclaw.template.json
    default_tmpl = template_dir / "default"
    default_tmpl.mkdir()
    oc_template = {
        "models": {
            "mode": "merge",
            "providers": {
                "default": {
                    "baseUrl": "{{LLM_BASE_URL}}",
                    "apiKey": "{{LLM_API_KEY}}",
                    "api": "openai-completions",
                    "models": [{
                        "id": "{{LLM_MODEL}}",
                        "name": "{{LLM_MODEL}}",
                        "contextWindow": 128000,
                        "maxTokens": 8192,
                    }],
                }
            },
        },
        "agents": {"defaults": {"model": "default/{{LLM_MODEL}}"}},
    }
    (default_tmpl / "openclaw.template.json").write_text(json.dumps(oc_template))
    (default_tmpl / "SOUL.md").write_text("default soul")

    monkeypatch.setattr("app.TEMPLATE_DIR", template_dir)
    monkeypatch.setattr("app.BOTS_DIR", bots_dir)
    monkeypatch.setenv("LLM_BASE_URL", "http://10.0.0.1:8000/v1")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_CONTEXT_WINDOW", "128000")
    monkeypatch.setenv("LLM_MAX_TOKENS", "8192")

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
    def test_template_resolved_with_env_vars(self, bot_env):
        config = generate_config("test")
        provider = config["models"]["providers"]["default"]
        assert provider["baseUrl"] == "http://10.0.0.1:8000/v1"
        assert provider["apiKey"] == "test-key"
        assert config["agents"]["defaults"]["model"] == "default/test-model"

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
        config = generate_config("mybot", extra_config={"agents": {"defaults": {"model": "custom/model"}}})
        assert config["agents"]["defaults"]["model"] == "custom/model"
        # Original field preserved
        assert config["models"]["mode"] == "merge"

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
        monkeypatch.setattr("app.BACKUP_DIR", ext_dir)

        _create_test_bot(bots_dir, "mybot")
        write_meta("mybot", {"created_at": "x", "modified_at": "x", "forked_from": None, "backups": []})

        result = create_backup("mybot")
        ts = result["timestamp"]
        assert (ext_dir / "mybot" / f"{ts}.tar.gz").exists()
        # Should NOT be in the bot's own .backups/
        assert not (bots_dir / "mybot" / ".backups" / f"{ts}.tar.gz").exists()


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
        monkeypatch.setattr("app.BACKUP_DIR", ext_dir)

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


# ===========================================================================
# Full-state backup: .openclaw/ directory (tests 41–44)
# ===========================================================================
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


# ===========================================================================
# Full-state rollback: .openclaw/ restoration (tests 45–47)
# ===========================================================================
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


# ===========================================================================
# Workspace copy for duplicate/fork (tests 48–50)
# ===========================================================================
class TestWorkspaceCopy:
    def _mock_launch(self, monkeypatch):
        def fake_launch(name, bot_dir):
            return {"name": name, "status": "created", "port": 3001, "container_name": f"openclaw-bot-{name}"}
        monkeypatch.setattr("app._launch_container", fake_launch)

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


# ===========================================================================
# Storage & Cron
# ===========================================================================
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


# ===========================================================================
# Token Usage
# ===========================================================================
class TestTokenUsage:
    def test_token_usage_no_sessions(self, bot_env):
        bots_dir = bot_env["bots_dir"]
        _create_test_bot(bots_dir, "mybot")
        result = get_bot_token_usage("mybot")
        assert result["total_tokens"] == 0

    def test_token_usage_reads_sessions(self, bot_env):
        bots_dir = bot_env["bots_dir"]
        _create_test_bot(bots_dir, "mybot")
        sessions_dir = bots_dir / "mybot" / ".openclaw" / "agents" / "main" / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        sessions_data = {
            "session-1": {"inputTokens": 1000, "outputTokens": 200, "contextTokens": 262144},
            "session-2": {"inputTokens": 500, "outputTokens": 100, "contextTokens": 262144},
        }
        (sessions_dir / "sessions.json").write_text(json.dumps(sessions_data))
        result = get_bot_token_usage("mybot")
        assert result["input_tokens"] == 1500
        assert result["output_tokens"] == 300
        assert result["total_tokens"] == 1800
        assert result["context_tokens"] == 262144


# ===========================================================================
# Fleet Stats
# ===========================================================================
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
        monkeypatch.setattr("app._get_client", lambda: mock_client)

        result = get_fleet_stats()
        assert result["total_bots"] == 2
        assert result["running_bots"] == 1
        assert result["starting_bots"] == 0
        assert result["total_cpu_percent"] > 0
        assert result["total_memory_mb"] > 0
        assert result["total_storage_bytes"] > 0
        assert "total_tokens_used" in result

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
        monkeypatch.setattr("app._get_client", lambda: mock_client)

        result = get_fleet_stats()
        assert result["total_bots"] == 2
        assert result["running_bots"] == 1
        assert result["starting_bots"] == 1
        # Both contribute to resource stats
        assert result["total_cpu_percent"] > 0

    def test_fleet_stats_empty_fleet(self, bot_env, monkeypatch):
        mock_client = MagicMock()
        mock_client.containers.list.return_value = []
        monkeypatch.setattr("app._get_client", lambda: mock_client)

        result = get_fleet_stats()
        assert result["total_bots"] == 0
        assert result["running_bots"] == 0
        assert result["starting_bots"] == 0
        assert result["total_cpu_percent"] == 0
        assert result["total_storage_bytes"] == 0


# ===========================================================================
# Effective Status
# ===========================================================================
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


# ===========================================================================
# Backup Retention (pruning)
# ===========================================================================
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


# ===========================================================================
# Auth helpers — fixtures
# ===========================================================================
@pytest.fixture(autouse=False)
def auth_env(monkeypatch, tmp_path):
    """Auth test environment with temp users file and clean sessions."""
    bots_dir = tmp_path / "bots"
    bots_dir.mkdir(exist_ok=True)
    users_file = bots_dir / ".users.json"
    monkeypatch.setattr("app.BOTS_DIR", bots_dir)
    monkeypatch.setenv("USERS_FILE", str(users_file))
    monkeypatch.setattr("app.AUTH_DISABLED", False)
    monkeypatch.setattr("app.SESSION_TTL", 3600)
    monkeypatch.setattr("app.ADMIN_USER", "admin")
    SESSIONS.clear()
    with _login_lock:
        _login_attempts.clear()
    yield {"bots_dir": bots_dir, "users_file": users_file}
    SESSIONS.clear()
    with _login_lock:
        _login_attempts.clear()


# ===========================================================================
# Password Hashing (tests 70–71)
# ===========================================================================
class TestPasswordHashing:
    def test_hash_and_verify(self, auth_env):
        h = _hash_password("secret123")
        assert h != "secret123"
        assert _verify_password("secret123", h) is True

    def test_wrong_password_fails(self, auth_env):
        h = _hash_password("correct")
        assert _verify_password("wrong", h) is False


# ===========================================================================
# Session Lifecycle (tests 72–76)
# ===========================================================================
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
        monkeypatch.setattr("app.SESSION_TTL", 1)
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
        monkeypatch.setattr("app.SESSION_TTL", 1)
        self._make_user(auth_env, "alice")
        _create_session("alice")
        _create_session("alice")
        time.sleep(1.1)
        removed = _cleanup_expired_sessions()
        assert removed == 2
        assert len(SESSIONS) == 0


# ===========================================================================
# RBAC Checks (tests 77–81)
# ===========================================================================
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


# ===========================================================================
# User Bootstrap (tests 82–84)
# ===========================================================================
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


# ===========================================================================
# Session Invalidation (tests 85–86)
# ===========================================================================
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


# ===========================================================================
# Integration tests (FastAPI TestClient) (tests 87–98)
# ===========================================================================
class TestAuthAPI:
    @pytest.fixture(autouse=True)
    def setup(self, auth_env, monkeypatch):
        """Set up test client and seed admin user."""
        from app import app
        from fastapi.testclient import TestClient
        monkeypatch.setenv("ADMIN_PASSWORD", "admin-pass")
        _bootstrap_admin()
        self.client = TestClient(app)
        self.auth_env = auth_env

    def _login_client(self, username="admin", password="admin-pass"):
        """Login and return a fresh client with the session cookie set."""
        from app import app
        from fastapi.testclient import TestClient
        c = TestClient(app)
        r = c.post("/api/auth/login", json={"username": username, "password": password})
        assert r.status_code == 200
        # Transfer the cookie to the client's cookie jar
        token = r.cookies.get("cfm_session")
        if token:
            c.cookies.set("cfm_session", token)
        return c

    def test_login_sets_cookie(self):
        r = self.client.post("/api/auth/login", json={"username": "admin", "password": "admin-pass"})
        assert r.status_code == 200
        assert "cfm_session" in r.cookies

    def test_login_wrong_password(self):
        r = self.client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
        assert r.status_code == 401

    def test_verify_returns_x_user(self):
        c = self._login_client()
        r = c.get("/api/auth/verify")
        assert r.status_code == 200
        assert r.headers.get("x-forwarded-user") == "admin"

    def test_verify_without_cookie(self):
        r = self.client.get("/api/auth/verify")
        assert r.status_code == 401

    def test_me_returns_user_info(self):
        c = self._login_client()
        r = c.get("/api/auth/me")
        assert r.status_code == 200
        data = r.json()
        assert data["username"] == "admin"
        assert data["role"] == "admin"

    def test_logout_clears_session(self):
        c = self._login_client()
        c.post("/api/auth/logout")
        r = c.get("/api/auth/me")
        assert r.status_code == 401

    def test_create_user(self):
        c = self._login_client()
        r = c.post("/api/auth/users", json={
            "username": "alice", "password": "alice-pass", "role": "user", "bots": ["bot-a"],
        })
        assert r.status_code == 200
        assert r.json()["username"] == "alice"

    def test_list_users(self):
        c = self._login_client()
        c.post("/api/auth/users", json={
            "username": "bob", "password": "bob-pass", "role": "user", "bots": [],
        })
        r = c.get("/api/auth/users")
        assert r.status_code == 200
        names = [u["username"] for u in r.json()]
        assert "admin" in names
        assert "bob" in names

    def test_update_user(self):
        c = self._login_client()
        c.post("/api/auth/users", json={
            "username": "carol", "password": "p", "role": "user", "bots": [],
        })
        r = c.put("/api/auth/users/carol", json={
            "role": "admin", "bots": ["*"],
        })
        assert r.status_code == 200
        assert r.json()["role"] == "admin"

    def test_delete_user(self):
        c = self._login_client()
        c.post("/api/auth/users", json={
            "username": "dave", "password": "p", "role": "user", "bots": [],
        })
        r = c.delete("/api/auth/users/dave")
        assert r.status_code == 200
        assert r.json()["deleted"] == "dave"

    def test_cannot_delete_self(self):
        c = self._login_client()
        r = c.delete("/api/auth/users/admin")
        assert r.status_code == 400

    def test_cannot_delete_last_admin(self):
        c = self._login_client()
        # Create another admin
        c.post("/api/auth/users", json={
            "username": "admin2", "password": "p", "role": "admin", "bots": ["*"],
        })
        # Login as admin2 and delete admin (OK — admin2 is still an admin)
        c2 = self._login_client("admin2", "p")
        r = c2.delete("/api/auth/users/admin")
        assert r.status_code == 200
        # Now try to delete admin2 (the last admin) — should fail (can't delete self)
        r2 = c2.delete("/api/auth/users/admin2")
        assert r2.status_code == 400

    def test_non_admin_cannot_list_users(self):
        c = self._login_client()
        c.post("/api/auth/users", json={
            "username": "eve", "password": "eve-pass", "role": "user", "bots": ["*"],
        })
        c2 = self._login_client("eve", "eve-pass")
        r = c2.get("/api/auth/users")
        assert r.status_code == 403

    def test_verify_with_bot_name_rbac(self):
        """Test that verify checks per-bot RBAC when X-Original-Bot is set."""
        c = self._login_client()
        c.post("/api/auth/users", json={
            "username": "limited", "password": "lp", "role": "user", "bots": ["bot-a"],
        })
        c2 = self._login_client("limited", "lp")
        # Access allowed bot
        r = c2.get("/api/auth/verify", headers={"X-Original-Bot": "bot-a"})
        assert r.status_code == 200
        # Access denied bot
        r2 = c2.get("/api/auth/verify", headers={"X-Original-Bot": "bot-b"})
        assert r2.status_code == 403


# ===========================================================================
# Creator tracking
# ===========================================================================
class TestCreatedBy:
    def test_created_by_stored_in_meta(self, bot_env):
        config = {"llm": {"baseUrl": "http://x", "model": "m"}, "gateway": {"port": 3000}}
        write_bot_files("test-bot", config, created_by="alice")
        meta = read_meta("test-bot")
        assert meta["created_by"] == "alice"

    def test_created_by_none_when_not_provided(self, bot_env):
        config = {"llm": {"baseUrl": "http://x", "model": "m"}, "gateway": {"port": 3000}}
        write_bot_files("test-bot", config)
        meta = read_meta("test-bot")
        assert meta.get("created_by") is None


# ===========================================================================
# Grant bot to user
# ===========================================================================
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


# ===========================================================================
# RBAC endpoint enforcement
# ===========================================================================
class TestRBACEndpoints:
    @pytest.fixture(autouse=True)
    def setup(self, auth_env, monkeypatch):
        from app import app
        from fastapi.testclient import TestClient
        monkeypatch.setenv("ADMIN_PASSWORD", "admin-pass")
        _bootstrap_admin()
        self.client = TestClient(app)

    def _login_client(self, username="admin", password="admin-pass"):
        from app import app
        from fastapi.testclient import TestClient
        c = TestClient(app)
        r = c.post("/api/auth/login", json={"username": username, "password": password})
        token = r.cookies.get("cfm_session")
        if token:
            c.cookies.set("cfm_session", token)
        return c

    def test_list_bots_requires_auth(self):
        r = self.client.get("/api/bots")
        assert r.status_code == 401

    def test_fleet_stats_requires_auth(self):
        r = self.client.get("/api/fleet/stats")
        assert r.status_code == 401

    def test_change_password(self):
        c = self._login_client()
        r = c.post("/api/auth/change-password", json={
            "current_password": "admin-pass",
            "new_password": "new-pass",
        })
        assert r.status_code == 200
        # Old password should fail
        r2 = self.client.post("/api/auth/login", json={"username": "admin", "password": "admin-pass"})
        assert r2.status_code == 401
        # New password should work
        r3 = self.client.post("/api/auth/login", json={"username": "admin", "password": "new-pass"})
        assert r3.status_code == 200

    def test_change_password_wrong_current(self):
        c = self._login_client()
        r = c.post("/api/auth/change-password", json={
            "current_password": "wrong",
            "new_password": "new-pass",
        })
        assert r.status_code == 401

    def test_change_password_empty(self):
        c = self._login_client()
        r = c.post("/api/auth/change-password", json={
            "current_password": "admin-pass",
            "new_password": "   ",
        })
        assert r.status_code == 400

    def test_role_validation_rejects_invalid(self):
        """Test that creating a user with an invalid role returns 422."""
        c = self._login_client()
        r = c.post("/api/auth/users", json={
            "username": "test", "password": "pass", "role": "superadmin",
        })
        assert r.status_code == 422

    def test_role_validation_accepts_valid(self):
        """Test that valid roles 'admin' and 'user' are accepted."""
        c = self._login_client()
        r1 = c.post("/api/auth/users", json={
            "username": "u1", "password": "pass", "role": "user",
        })
        assert r1.status_code == 200
        r2 = c.post("/api/auth/users", json={
            "username": "u2", "password": "pass", "role": "admin",
        })
        assert r2.status_code == 200

    def test_login_rate_limiting_endpoint(self):
        """Test that 5+ rapid failed logins return 429."""
        from app import app
        from fastapi.testclient import TestClient
        c = TestClient(app)
        for _ in range(5):
            c.post("/api/auth/login", json={"username": "nobody", "password": "wrong"})
        r = c.post("/api/auth/login", json={"username": "nobody", "password": "wrong"})
        assert r.status_code == 429

    def test_config_requires_auth(self):
        """Test that /api/config requires authentication."""
        r = self.client.get("/api/config")
        assert r.status_code == 401


# ===========================================================================
# Bot name collision guard
# ===========================================================================
class TestCreateBotNameCollision:
    def test_create_bot_name_collision(self, bot_env, monkeypatch):
        """Test that creating a bot with an existing name raises ValueError."""
        bots_dir = bot_env["bots_dir"]
        # Create a bot directory to simulate existing bot
        (bots_dir / "existing-bot").mkdir()
        monkeypatch.setattr("app._get_client", lambda: MagicMock())
        with pytest.raises(ValueError, match="already exists"):
            create_bot("existing-bot")


# ===========================================================================
# Template system
# ===========================================================================
class TestResolveTemplate:
    def test_basic_substitution(self, monkeypatch):
        monkeypatch.setenv("MY_VAR", "hello")
        assert _resolve_template("value: {{MY_VAR}}") == "value: hello"

    def test_unset_var_kept(self, monkeypatch):
        monkeypatch.delenv("NONEXISTENT_VAR", raising=False)
        assert _resolve_template("{{NONEXISTENT_VAR}}") == "{{NONEXISTENT_VAR}}"

    def test_multiple_vars(self, monkeypatch):
        monkeypatch.setenv("A", "1")
        monkeypatch.setenv("B", "2")
        assert _resolve_template("{{A}} and {{B}}") == "1 and 2"

    def test_numeric_unquoted(self, monkeypatch):
        monkeypatch.setenv("NUM", "42")
        result = _resolve_template('{"val": {{NUM}}}')
        parsed = json.loads(result)
        assert parsed["val"] == 42


class TestListTemplates:
    def test_lists_default_template(self, bot_env):
        templates = list_templates()
        names = [t["name"] for t in templates]
        assert "default" in names

    def test_lists_multiple_templates(self, bot_env):
        # Add a second template
        custom = bot_env["template_dir"] / "custom"
        custom.mkdir()
        (custom / "openclaw.template.json").write_text("{}")
        (custom / "SOUL.md").write_text("I am custom")
        templates = list_templates()
        names = [t["name"] for t in templates]
        assert "default" in names
        assert "custom" in names

    def test_soul_preview(self, bot_env):
        templates = list_templates()
        default = next(t for t in templates if t["name"] == "default")
        assert default["soul_preview"] == "default soul"

    def test_hidden_dirs_excluded(self, bot_env):
        (bot_env["template_dir"] / ".hidden").mkdir()
        templates = list_templates()
        names = [t["name"] for t in templates]
        assert ".hidden" not in names


class TestTemplateInCreateFlow:
    def test_generate_config_with_named_template(self, bot_env):
        # Create a custom template
        custom = bot_env["template_dir"] / "custom"
        custom.mkdir()
        custom_tmpl = {"models": {"providers": {"openai": {"apiKey": "{{OPENAI_API_KEY}}"}}}}
        (custom / "openclaw.template.json").write_text(json.dumps(custom_tmpl))
        (custom / "SOUL.md").write_text("custom soul")

        import os
        os.environ["OPENAI_API_KEY"] = "sk-test"
        try:
            config = generate_config("test", template="custom")
            assert config["models"]["providers"]["openai"]["apiKey"] == "sk-test"
        finally:
            del os.environ["OPENAI_API_KEY"]

    def test_fallback_to_default_on_missing_template(self, bot_env):
        config = generate_config("test", template="nonexistent")
        # Should fall back to default template
        assert "models" in config
        assert "default" in config["models"]["providers"]

    def test_template_soul_used_when_no_custom_soul(self, bot_env):
        custom = bot_env["template_dir"] / "custom"
        custom.mkdir()
        (custom / "openclaw.template.json").write_text("{}")
        (custom / "SOUL.md").write_text("custom soul here")

        config = generate_config("mybot", template="custom")
        write_bot_files("mybot", config, soul="", template="custom")
        soul_path = bot_env["bots_dir"] / "mybot" / "SOUL.md"
        assert soul_path.read_text() == "custom soul here"


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


# ===========================================================================
# TLS Mode Config
# ===========================================================================

class TestBuildTlsConfig:
    """Tests for _build_tls_config() TLS mode selection."""

    def test_internal_mode_default(self, monkeypatch):
        """Internal mode (default): Caddy auto-generates self-signed cert."""
        import app
        monkeypatch.setattr(app, "TLS_MODE", "internal")
        policies, tls_app, scheme = _build_tls_config()
        assert policies == [{}]  # empty policy activates TLS on listener
        assert scheme == "https"
        assert "automation" in tls_app
        policy = tls_app["automation"]["policies"][0]
        assert policy["issuers"][0]["module"] == "internal"
        assert policy["on_demand"] is True  # required for port-only listeners

    def test_custom_mode(self, monkeypatch):
        """Custom mode: load user-provided cert files."""
        import app
        monkeypatch.setattr(app, "TLS_MODE", "custom")
        policies, tls_app, scheme = _build_tls_config()
        assert len(policies) == 1
        assert policies[0]["certificate_selection"]["any_tag"] == ["cert0"]
        assert scheme == "https"
        load_files = tls_app["certificates"]["load_files"]
        assert load_files[0]["certificate"] == "/certs/cert.pem"
        assert load_files[0]["key"] == "/certs/key.pem"
        assert load_files[0]["tags"] == ["cert0"]

    def test_off_mode(self, monkeypatch):
        """Off mode: no TLS, plain HTTP."""
        import app
        monkeypatch.setattr(app, "TLS_MODE", "off")
        policies, tls_app, scheme = _build_tls_config()
        assert policies == []
        assert tls_app == {}
        assert scheme == "http"

    def test_acme_mode_with_domain(self, monkeypatch):
        """ACME mode: Let's Encrypt with domain."""
        import app
        monkeypatch.setattr(app, "TLS_MODE", "acme")
        monkeypatch.setattr(app, "DOMAIN", "farm.example.com")
        monkeypatch.setenv("ACME_EMAIL", "admin@example.com")
        policies, tls_app, scheme = _build_tls_config()
        assert policies == [{}]  # empty policy activates TLS on listener
        assert scheme == "https"
        policy = tls_app["automation"]["policies"][0]
        assert policy["subjects"] == ["farm.example.com"]
        assert policy["issuers"][0]["module"] == "acme"
        assert policy["issuers"][0]["email"] == "admin@example.com"

    def test_acme_mode_without_email(self, monkeypatch):
        """ACME mode without email: no email in issuer config."""
        import app
        monkeypatch.setattr(app, "TLS_MODE", "acme")
        monkeypatch.setattr(app, "DOMAIN", "farm.example.com")
        monkeypatch.delenv("ACME_EMAIL", raising=False)
        policies, tls_app, scheme = _build_tls_config()
        issuer = tls_app["automation"]["policies"][0]["issuers"][0]
        assert issuer == {"module": "acme"}

    def test_acme_mode_without_domain(self, monkeypatch):
        """ACME mode without domain: no subjects in policy."""
        import app
        monkeypatch.setattr(app, "TLS_MODE", "acme")
        monkeypatch.setattr(app, "DOMAIN", "")
        monkeypatch.delenv("ACME_EMAIL", raising=False)
        policies, tls_app, scheme = _build_tls_config()
        policy = tls_app["automation"]["policies"][0]
        assert "subjects" not in policy

    def test_unknown_mode_defaults_to_internal(self, monkeypatch):
        """Unknown TLS_MODE values fall back to internal."""
        import app
        monkeypatch.setattr(app, "TLS_MODE", "bogus")
        policies, tls_app, scheme = _build_tls_config()
        assert scheme == "https"
        policy = tls_app["automation"]["policies"][0]
        assert policy["issuers"][0]["module"] == "internal"
        assert policy["on_demand"] is True


# ===========================================================================
# TestSyncCaddyConfig — full Caddy JSON config integration tests
# ===========================================================================
class TestSyncCaddyConfig:
    """Tests for _sync_caddy_config() — validates the full Caddy JSON config
    pushed to Caddy's admin API under each TLS mode and auth state."""

    @pytest.fixture
    def caddy_env(self, monkeypatch):
        """Set up mocks for _sync_caddy_config: Docker client + requests.post.

        Returns a dict with:
        - captured: list that receives the JSON config posted to Caddy
        - mock_client: the mocked Docker client
        - add_bot(name): helper to add a running bot container to the mock
        """
        import app

        # Mock Docker client with empty container list by default
        mock_client = MagicMock()
        containers = []
        mock_client.containers.list.return_value = containers
        monkeypatch.setattr("app._get_client", lambda: mock_client)

        # Capture what gets POSTed to Caddy admin API
        # _sync_caddy_config does `import requests as _req` internally,
        # so we need to mock requests.post on the real module.
        import requests as _real_req
        captured = []
        _orig_post = _real_req.post

        def _capture_post(url, json=None, headers=None, timeout=None):
            captured.append(json)

        monkeypatch.setattr(_real_req, "post", _capture_post)

        # Enable compose mode (bot routes only added when HOST_BOTS_DIR is set)
        monkeypatch.setenv("HOST_BOTS_DIR", "/host/bots")

        # Defaults
        monkeypatch.setattr(app, "CADDY_ADMIN_URL", "http://caddy:2019")
        monkeypatch.setattr(app, "AUTH_DISABLED", False)
        monkeypatch.setattr(app, "PORTAL_URL", "")

        def add_bot(name):
            bot = MagicMock()
            bot.labels = {"openclaw.name": name, "openclaw.bot": "true"}
            containers.append(bot)

        return {"captured": captured, "mock_client": mock_client, "add_bot": add_bot}

    def test_internal_mode_no_bots(self, monkeypatch, caddy_env):
        """Internal mode with no bots: self-signed TLS + redirect server."""
        import app
        monkeypatch.setattr(app, "TLS_MODE", "internal")
        monkeypatch.setattr(app, "CADDY_PORT", 8443)

        _sync_caddy_config()

        assert len(caddy_env["captured"]) == 1
        config = caddy_env["captured"][0]

        # Admin API
        assert config["admin"]["listen"] == ":2019"

        # Main server listens on fixed internal port
        main = config["apps"]["http"]["servers"]["main"]
        assert main["listen"] == [":8080"]

        # TLS app: internal issuer with on_demand
        tls_app = config["apps"]["tls"]
        policy = tls_app["automation"]["policies"][0]
        assert policy["issuers"][0]["module"] == "internal"
        assert policy["on_demand"] is True

        # tls_connection_policies with empty policy activates TLS
        assert main["tls_connection_policies"] == [{}]

        # Redirect server exists (HTTP→HTTPS)
        redirect = config["apps"]["http"]["servers"]["redirect"]
        assert redirect["listen"] == [":80"]
        redir_loc = redirect["routes"][0]["handle"][0]["headers"]["Location"][0]
        assert "https://" in redir_loc
        assert ":8443" in redir_loc

    def test_internal_mode_with_bots(self, monkeypatch, caddy_env):
        """Internal mode with bots: bot routes inserted before catch-all."""
        import app
        monkeypatch.setattr(app, "TLS_MODE", "internal")
        monkeypatch.setattr(app, "CADDY_PORT", 8443)

        caddy_env["add_bot"]("alice")
        caddy_env["add_bot"]("bob")

        _sync_caddy_config()

        config = caddy_env["captured"][0]
        main = config["apps"]["http"]["servers"]["main"]
        routes = main["routes"]

        # Find bot routes by matching path patterns
        bot_routes = [r for r in routes if any(
            "/claw/" in p
            for m in r.get("match", [])
            for p in m.get("path", [])
        )]
        assert len(bot_routes) == 2

        bot_names_found = set()
        for r in bot_routes:
            for m in r["match"]:
                for p in m.get("path", []):
                    if p.startswith("/claw/"):
                        name = p.split("/")[2]
                        bot_names_found.add(name)
        assert bot_names_found == {"alice", "bob"}

        # Bot route handlers: forward_auth + set-cookie + strip_prefix + proxy
        for r in bot_routes:
            handlers = r["handle"]
            handler_types = [h["handler"] for h in handlers]
            # Auth enabled: forward_auth (reverse_proxy) + headers (cookie) + rewrite (strip) + reverse_proxy (bot)
            assert "reverse_proxy" in handler_types
            assert "rewrite" in handler_types
            assert "headers" in handler_types

        # Bot routes are before the catch-all (last route)
        last_route = routes[-1]
        # Last route should be the catch-all frontend route (no "match" or broad match)
        assert "match" not in last_route or last_route["match"] == []

    def test_off_mode_no_redirect_server(self, monkeypatch, caddy_env):
        """Off mode: plain HTTP, no TLS app, no redirect server."""
        import app
        monkeypatch.setattr(app, "TLS_MODE", "off")
        monkeypatch.setattr(app, "CADDY_PORT", 8443)

        _sync_caddy_config()

        config = caddy_env["captured"][0]
        servers = config["apps"]["http"]["servers"]

        # Only "main" server, no "redirect"
        assert "redirect" not in servers
        assert "main" in servers

        # No TLS app
        assert "tls" not in config["apps"]

        # Main server on fixed internal port (independent of CADDY_PORT)
        assert servers["main"]["listen"] == [":8080"]

    def test_custom_mode_tls_policies(self, monkeypatch, caddy_env):
        """Custom mode: cert file references in TLS config."""
        import app
        monkeypatch.setattr(app, "TLS_MODE", "custom")
        monkeypatch.setattr(app, "CADDY_PORT", 8443)

        _sync_caddy_config()

        config = caddy_env["captured"][0]
        main = config["apps"]["http"]["servers"]["main"]

        # tls_connection_policies with cert tag
        assert "tls_connection_policies" in main
        assert main["tls_connection_policies"][0]["certificate_selection"]["any_tag"] == ["cert0"]

        # TLS app with load_files
        tls_app = config["apps"]["tls"]
        load = tls_app["certificates"]["load_files"][0]
        assert load["certificate"] == "/certs/cert.pem"
        assert load["key"] == "/certs/key.pem"

        # Redirect server exists
        assert "redirect" in config["apps"]["http"]["servers"]

    def test_acme_mode_full_config(self, monkeypatch, caddy_env):
        """ACME mode: Let's Encrypt automation policy + domain subjects."""
        import app
        monkeypatch.setattr(app, "TLS_MODE", "acme")
        monkeypatch.setattr(app, "DOMAIN", "farm.example.com")
        monkeypatch.setenv("ACME_EMAIL", "admin@example.com")
        monkeypatch.setattr(app, "CADDY_PORT", 443)

        _sync_caddy_config()

        config = caddy_env["captured"][0]

        # TLS app has ACME automation
        tls_app = config["apps"]["tls"]
        policy = tls_app["automation"]["policies"][0]
        assert policy["issuers"][0]["module"] == "acme"
        assert policy["issuers"][0]["email"] == "admin@example.com"
        assert policy["subjects"] == ["farm.example.com"]

        # Main server on fixed internal port (CADDY_PORT=443 is external only)
        assert config["apps"]["http"]["servers"]["main"]["listen"] == [":8080"]

        # Redirect server (HTTPS enabled)
        assert "redirect" in config["apps"]["http"]["servers"]

    def test_auth_disabled_no_forward_auth(self, monkeypatch, caddy_env):
        """When AUTH_DISABLED, routes should NOT have forward_auth handlers."""
        import app
        monkeypatch.setattr(app, "TLS_MODE", "internal")
        monkeypatch.setattr(app, "AUTH_DISABLED", True)
        monkeypatch.setattr(app, "CADDY_PORT", 8443)

        caddy_env["add_bot"]("testbot")

        _sync_caddy_config()

        config = caddy_env["captured"][0]
        routes = config["apps"]["http"]["servers"]["main"]["routes"]

        # Auth disabled: simpler route structure (no forward_auth subrequests)
        # API route should be direct reverse_proxy, no auth check
        api_routes = [r for r in routes if any(
            "/api/*" in p for m in r.get("match", []) for p in m.get("path", [])
        )]
        assert len(api_routes) == 1
        # Should have exactly 1 handler (direct proxy), not 2 (auth + proxy)
        assert len(api_routes[0]["handle"]) == 1
        assert api_routes[0]["handle"][0]["handler"] == "reverse_proxy"

        # Bot route should have hardcoded X-Forwarded-User: dev
        bot_routes = [r for r in routes if any(
            "/claw/testbot" in p
            for m in r.get("match", [])
            for p in m.get("path", [])
        )]
        assert len(bot_routes) == 1
        handlers = bot_routes[0]["handle"]
        # First handler sets X-Forwarded-User to "dev"
        fwd_handler = handlers[0]
        assert fwd_handler["handler"] == "headers"
        assert fwd_handler["request"]["set"]["X-Forwarded-User"] == ["dev"]

    def test_bot_route_websocket_cookie_routing(self, monkeypatch, caddy_env):
        """Bot routes include WebSocket cookie matcher for root / connections."""
        import app
        monkeypatch.setattr(app, "TLS_MODE", "internal")
        monkeypatch.setattr(app, "AUTH_DISABLED", True)  # simpler to inspect
        monkeypatch.setattr(app, "CADDY_PORT", 8443)

        caddy_env["add_bot"]("mybot")

        _sync_caddy_config()

        config = caddy_env["captured"][0]
        routes = config["apps"]["http"]["servers"]["main"]["routes"]

        bot_routes = [r for r in routes if any(
            "/claw/mybot" in p
            for m in r.get("match", [])
            for p in m.get("path", [])
        )]
        assert len(bot_routes) == 1
        match_clauses = bot_routes[0]["match"]

        # Should have 2 match clauses: path match and WS cookie match
        assert len(match_clauses) == 2

        # First: path match
        assert "/claw/mybot/*" in match_clauses[0]["path"]
        assert "/claw/mybot" in match_clauses[0]["path"]

        # Second: root WS with cookie
        ws_match = match_clauses[1]
        assert ws_match["path"] == ["/"]
        assert ws_match["header"]["Upgrade"] == ["websocket"]
        # Cookie header_regexp key must be the header name "Cookie"
        assert "Cookie" in ws_match["header_regexp"]
        assert ws_match["header_regexp"]["Cookie"]["name"] == "cfm_bot"
        assert "mybot" in ws_match["header_regexp"]["Cookie"]["pattern"]

    def test_portal_url_in_redirect(self, monkeypatch, caddy_env):
        """When PORTAL_URL is set, redirect and login URLs use it."""
        import app
        monkeypatch.setattr(app, "TLS_MODE", "internal")
        monkeypatch.setattr(app, "PORTAL_URL", "https://farm.example.com")
        monkeypatch.setattr(app, "CADDY_PORT", 8443)

        _sync_caddy_config()

        config = caddy_env["captured"][0]

        # Redirect server uses PORTAL_URL
        redirect = config["apps"]["http"]["servers"]["redirect"]
        redir_loc = redirect["routes"][0]["handle"][0]["headers"]["Location"][0]
        # PORTAL_URL is used directly (already includes port if needed)
        assert redir_loc.startswith("https://farm.example.com")

    def test_caddy_unreachable_fails_silently(self, monkeypatch):
        """When Caddy admin API is unreachable, _sync_caddy_config doesn't raise."""
        import app
        import requests as _real_req

        mock_client = MagicMock()
        mock_client.containers.list.return_value = []
        monkeypatch.setattr("app._get_client", lambda: mock_client)
        monkeypatch.setenv("HOST_BOTS_DIR", "/host/bots")
        monkeypatch.setattr(app, "TLS_MODE", "internal")
        monkeypatch.setattr(app, "AUTH_DISABLED", False)
        monkeypatch.setattr(app, "PORTAL_URL", "")
        monkeypatch.setattr(app, "CADDY_ADMIN_URL", "http://localhost:99999")

        # Mock requests.post to raise ConnectionError
        def _failing_post(*args, **kwargs):
            raise ConnectionError("Caddy not reachable")
        monkeypatch.setattr(_real_req, "post", _failing_post)

        # Should not raise — fails silently
        _sync_caddy_config()

    def test_security_headers_present(self, monkeypatch, caddy_env):
        """First route is a matchless security headers route with correct headers."""
        import app
        monkeypatch.setattr(app, "TLS_MODE", "internal")
        monkeypatch.setattr(app, "CADDY_PORT", 8443)

        _sync_caddy_config()

        config = caddy_env["captured"][0]
        routes = config["apps"]["http"]["servers"]["main"]["routes"]
        first = routes[0]

        # No matcher — applies to all requests (non-terminal middleware)
        assert "match" not in first

        headers_handler = first["handle"][0]
        assert headers_handler["handler"] == "headers"
        hdr = headers_handler["response"]["set"]
        assert hdr["X-Content-Type-Options"] == ["nosniff"]
        assert hdr["X-Frame-Options"] == ["SAMEORIGIN"]
        assert hdr["Referrer-Policy"] == ["strict-origin-when-cross-origin"]
        assert hdr["Permissions-Policy"] == ["camera=(), microphone=(), geolocation=()"]
        # HSTS present when TLS is enabled
        assert "Strict-Transport-Security" in hdr
        assert "max-age=63072000" in hdr["Strict-Transport-Security"][0]

    def test_security_headers_no_hsts_when_tls_off(self, monkeypatch, caddy_env):
        """No HSTS header when TLS_MODE=off."""
        import app
        monkeypatch.setattr(app, "TLS_MODE", "off")
        monkeypatch.setattr(app, "CADDY_PORT", 8443)

        _sync_caddy_config()

        config = caddy_env["captured"][0]
        routes = config["apps"]["http"]["servers"]["main"]["routes"]
        first = routes[0]
        hdr = first["handle"][0]["response"]["set"]
        assert "Strict-Transport-Security" not in hdr
        # Other headers still present
        assert hdr["X-Content-Type-Options"] == ["nosniff"]

    def test_x_frame_options_sameorigin_global(self, monkeypatch, caddy_env):
        """X-Frame-Options is SAMEORIGIN globally (allows bot Control UI iframes)."""
        import app
        monkeypatch.setattr(app, "TLS_MODE", "internal")
        monkeypatch.setattr(app, "AUTH_DISABLED", True)
        monkeypatch.setattr(app, "CADDY_PORT", 8443)

        caddy_env["add_bot"]("testbot")

        _sync_caddy_config()

        config = caddy_env["captured"][0]
        routes = config["apps"]["http"]["servers"]["main"]["routes"]
        first = routes[0]

        # Global security headers use SAMEORIGIN (not DENY) because the
        # dashboard iframes bot Control UI at the same origin.
        assert "match" not in first
        hdr = first["handle"][0]["response"]["set"]
        assert hdr["X-Frame-Options"] == ["SAMEORIGIN"]

    def test_health_in_public_routes(self, monkeypatch, caddy_env):
        """Health endpoint is in the public (unauthenticated) routes."""
        import app
        monkeypatch.setattr(app, "TLS_MODE", "internal")
        monkeypatch.setattr(app, "CADDY_PORT", 8443)

        _sync_caddy_config()

        config = caddy_env["captured"][0]
        routes = config["apps"]["http"]["servers"]["main"]["routes"]

        # Find the public auth routes (match includes /api/auth/login)
        public_routes = [r for r in routes if any(
            "/api/auth/login" in p
            for m in r.get("match", [])
            for p in m.get("path", [])
        )]
        assert len(public_routes) == 1
        paths = public_routes[0]["match"][0]["path"]
        assert "/api/health" in paths


# ===========================================================================
# Health Endpoint
# ===========================================================================
class TestHealthEndpoint:
    @pytest.fixture(autouse=True)
    def setup(self, auth_env, monkeypatch):
        from app import app
        from fastapi.testclient import TestClient
        monkeypatch.setenv("ADMIN_PASSWORD", "admin-pass")
        _bootstrap_admin()
        self.client = TestClient(app)

    def test_health_returns_200_without_auth(self):
        """Health endpoint works without authentication."""
        r = self.client.get("/api/health")
        assert r.status_code == 200
        assert r.json() == {"ok": True}


# ===========================================================================
# Session Cookie Secure Flag
# ===========================================================================
class TestSessionCookieSecureFlag:
    @pytest.fixture(autouse=True)
    def setup(self, auth_env, monkeypatch):
        from app import app
        from fastapi.testclient import TestClient
        monkeypatch.setenv("ADMIN_PASSWORD", "admin-pass")
        _bootstrap_admin()
        self.client = TestClient(app)
        self.monkeypatch = monkeypatch

    def _login_and_get_cookie_header(self):
        r = self.client.post("/api/auth/login", json={"username": "admin", "password": "admin-pass"})
        assert r.status_code == 200
        return r.headers.get("set-cookie", "")

    def test_compose_tls_on_sets_secure(self):
        """Compose mode + TLS enabled → Secure flag set."""
        import app
        self.monkeypatch.setenv("HOST_BOTS_DIR", "/host/bots")
        self.monkeypatch.setattr(app, "TLS_MODE", "internal")
        self.monkeypatch.setattr(app, "PORTAL_URL", "")
        cookie = self._login_and_get_cookie_header()
        assert "secure" in cookie.lower()

    def test_compose_tls_off_with_https_portal_sets_secure(self):
        """Compose mode + TLS off + PORTAL_URL=https → Secure flag set."""
        import app
        self.monkeypatch.setenv("HOST_BOTS_DIR", "/host/bots")
        self.monkeypatch.setattr(app, "TLS_MODE", "off")
        self.monkeypatch.setattr(app, "PORTAL_URL", "https://farm.example.com")
        cookie = self._login_and_get_cookie_header()
        assert "secure" in cookie.lower()

    def test_compose_tls_off_without_https_portal_no_secure(self):
        """Compose mode + TLS off + no HTTPS PORTAL_URL → no Secure flag."""
        import app
        self.monkeypatch.setenv("HOST_BOTS_DIR", "/host/bots")
        self.monkeypatch.setattr(app, "TLS_MODE", "off")
        self.monkeypatch.setattr(app, "PORTAL_URL", "")
        cookie = self._login_and_get_cookie_header()
        assert "secure" not in cookie.lower()

    def test_dev_mode_no_secure(self):
        """Dev mode (no HOST_BOTS_DIR) → no Secure flag regardless of TLS mode."""
        import app
        self.monkeypatch.delenv("HOST_BOTS_DIR", raising=False)
        self.monkeypatch.setattr(app, "TLS_MODE", "internal")
        self.monkeypatch.setattr(app, "PORTAL_URL", "")
        cookie = self._login_and_get_cookie_header()
        assert "secure" not in cookie.lower()


# ===========================================================================
# API Integration Tests — Bot Lifecycle (start/stop/restart/delete/logs)
# ===========================================================================
class TestBotLifecycleAPI:
    """Integration tests for bot lifecycle endpoints via HTTP."""

    @pytest.fixture(autouse=True)
    def setup(self, bot_env, auth_env, monkeypatch):
        import app as _app
        import docker as _docker
        from fastapi.testclient import TestClient
        monkeypatch.setattr(_app, "AUTH_DISABLED", True)
        self.mock_client = MagicMock()
        monkeypatch.setattr("app._get_client", lambda: self.mock_client)
        monkeypatch.setattr("app._sync_caddy_config_async", lambda: None)
        monkeypatch.setattr("app._sync_caddy_config", lambda: None)
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


# ===========================================================================
# API Integration Tests — Filesystem read endpoints (meta/detail/stats)
# ===========================================================================
class TestFilesystemReadAPI:
    """Integration tests for meta, detail, stats endpoints."""

    @pytest.fixture(autouse=True)
    def setup(self, bot_env, auth_env, monkeypatch):
        import app as _app
        import docker as _docker
        from fastapi.testclient import TestClient
        monkeypatch.setattr(_app, "AUTH_DISABLED", True)
        self.mock_client = MagicMock()
        monkeypatch.setattr("app._get_client", lambda: self.mock_client)
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


# ===========================================================================
# API Integration Tests — Create, Duplicate, Fork, Backup, Rollback
# ===========================================================================
class TestCRUDEndpointsAPI:
    """Integration tests for create/duplicate/fork/backup/rollback via HTTP."""

    @pytest.fixture(autouse=True)
    def setup(self, bot_env, auth_env, monkeypatch):
        import app as _app
        import docker as _docker
        from fastapi.testclient import TestClient
        monkeypatch.setattr(_app, "AUTH_DISABLED", True)
        self.mock_client = MagicMock()
        monkeypatch.setattr("app._get_client", lambda: self.mock_client)
        monkeypatch.setattr("app._sync_caddy_config_async", lambda: None)
        monkeypatch.setattr("app._sync_caddy_config", lambda: None)
        # Mock _launch_container to avoid real Docker calls
        self._launched = []

        def fake_launch(name, bot_dir, **kwargs):
            self._launched.append(name)
            return {"name": name, "status": "running", "port": 3001}
        monkeypatch.setattr("app._launch_container", fake_launch)
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


# ===========================================================================
# API Integration Tests — Config, Fleet Stats, Bot List
# ===========================================================================
class TestFunctionalEndpointsAPI:
    """Functional tests for config, fleet stats, and bot list endpoints."""

    @pytest.fixture(autouse=True)
    def setup(self, bot_env, auth_env, monkeypatch):
        import app as _app
        from fastapi.testclient import TestClient
        monkeypatch.setattr(_app, "AUTH_DISABLED", True)
        self.mock_client = MagicMock()
        monkeypatch.setattr("app._get_client", lambda: self.mock_client)
        self.client = TestClient(_app.app)
        self.bot_env = bot_env
        self._app = _app
        self.monkeypatch = monkeypatch

    def test_config_returns_settings(self):
        import app
        self.monkeypatch.setattr(app, "PORTAL_URL", "https://farm.example.com")
        self.monkeypatch.setattr(app, "CADDY_PORT", 8443)
        self.monkeypatch.setattr(app, "TLS_MODE", "internal")
        r = self.client.get("/api/config")
        assert r.status_code == 200
        data = r.json()
        assert data["portal_url"] == "https://farm.example.com"
        assert data["caddy_port"] == 8443
        assert data["tls_mode"] == "internal"

    def test_config_null_portal(self):
        import app
        self.monkeypatch.setattr(app, "PORTAL_URL", "")
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
        mc.attrs = {"State": {"Health": {"Status": "healthy"}}}
        self.mock_client.containers.list.return_value = [mc]
        r = self.client.get("/api/bots")
        assert r.status_code == 200
        bots = r.json()
        assert len(bots) == 1
        assert bots[0]["name"] == "test"
        assert bots[0]["status"] == "running"

    def test_list_bots_rbac_filtering(self):
        """Non-admin users only see bots they have access to."""
        import app
        self.monkeypatch.setattr(app, "AUTH_DISABLED", False)
        # Set up auth with a limited user
        from app import _bootstrap_admin, _hash_password, _load_users, _save_users
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
            mc.labels = {"openclaw.name": name, "openclaw.port": "3001"}
            mc.attrs = {"State": {"Health": {"Status": "healthy"}}}
            bots.append(mc)
        self.mock_client.containers.list.return_value = bots
        r = self.client.get("/api/bots")
        assert r.status_code == 200
        names = [b["name"] for b in r.json()]
        assert "allowed-bot" in names
        assert "secret-bot" not in names
