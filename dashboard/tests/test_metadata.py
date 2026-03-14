import json

from templates import generate_config, write_bot_files
from tests.helpers import _create_test_bot
from utils import ensure_meta, read_meta, write_meta


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
