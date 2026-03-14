import json
import shutil
import tarfile
from pathlib import Path

import config
from utils import _now_iso, _now_timestamp, ensure_meta, write_meta


def _copy_openclaw_state(src_oc: Path, dst_oc: Path, *, exclude_logs: bool = False) -> None:
    """Copy .openclaw/ contents, optionally skipping logs and temp files."""
    if not src_oc.exists():
        return
    for item in src_oc.iterdir():
        name = item.name
        # Always skip: device tokens, temp backups, update checks
        if name in ("openclaw.json.bak", "update-check.json"):
            continue
        if exclude_logs and name == "logs":
            continue
        dst = dst_oc / name
        if item.is_dir():
            shutil.copytree(item, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(item, dst)


def _backup_base_dir(name: str) -> Path:
    """Return the directory where backups are stored for a bot."""
    if config.BACKUP_DIR:
        d = config.BACKUP_DIR / name
    else:
        d = config.BOTS_DIR / name / ".backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _tar_exclude_filter(tarinfo: tarfile.TarInfo) -> tarfile.TarInfo | None:
    """Filter for tarfile.add() — skip logs, temp files, .bak files."""
    parts = Path(tarinfo.name).parts
    # Skip logs directory inside .openclaw/
    if "logs" in parts:
        return None
    basename = Path(tarinfo.name).name
    if basename == "update-check.json":
        return None
    if ".bak" in basename:
        return None
    return tarinfo


def create_backup(name: str, label: str = "manual") -> dict:
    """Snapshot full agent state as a compressed tar.gz archive."""
    bot_dir = config.BOTS_DIR / name
    if not bot_dir.exists():
        raise FileNotFoundError(f"Bot directory not found: {name}")

    ts = _now_timestamp()
    backup_base = _backup_base_dir(name)
    tar_path = backup_base / f"{ts}.tar.gz"

    with tarfile.open(tar_path, "w:gz") as tar:
        # Add root-level bot files
        config_src = bot_dir / "config.json"
        soul_src = bot_dir / "SOUL.md"
        if config_src.exists():
            tar.add(str(config_src), arcname="config.json")
        if soul_src.exists():
            tar.add(str(soul_src), arcname="SOUL.md")

        # Add full .openclaw/ state with exclusion filter
        src_oc = bot_dir / ".openclaw"
        if src_oc.exists():
            tar.add(str(src_oc), arcname=".openclaw", filter=_tar_exclude_filter)

    size_bytes = tar_path.stat().st_size
    now = _now_iso()
    meta = ensure_meta(name)
    meta["backups"].append({
        "timestamp": ts, "created_at": now, "label": label,
        "size_bytes": size_bytes,
    })
    meta["modified_at"] = now
    write_meta(name, meta)

    return {"timestamp": ts, "created_at": now, "label": label, "size_bytes": size_bytes}


def list_backups(name: str) -> list[dict]:
    """Return backup list from .meta.json."""
    meta = ensure_meta(name)
    return meta.get("backups", [])


def _find_backup(name: str, timestamp: str) -> tuple[Path | None, str]:
    """Locate a backup — returns (path, kind) where kind is 'tar' or 'dir'.

    Checks both external BACKUP_DIR and in-bot .backups/ for compatibility.
    """
    # Check tar.gz in all possible locations
    for base in [config.BACKUP_DIR / name if config.BACKUP_DIR else None, config.BOTS_DIR / name / ".backups"]:
        if base is None:
            continue
        tar_path = base / f"{timestamp}.tar.gz"
        if tar_path.exists():
            return tar_path, "tar"
    # Backward compat: old directory-based backups
    dir_path = config.BOTS_DIR / name / ".backups" / timestamp
    if dir_path.is_dir():
        return dir_path, "dir"
    return None, ""


def _rollback_from_tar(tar_path: Path, bot_dir: Path) -> None:
    """Restore bot state from a tar.gz backup."""
    dst_oc = bot_dir / ".openclaw"

    # Preserve gateway auth token
    existing_token = ""
    oc_json = dst_oc / "openclaw.json"
    if oc_json.exists():
        try:
            cfg = json.loads(oc_json.read_text())
            existing_token = cfg.get("gateway", {}).get("auth", {}).get("token", "")
        except (json.JSONDecodeError, KeyError):
            pass

    # Clear workspace, agents, cron before extracting
    for subdir in ("workspace", "agents", "cron"):
        target = dst_oc / subdir
        if target.exists():
            shutil.rmtree(target)

    with tarfile.open(tar_path, "r:gz") as tar:
        tar.extractall(path=str(bot_dir), filter="data")

    # Re-inject gateway token
    if existing_token and oc_json.exists():
        try:
            cfg = json.loads(oc_json.read_text())
            cfg.setdefault("gateway", {}).setdefault("auth", {})["token"] = existing_token
            with open(oc_json, "w") as f:
                json.dump(cfg, f, indent=2)
        except (json.JSONDecodeError, KeyError):
            pass


def _rollback_from_dir(backup_dir: Path, bot_dir: Path) -> None:
    """Restore bot state from an old directory-based backup (backward compat)."""
    # Restore root-level files
    backup_config = backup_dir / "config.json"
    backup_soul = backup_dir / "SOUL.md"
    if backup_config.exists():
        shutil.copy2(backup_config, bot_dir / "config.json")
    if backup_soul.exists():
        shutil.copy2(backup_soul, bot_dir / "SOUL.md")

    # Restore .openclaw/ state if present in backup
    backup_oc = backup_dir / ".openclaw"
    if backup_oc.exists():
        dst_oc = bot_dir / ".openclaw"
        existing_token = ""
        oc_json = dst_oc / "openclaw.json"
        if oc_json.exists():
            try:
                cfg = json.loads(oc_json.read_text())
                existing_token = cfg.get("gateway", {}).get("auth", {}).get("token", "")
            except (json.JSONDecodeError, KeyError):
                pass

        for subdir in ("workspace", "agents", "cron"):
            target = dst_oc / subdir
            if target.exists():
                shutil.rmtree(target)

        _copy_openclaw_state(backup_oc, dst_oc)

        if existing_token and oc_json.exists():
            try:
                cfg = json.loads(oc_json.read_text())
                cfg.setdefault("gateway", {}).setdefault("auth", {})["token"] = existing_token
                with open(oc_json, "w") as f:
                    json.dump(cfg, f, indent=2)
            except (json.JSONDecodeError, KeyError):
                pass


def rollback_to_backup(name: str, timestamp: str) -> dict:
    """Auto-backup current state, then restore full agent state from backup."""
    bot_dir = config.BOTS_DIR / name
    if not bot_dir.exists():
        raise FileNotFoundError(f"Bot directory not found: {name}")

    path, kind = _find_backup(name, timestamp)
    if path is None:
        raise ValueError(f"Backup not found: {timestamp}")

    # Safety: auto-backup current state before overwriting
    create_backup(name, label="pre-rollback")

    if kind == "tar":
        _rollback_from_tar(path, bot_dir)
    else:
        _rollback_from_dir(path, bot_dir)

    meta = ensure_meta(name)
    meta["modified_at"] = _now_iso()
    write_meta(name, meta)

    return {"name": name, "rolled_back_to": timestamp}


def prune_scheduled_backups(name: str, keep: int | None = None) -> int:
    """Remove oldest scheduled backups beyond the retention limit. Returns count pruned."""
    if keep is None:
        keep = config.BACKUP_KEEP
    meta = ensure_meta(name)
    scheduled = [b for b in meta["backups"] if b.get("label") == "scheduled"]
    if len(scheduled) <= keep:
        return 0

    to_remove = scheduled[: len(scheduled) - keep]
    remove_timestamps = {b["timestamp"] for b in to_remove}
    pruned = 0

    for ts in remove_timestamps:
        # Delete the tar.gz (or old dir) from disk
        for base in [config.BACKUP_DIR / name if config.BACKUP_DIR else None, config.BOTS_DIR / name / ".backups"]:
            if base is None:
                continue
            tar_path = base / f"{ts}.tar.gz"
            if tar_path.exists():
                tar_path.unlink()
                pruned += 1
            dir_path = base / ts
            if dir_path.is_dir():
                shutil.rmtree(dir_path)
                pruned += 1

    meta["backups"] = [b for b in meta["backups"] if b["timestamp"] not in remove_timestamps]
    write_meta(name, meta)
    return pruned
