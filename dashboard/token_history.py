import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import config
import docker_utils
from bots import get_bot_token_usage

# ---------------------------------------------------------------------------
# Token history — periodic snapshots for sparkline visualisation
# ---------------------------------------------------------------------------
_MAX_ENTRIES = 96  # 24 hours at 15-minute intervals
_FLEET_MAX_ENTRIES = 168  # 7 days at hourly intervals

_token_history_stop_event = threading.Event()


def _snapshot_file(name: str) -> Path:
    return config.BOTS_DIR / name / ".token_history.jsonl"


def _fleet_history_file() -> Path:
    return config.BOTS_DIR / ".fleet_token_history.jsonl"


def _read_jsonl(path: Path) -> list[dict]:
    """Read a JSONL file, skipping malformed lines."""
    if not path.exists():
        return []
    entries: list[dict] = []
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        return []
    return entries


def _read_history(name: str) -> list[dict]:
    """Read per-bot token history JSONL."""
    return _read_jsonl(_snapshot_file(name))


def _write_jsonl(path: Path, entries: list[dict], max_entries: int) -> None:
    """Write entries as JSONL, keeping only the last max_entries."""
    trimmed = entries[-max_entries:]
    try:
        path.write_text("".join(json.dumps(e, separators=(",", ":")) + "\n" for e in trimmed))
    except OSError:
        pass


def _write_history(name: str, entries: list[dict]) -> None:
    """Write per-bot token history JSONL."""
    _write_jsonl(_snapshot_file(name), entries, _MAX_ENTRIES)


def _snapshot_one_bot(name: str) -> dict | None:
    """Collect a single token usage snapshot for one bot. Returns the entry with bot name."""
    usage = get_bot_token_usage(name)
    current_in = usage["input_tokens"]
    current_out = usage["output_tokens"]
    current_total = usage["total_tokens"]

    history = _read_history(name)
    prev_cumulative_in = 0
    prev_cumulative_out = 0
    if history:
        last = history[-1]
        prev_cumulative_in = last.get("cum_in", 0)
        prev_cumulative_out = last.get("cum_out", 0)

    delta_in = max(0, current_in - prev_cumulative_in)
    delta_out = max(0, current_out - prev_cumulative_out)
    delta_total = delta_in + delta_out

    entry = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "in": delta_in,
        "out": delta_out,
        "total": delta_total,
        "model": usage.get("model"),
        "cumulative": current_total,
        "cum_in": current_in,
        "cum_out": current_out,
        "bot": name,
    }

    history.append(entry)
    _write_history(name, history)
    return entry


def _update_fleet_history(bot_entries: list[dict | None]) -> None:
    """Aggregate per-bot deltas into hourly fleet-level buckets.

    Each hourly bucket stores per-bot contributions keyed by bot name,
    enabling RBAC filtering at query time. Format:
    {"ts": "...", "bots": {"bot-a": {"model": "m1", "total": 150}, ...}}
    """
    now = datetime.now(timezone.utc)
    hour_key = now.strftime("%Y-%m-%dT%H:00:00Z")

    # Collect per-bot contributions
    bot_contributions: dict[str, dict] = {}
    for entry in bot_entries:
        if not entry or entry.get("total", 0) == 0:
            continue
        bot_name = entry.get("bot", "unknown")
        model = entry.get("model") or "unknown"
        total = entry["total"]
        if bot_name in bot_contributions:
            bot_contributions[bot_name]["total"] += total
            bot_contributions[bot_name]["model"] = model
        else:
            bot_contributions[bot_name] = {"model": model, "total": total}

    if not bot_contributions:
        return

    path = _fleet_history_file()
    fleet_history = _read_jsonl(path)

    # Update existing hour bucket or create new one
    if fleet_history and fleet_history[-1].get("ts") == hour_key:
        existing_bots = fleet_history[-1].get("bots", {})
        for bot_name, contrib in bot_contributions.items():
            if bot_name in existing_bots:
                existing_bots[bot_name]["total"] += contrib["total"]
                existing_bots[bot_name]["model"] = contrib["model"]
            else:
                existing_bots[bot_name] = contrib
        fleet_history[-1]["bots"] = existing_bots
    else:
        fleet_history.append({"ts": hour_key, "bots": bot_contributions})

    _write_jsonl(path, fleet_history, _FLEET_MAX_ENTRIES)


def collect_token_snapshots() -> None:
    """Collect token usage snapshots for all bots in parallel."""
    try:
        client = docker_utils._get_client()
        containers = client.containers.list(
            all=True, filters={"label": "openclaw.bot=true"}
        )
    except Exception:
        return

    names = [c.labels.get("openclaw.name", "") for c in containers if c.labels.get("openclaw.name")]
    if not names:
        return

    def _safe_snapshot(name: str) -> dict | None:
        try:
            return _snapshot_one_bot(name)
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=min(len(names), 8)) as pool:
        entries = list(pool.map(_safe_snapshot, names))

    # Aggregate into fleet-level hourly history
    try:
        _update_fleet_history(entries)
    except Exception:
        pass


def _token_history_scheduler() -> None:
    """Background loop: collect token snapshots at a fixed interval."""
    interval = config.TOKEN_HISTORY_INTERVAL
    if interval <= 0:
        return
    while not _token_history_stop_event.wait(interval):
        try:
            collect_token_snapshots()
        except Exception:
            pass


def get_sparkline_data(name: str) -> list[dict]:
    """Return sparkline-ready data: [{ts, total}, ...]."""
    return [{"ts": e["ts"], "total": e.get("total", 0)} for e in _read_history(name)]


def get_fleet_token_chart(allowed_bots: set[str] | None = None) -> list[dict]:
    """Return fleet-level hourly token usage by model for chart rendering.

    Args:
        allowed_bots: If set, only include contributions from these bots.
                      None means include all (admin).

    Returns: [{ts, models: {model_name: token_count}}, ...]
    """
    raw = _read_jsonl(_fleet_history_file())
    result = []
    for entry in raw:
        bots = entry.get("bots")
        if bots:
            # New format: per-bot contributions with RBAC support
            models: dict[str, int] = {}
            for bot_name, contrib in bots.items():
                if allowed_bots is not None and bot_name not in allowed_bots:
                    continue
                model = contrib.get("model", "unknown")
                models[model] = models.get(model, 0) + contrib.get("total", 0)
        else:
            # Old format: direct {models: {model_name: count}} (no RBAC)
            models = entry.get("models", {})
        if models:
            result.append({"ts": entry["ts"], "models": models})
    return result
