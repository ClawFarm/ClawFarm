import copy
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app import allocate_port, deep_merge, generate_config, sanitize_name, write_bot_files


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
        # Mutating result should not affect override
        result["b"]["nested"].append(3)
        assert override == override_copy


# ===========================================================================
# Config Generation + File Writing (tests 9–13)
# ===========================================================================
class TestConfigGeneration:
    def test_llm_fields_populated(self, monkeypatch, tmp_path):
        template_dir = tmp_path / "template"
        template_dir.mkdir()
        bots_dir = tmp_path / "bots"
        bots_dir.mkdir()

        template = {
            "gateway": {"port": 3000},
            "llm": {"provider": "", "baseUrl": "", "model": ""},
            "channels": [],
        }
        (template_dir / "config.template.json").write_text(json.dumps(template))

        monkeypatch.setattr("app.TEMPLATE_DIR", template_dir)
        monkeypatch.setattr("app.BOTS_DIR", bots_dir)
        monkeypatch.setenv("LLM_BASE_URL", "http://10.0.0.1:8000/v1")
        monkeypatch.setenv("LLM_MODEL", "test-model")

        config = generate_config("test")
        assert config["llm"]["baseUrl"] == "http://10.0.0.1:8000/v1"
        assert config["llm"]["model"] == "test-model"

    def test_soul_written_to_workspace(self, monkeypatch, tmp_path):
        template_dir = tmp_path / "template"
        template_dir.mkdir()
        bots_dir = tmp_path / "bots"
        bots_dir.mkdir()

        template = {"llm": {"provider": "", "baseUrl": "", "model": ""}}
        (template_dir / "config.template.json").write_text(json.dumps(template))
        (template_dir / "SOUL.md").write_text("default soul")

        monkeypatch.setattr("app.TEMPLATE_DIR", template_dir)
        monkeypatch.setattr("app.BOTS_DIR", bots_dir)
        monkeypatch.setenv("LLM_BASE_URL", "http://x")
        monkeypatch.setenv("LLM_MODEL", "m")

        config = generate_config("mybot")
        write_bot_files("mybot", config, soul="custom soul text")

        soul_path = bots_dir / "mybot" / "SOUL.md"
        assert soul_path.exists()
        assert soul_path.read_text() == "custom soul text"

    def test_default_soul_when_blank(self, monkeypatch, tmp_path):
        template_dir = tmp_path / "template"
        template_dir.mkdir()
        bots_dir = tmp_path / "bots"
        bots_dir.mkdir()

        template = {"llm": {"provider": "", "baseUrl": "", "model": ""}}
        (template_dir / "config.template.json").write_text(json.dumps(template))
        (template_dir / "SOUL.md").write_text("default soul")

        monkeypatch.setattr("app.TEMPLATE_DIR", template_dir)
        monkeypatch.setattr("app.BOTS_DIR", bots_dir)
        monkeypatch.setenv("LLM_BASE_URL", "http://x")
        monkeypatch.setenv("LLM_MODEL", "m")

        config = generate_config("mybot")
        write_bot_files("mybot", config, soul="")

        soul_path = bots_dir / "mybot" / "SOUL.md"
        assert soul_path.read_text() == "default soul"

    def test_extra_config_merges(self, monkeypatch, tmp_path):
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

        monkeypatch.setattr("app.TEMPLATE_DIR", template_dir)
        monkeypatch.setattr("app.BOTS_DIR", bots_dir)
        monkeypatch.setenv("LLM_BASE_URL", "http://x")
        monkeypatch.setenv("LLM_MODEL", "m")

        config = generate_config("mybot", extra_config={"compaction": {"maxMessages": 100}})
        assert config["compaction"]["maxMessages"] == 100
        assert config["compaction"]["enabled"] is True

    def test_workspace_dir_created(self, monkeypatch, tmp_path):
        template_dir = tmp_path / "template"
        template_dir.mkdir()
        bots_dir = tmp_path / "bots"
        bots_dir.mkdir()

        template = {"llm": {"provider": "", "baseUrl": "", "model": ""}}
        (template_dir / "config.template.json").write_text(json.dumps(template))
        (template_dir / "SOUL.md").write_text("default")

        monkeypatch.setattr("app.TEMPLATE_DIR", template_dir)
        monkeypatch.setattr("app.BOTS_DIR", bots_dir)
        monkeypatch.setenv("LLM_BASE_URL", "http://x")
        monkeypatch.setenv("LLM_MODEL", "m")

        config = generate_config("newbot")
        write_bot_files("newbot", config)

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
