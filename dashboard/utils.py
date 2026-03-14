import copy
import json
import re
from datetime import datetime, timezone

import config


def sanitize_name(name: str) -> str:
    """Lowercase, replace non-alphanum with hyphens, collapse, strip, truncate 48."""
    s = name.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-{2,}", "-", s)
    s = s.strip("-")
    s = s[:48]
    if not s:
        raise ValueError(f"Invalid bot name: {name!r}")
    return s


def deep_merge(base: dict, override: dict) -> dict:
    """Recursive dict merge. Lists replace, deep copies, no mutation of inputs."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------
def read_meta(name: str) -> dict:
    """Load .meta.json for a bot, returning {} if missing."""
    meta_path = config.BOTS_DIR / name / ".meta.json"
    if meta_path.exists():
        with open(meta_path) as f:
            return json.load(f)
    return {}


def write_meta(name: str, meta: dict) -> None:
    """Write .meta.json for a bot."""
    meta_path = config.BOTS_DIR / name / ".meta.json"
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)


def ensure_meta(name: str) -> dict:
    """Load or create .meta.json with defaults (migration for existing bots)."""
    meta = read_meta(name)
    if meta:
        return meta
    bot_dir = config.BOTS_DIR / name
    if not bot_dir.exists():
        return {}
    # Derive created_at from config.json mtime if available
    config_path = bot_dir / "config.json"
    if config_path.exists():
        mtime = datetime.fromtimestamp(config_path.stat().st_mtime, tz=timezone.utc)
        created = mtime.strftime("%Y-%m-%dT%H:%M:%SZ")
    else:
        created = _now_iso()
    meta = {
        "created_at": created,
        "modified_at": created,
        "forked_from": None,
        "backups": [],
    }
    write_meta(name, meta)
    return meta
