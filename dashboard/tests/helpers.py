import json


def _create_test_bot(bots_dir, name, config=None, soul="test soul"):
    """Helper to create a bot directory with files for testing."""
    bot_dir = bots_dir / name
    bot_dir.mkdir(parents=True, exist_ok=True)
    cfg = config or {"llm": {"baseUrl": "http://x", "model": "m"}, "gateway": {"port": 3000}}
    (bot_dir / "config.json").write_text(json.dumps(cfg))
    (bot_dir / "SOUL.md").write_text(soul)
    return bot_dir
