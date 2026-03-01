import copy
import json
import os
import re
import secrets
import shutil
import tarfile
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import bcrypt
import docker
from dotenv import load_dotenv
from fastapi import Cookie, Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from typing import Literal

from pydantic import BaseModel

load_dotenv()

# ---------------------------------------------------------------------------
# Path constants (overridable via env for Docker mount paths)
# ---------------------------------------------------------------------------
TEMPLATE_DIR = Path(os.environ.get("TEMPLATE_DIR", Path(__file__).resolve().parent.parent / "bot-template"))
BOTS_DIR = Path(os.environ.get("BOTS_DIR", Path(__file__).resolve().parent.parent / "bots"))
_backup_dir_env = os.environ.get("BACKUP_DIR", "")
BACKUP_DIR = Path(_backup_dir_env) if _backup_dir_env else None
BACKUP_INTERVAL_SECONDS = int(os.environ.get("BACKUP_INTERVAL_SECONDS", "3600"))
BACKUP_KEEP = int(os.environ.get("BACKUP_KEEP", "24"))

# ---------------------------------------------------------------------------
# Auth & RBAC
# ---------------------------------------------------------------------------
USERS_FILE = Path(os.environ.get("USERS_FILE", ""))  # resolved lazily after BOTS_DIR
SESSION_TTL = int(os.environ.get("SESSION_TTL", "86400"))  # 24h default
AUTH_DISABLED = os.environ.get("AUTH_DISABLED", "").lower() in ("1", "true", "yes")
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
SESSIONS: dict[str, dict] = {}  # token -> {username, role, bots, created_at}


def _users_file_path() -> Path:
    """Return the resolved path to users.json."""
    env = os.environ.get("USERS_FILE", "")
    if env:
        return Path(env)
    return BOTS_DIR / ".users.json"


def _load_users() -> dict:
    """Load users from JSON file. Returns {username: {password_hash, role, bots}}."""
    path = _users_file_path()
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_users(users: dict) -> None:
    """Atomically write users to JSON file."""
    path = _users_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(users, f, indent=2)
        os.replace(tmp, str(path))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()


def _verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except Exception:
        return False


def _bootstrap_admin() -> None:
    """Create default admin user if users file is empty or missing."""
    users = _load_users()
    if users:
        return
    password = os.environ.get("ADMIN_PASSWORD", "")
    if not password:
        password = secrets.token_urlsafe(16)
        print(f"[AUTH] Generated admin password for '{ADMIN_USER}': {password}")
    users[ADMIN_USER] = {
        "password_hash": _hash_password(password),
        "role": "admin",
        "bots": ["*"],
    }
    _save_users(users)
    print(f"[AUTH] Created admin user: {ADMIN_USER}")


def _create_session(username: str) -> str:
    """Create a new session token for a user. Returns the token."""
    token = secrets.token_urlsafe(32)
    users = _load_users()
    user = users.get(username, {})
    SESSIONS[token] = {
        "username": username,
        "role": user.get("role", "user"),
        "bots": user.get("bots", []),
        "created_at": time.time(),
    }
    return token


def _get_session(token: str) -> dict | None:
    """Validate a session token. Re-reads users.json for current RBAC. Returns None if invalid."""
    session = SESSIONS.get(token)
    if not session:
        return None
    # Check expiry
    if time.time() - session["created_at"] > SESSION_TTL:
        SESSIONS.pop(token, None)
        return None
    # Re-read user data for always-current permissions
    users = _load_users()
    user = users.get(session["username"])
    if not user:
        # User was deleted
        SESSIONS.pop(token, None)
        return None
    # Update session with current permissions
    session["role"] = user.get("role", "user")
    session["bots"] = user.get("bots", [])
    return session


def _cleanup_expired_sessions() -> int:
    """Remove all expired sessions. Returns count removed."""
    now = time.time()
    expired = [t for t, s in SESSIONS.items() if now - s["created_at"] > SESSION_TTL]
    for t in expired:
        del SESSIONS[t]
    return len(expired)


def _invalidate_user_sessions(username: str) -> int:
    """Remove all sessions for a given user. Returns count removed."""
    to_remove = [t for t, s in SESSIONS.items() if s["username"] == username]
    for t in to_remove:
        del SESSIONS[t]
    return len(to_remove)


_login_attempts: dict[str, list[float]] = {}
_login_lock = threading.Lock()


def _check_login_rate(ip: str) -> None:
    now = time.time()
    with _login_lock:
        attempts = [t for t in _login_attempts.get(ip, []) if now - t < 300]
        _login_attempts[ip] = attempts
        if len(attempts) >= 5:
            raise HTTPException(status_code=429, detail="Too many login attempts. Try again later.")


def _record_failed_login(ip: str) -> None:
    with _login_lock:
        _login_attempts.setdefault(ip, []).append(time.time())


def _user_can_access_bot(session: dict, bot_name: str) -> bool:
    """Check if user's session grants access to a specific bot."""
    if session["role"] == "admin":
        return True
    bots = session.get("bots", [])
    if "*" in bots:
        return True
    return bot_name in bots


def _grant_bot_to_user(username: str, bot_name: str) -> None:
    """Add bot_name to a user's bots list (no-op for admins/wildcard)."""
    users = _load_users()
    user = users.get(username)
    if not user:
        return
    if user.get("role") == "admin":
        return
    bots = user.get("bots", [])
    if "*" in bots or bot_name in bots:
        return
    bots.append(bot_name)
    user["bots"] = bots
    _save_users(users)


def _require_session(cfm_session: str | None = Cookie(None)) -> dict:
    """FastAPI dependency: require a valid session. Returns session dict."""
    if AUTH_DISABLED:
        return {"username": "dev", "role": "admin", "bots": ["*"]}
    session = _get_session(cfm_session) if cfm_session else None
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return session


def _require_bot_access(name: str, session: dict = Depends(_require_session)) -> dict:
    """FastAPI dependency: require session + access to a specific bot."""
    sname = sanitize_name(name)
    if not _user_can_access_bot(session, sname):
        raise HTTPException(status_code=403, detail=f"Access denied to bot {sname!r}")
    return {**session, "_bot_name": sname}


# ---------------------------------------------------------------------------
# Lazy Docker client
# ---------------------------------------------------------------------------
_client = None


def _get_client():
    global _client
    if _client is None:
        _client = docker.from_env()
    return _client


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------
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
    meta_path = BOTS_DIR / name / ".meta.json"
    if meta_path.exists():
        with open(meta_path) as f:
            return json.load(f)
    return {}


def write_meta(name: str, meta: dict) -> None:
    """Write .meta.json for a bot."""
    meta_path = BOTS_DIR / name / ".meta.json"
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)


def ensure_meta(name: str) -> dict:
    """Load or create .meta.json with defaults (migration for existing bots)."""
    meta = read_meta(name)
    if meta:
        return meta
    bot_dir = BOTS_DIR / name
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


# ---------------------------------------------------------------------------
# Backup & rollback
# ---------------------------------------------------------------------------
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
    if BACKUP_DIR:
        d = BACKUP_DIR / name
    else:
        d = BOTS_DIR / name / ".backups"
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
    bot_dir = BOTS_DIR / name
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
    for base in [BACKUP_DIR / name if BACKUP_DIR else None, BOTS_DIR / name / ".backups"]:
        if base is None:
            continue
        tar_path = base / f"{timestamp}.tar.gz"
        if tar_path.exists():
            return tar_path, "tar"
    # Backward compat: old directory-based backups
    dir_path = BOTS_DIR / name / ".backups" / timestamp
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
    bot_dir = BOTS_DIR / name
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


# ---------------------------------------------------------------------------
# Port allocation
# ---------------------------------------------------------------------------
def allocate_port() -> int:
    """Find first free port in configured range by scanning labeled containers."""
    start = int(os.environ.get("BOT_PORT_START", 3001))
    end = int(os.environ.get("BOT_PORT_END", 3100))
    client = _get_client()
    containers = client.containers.list(all=True, filters={"label": "openclaw.bot=true"})
    used = set()
    for c in containers:
        port_label = c.labels.get("openclaw.port")
        if port_label:
            used.add(int(port_label))
    for port in range(start, end + 1):
        if port not in used:
            return port
    raise RuntimeError(f"No free ports in range {start}-{end}")


# ---------------------------------------------------------------------------
# Config generation & file writing
# ---------------------------------------------------------------------------
def generate_config(name: str, extra_config: dict | None = None) -> dict:
    """Load template, inject LLM settings, deep-merge extra_config."""
    template_path = TEMPLATE_DIR / "config.template.json"
    with open(template_path) as f:
        config = json.load(f)

    config["llm"]["provider"] = "openai-compatible"
    config["llm"]["baseUrl"] = os.environ.get("LLM_BASE_URL", "")
    config["llm"]["model"] = os.environ.get("LLM_MODEL", "")

    if extra_config:
        config = deep_merge(config, extra_config)

    return config


def write_bot_files(name: str, config: dict, soul: str | None = None,
                    forked_from: str | None = None, created_by: str | None = None) -> Path:
    """Create bots/{name}/, write config.json + SOUL.md + .meta.json. Returns bot dir."""
    bot_dir = BOTS_DIR / name
    bot_dir.mkdir(parents=True, exist_ok=True)

    with open(bot_dir / "config.json", "w") as f:
        json.dump(config, f, indent=2)

    if soul and soul.strip():
        soul_text = soul
    else:
        default_soul = TEMPLATE_DIR / "SOUL.md"
        soul_text = default_soul.read_text() if default_soul.exists() else ""

    with open(bot_dir / "SOUL.md", "w") as f:
        f.write(soul_text)

    now = _now_iso()
    meta = {
        "created_at": now,
        "modified_at": now,
        "forked_from": forked_from,
        "created_by": created_by,
        "backups": [],
    }
    write_meta(name, meta)

    return bot_dir


# ---------------------------------------------------------------------------
# Container launcher (shared by create, duplicate, fork)
# ---------------------------------------------------------------------------
def _prepare_openclaw_home(bot_dir: Path, soul_text: str, trusted_proxies: list[str] | None = None,
                           bot_name: str | None = None) -> Path:
    """Create .openclaw/ config dir for a bot so it's usable from the start.

    If a workspace already exists (e.g. copied from a source bot during
    duplicate/fork), its files are preserved — only SOUL.md is written
    if it doesn't already exist in the workspace.
    """
    oc_dir = bot_dir / ".openclaw"
    oc_dir.mkdir(exist_ok=True)
    (oc_dir / "workspace").mkdir(exist_ok=True)

    # Only write workspace files if not already present (duplicate/fork may have copied them)
    ws = oc_dir / "workspace"
    ws_soul = ws / "SOUL.md"
    if not ws_soul.exists():
        ws_soul.write_text(soul_text)
    ws_memory = ws / "MEMORY.md"
    if not ws_memory.exists():
        ws_memory.write_text("")
    ws_memory_dir = ws / "memory"
    ws_memory_dir.mkdir(exist_ok=True)

    # Gateway config: register local model provider, set as default
    llm_model = os.environ.get("LLM_MODEL", "Qwen3.5-122B-A10B")
    llm_api_key = os.environ.get("LLM_API_KEY", "none")
    llm_base_url = os.environ.get("LLM_BASE_URL", "http://localhost:8000/v1")
    brave_api_key = os.environ.get("BRAVE_API_KEY", "")

    oc_config = {
        "models": {
            "mode": "merge",
            "providers": {
                "local": {
                    "baseUrl": llm_base_url,
                    "apiKey": llm_api_key,
                    "api": "openai-completions",
                    "models": [
                        {
                            "id": llm_model,
                            "name": llm_model,
                            "reasoning": False,
                            "input": ["text", "image"],
                            "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                            "contextWindow": 262144,
                            "maxTokens": 8192,
                        }
                    ],
                }
            },
        },
        "agents": {
            "defaults": {
                "model": f"local/{llm_model}",
                "compaction": {"mode": "safeguard"},
            }
        },
        "commands": {
            "native": "auto",
            "nativeSkills": "auto",
            "restart": True,
            "ownerDisplay": "raw",
        },
        "gateway": {
            "auth": {
                "mode": "trusted-proxy",
                "trustedProxy": {
                    "userHeader": "X-Forwarded-User",
                },
            },
            "controlUi": {
                "dangerouslyAllowHostHeaderOriginFallback": True,
            },
            "trustedProxies": trusted_proxies or ["127.0.0.1"],
        },
        **({"tools": {"web": {"search": {"provider": "brave", "apiKey": brave_api_key}}}}
           if brave_api_key else {}),
    }
    with open(oc_dir / "openclaw.json", "w") as f:
        json.dump(oc_config, f, indent=2)

    return oc_dir


def _host_path(container_path: Path) -> str:
    """Convert a container-internal path to the corresponding host path.

    When running inside Docker, BOTS_DIR is e.g. /data/bots but the host
    path is different (e.g. /home/user/botfarm/bots).  HOST_BOTS_DIR tells
    us the host-side mount point so volume mounts for bot containers resolve
    correctly from the Docker daemon's perspective.
    """
    host_bots = os.environ.get("HOST_BOTS_DIR", "")
    if not host_bots:
        return str(container_path.resolve())
    # Replace the BOTS_DIR prefix with the host path
    rel = container_path.resolve().relative_to(BOTS_DIR.resolve())
    return str(Path(host_bots) / rel)


def _launch_container(name: str, bot_dir: Path) -> dict:
    """Allocate port, create network, start container. Returns bot info."""
    port = allocate_port()
    client = _get_client()
    network_name = f"openclaw-net-{name}"
    client.networks.create(network_name, driver="bridge")

    container_name = f"openclaw-bot-{name}"
    image = os.environ.get("OPENCLAW_IMAGE", "ghcr.io/openclaw/openclaw:latest")

    # In Docker Compose mode, Caddy owns the port range — don't map to host.
    # In dev mode (no HOST_BOTS_DIR), map ports for direct access.
    in_compose = bool(os.environ.get("HOST_BOTS_DIR"))
    port_bindings = {} if in_compose else {"18789/tcp": port}

    # Connect Caddy to bot network BEFORE preparing openclaw config so we can
    # look up Caddy's exact IP for trustedProxies (OpenClaw doesn't support CIDR).
    trusted_proxies = None
    if in_compose:
        _connect_caddy_to_network(client, network_name)
        caddy_ip = _get_caddy_ip_on_network(client, network_name)
        trusted_proxies = ["127.0.0.1"]
        if caddy_ip:
            trusted_proxies.append(caddy_ip)

    # Read soul text for OpenClaw workspace injection
    soul_path = bot_dir / "SOUL.md"
    soul_text = soul_path.read_text() if soul_path.exists() else ""
    oc_dir = _prepare_openclaw_home(bot_dir, soul_text, trusted_proxies=trusted_proxies,
                                    bot_name=name)

    container = client.containers.run(
        image,
        name=container_name,
        detach=True,
        command=[
            "node", "openclaw.mjs", "gateway",
            "--allow-unconfigured", "--bind", "lan",
            *(["--auth", "trusted-proxy"] if in_compose else []),
        ],
        ports=port_bindings,
        volumes={
            _host_path(bot_dir): {"bind": "/data", "mode": "rw"},
            _host_path(oc_dir): {"bind": "/home/node/.openclaw", "mode": "rw"},
        },
        labels={
            "openclaw.bot": "true",
            "openclaw.port": str(port),
            "openclaw.name": name,
        },
        network=network_name,
        restart_policy={"Name": "unless-stopped"},
    )

    _sync_caddy_config()

    return {
        "name": name,
        "status": container.status,
        "port": port,
        "container_name": container_name,
        "ui_path": f"/claw/{name}/" if os.environ.get("HOST_BOTS_DIR") else None,
    }


# ---------------------------------------------------------------------------
# Bot lifecycle
# ---------------------------------------------------------------------------
def create_bot(name: str, soul: str | None = None, extra_config: dict | None = None,
               created_by: str | None = None) -> dict:
    """Full orchestration: sanitize, gen config, write files, launch container."""
    name = sanitize_name(name)
    if (BOTS_DIR / name).exists():
        raise ValueError(f"Bot already exists: {name!r}")
    config = generate_config(name, extra_config)
    bot_dir = write_bot_files(name, config, soul, created_by=created_by)
    return _launch_container(name, bot_dir)


def _copy_workspace(src_dir: Path, dst_dir: Path) -> None:
    """Copy the source bot's .openclaw/workspace/ to the destination.

    This preserves personality (SOUL.md), identity (IDENTITY.md, USER.md),
    memories (MEMORY.md, memory/), and any other workspace files the agent
    has created. Sessions and gateway auth are NOT copied — each bot gets
    a fresh conversation history and its own auth token.
    """
    src_ws = src_dir / ".openclaw" / "workspace"
    if not src_ws.exists():
        return
    dst_ws = dst_dir / ".openclaw" / "workspace"
    dst_ws.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src_ws, dst_ws, dirs_exist_ok=True)


def duplicate_bot(name: str, new_name: str, created_by: str | None = None) -> dict:
    """Copy a bot's full identity: config, soul, workspace, and memories."""
    name = sanitize_name(name)
    new_name = sanitize_name(new_name)

    src_dir = BOTS_DIR / name
    if not src_dir.exists():
        raise FileNotFoundError(f"Source bot not found: {name}")
    if (BOTS_DIR / new_name).exists():
        raise FileExistsError(f"Bot already exists: {new_name}")

    config = json.loads((src_dir / "config.json").read_text())
    soul = (src_dir / "SOUL.md").read_text() if (src_dir / "SOUL.md").exists() else ""

    bot_dir = write_bot_files(new_name, config, soul, forked_from=None, created_by=created_by)
    _copy_workspace(src_dir, bot_dir)
    return _launch_container(new_name, bot_dir)


def fork_bot(name: str, new_name: str, created_by: str | None = None) -> dict:
    """Fork a bot: copy full identity + memories, track lineage."""
    name = sanitize_name(name)
    new_name = sanitize_name(new_name)

    src_dir = BOTS_DIR / name
    if not src_dir.exists():
        raise FileNotFoundError(f"Source bot not found: {name}")
    if (BOTS_DIR / new_name).exists():
        raise FileExistsError(f"Bot already exists: {new_name}")

    config = json.loads((src_dir / "config.json").read_text())
    soul = (src_dir / "SOUL.md").read_text() if (src_dir / "SOUL.md").exists() else ""

    bot_dir = write_bot_files(new_name, config, soul, forked_from=name, created_by=created_by)
    _copy_workspace(src_dir, bot_dir)
    result = _launch_container(new_name, bot_dir)
    result["forked_from"] = name
    return result


def list_bots() -> list[dict]:
    """Query containers by label, merge metadata."""
    client = _get_client()
    containers = client.containers.list(all=True, filters={"label": "openclaw.bot=true"})
    bots = []
    for c in containers:
        name = c.labels.get("openclaw.name", "")
        meta = read_meta(name)
        bots.append({
            "name": name,
            "status": c.status,
            "port": int(c.labels.get("openclaw.port", 0)),
            "container_name": c.name,
            "forked_from": meta.get("forked_from"),
            "created_by": meta.get("created_by"),
            "created_at": meta.get("created_at"),
            "backup_count": len(meta.get("backups", [])),
            "storage_bytes": get_bot_storage(name),
            "cron_jobs": get_bot_cron_jobs(name),
            "ui_path": f"/claw/{name}/" if os.environ.get("HOST_BOTS_DIR") else None,
        })
    return bots


def delete_bot(name: str) -> dict:
    """Stop + remove container, network, config dir."""
    name = sanitize_name(name)
    client = _get_client()
    container_name = f"openclaw-bot-{name}"
    network_name = f"openclaw-net-{name}"

    try:
        container = client.containers.get(container_name)
        container.stop()
        container.remove()
    except docker.errors.NotFound:
        pass

    _disconnect_caddy_from_network(client, network_name)

    try:
        network = client.networks.get(network_name)
        network.remove()
    except docker.errors.NotFound:
        pass

    bot_dir = BOTS_DIR / name
    if bot_dir.exists():
        shutil.rmtree(bot_dir)

    _sync_caddy_config()

    return {"deleted": name}


# ---------------------------------------------------------------------------
# Bot storage & cron
# ---------------------------------------------------------------------------
def get_bot_storage(name: str) -> int:
    """Return total disk usage in bytes for a bot directory."""
    bot_dir = BOTS_DIR / name
    if not bot_dir.exists():
        return 0
    return sum(f.stat().st_size for f in bot_dir.rglob("*") if f.is_file())


def get_bot_cron_jobs(name: str) -> list[dict]:
    """Read cron jobs from the bot's .openclaw/cron/jobs.json."""
    cron_path = BOTS_DIR / name / ".openclaw" / "cron" / "jobs.json"
    if not cron_path.exists():
        return []
    try:
        data = json.loads(cron_path.read_text())
        return data.get("jobs", [])
    except (json.JSONDecodeError, OSError):
        return []


def get_gateway_token(name: str) -> str:
    """Read the gateway auth token from the bot's openclaw.json."""
    oc_path = BOTS_DIR / name / ".openclaw" / "openclaw.json"
    if not oc_path.exists():
        return ""
    try:
        cfg = json.loads(oc_path.read_text())
        return cfg.get("gateway", {}).get("auth", {}).get("token", "")
    except (json.JSONDecodeError, OSError):
        return ""


# ---------------------------------------------------------------------------
# Bot metrics & detail
# ---------------------------------------------------------------------------
def get_bot_stats(name: str) -> dict:
    """Get container stats: CPU %, memory, network I/O, uptime, restart count."""
    name = sanitize_name(name)
    client = _get_client()
    container = client.containers.get(f"openclaw-bot-{name}")

    stats = container.stats(stream=False)
    attrs = container.attrs

    # CPU calculation
    cpu_delta = stats["cpu_stats"]["cpu_usage"]["total_usage"] - \
                stats["precpu_stats"]["cpu_usage"]["total_usage"]
    system_delta = stats["cpu_stats"]["system_cpu_usage"] - \
                   stats["precpu_stats"]["system_cpu_usage"]
    num_cpus = stats["cpu_stats"].get("online_cpus", 1)
    cpu_percent = (cpu_delta / system_delta * num_cpus * 100.0) if system_delta > 0 else 0.0

    # Memory
    mem_usage = stats["memory_stats"].get("usage", 0)
    mem_limit = stats["memory_stats"].get("limit", 0)
    memory_mb = mem_usage / (1024 * 1024)
    memory_limit_mb = mem_limit / (1024 * 1024)
    memory_percent = (mem_usage / mem_limit * 100.0) if mem_limit > 0 else 0.0

    # Network I/O
    networks = stats.get("networks", {})
    rx_bytes = sum(n.get("rx_bytes", 0) for n in networks.values())
    tx_bytes = sum(n.get("tx_bytes", 0) for n in networks.values())

    # Uptime & restarts
    started_at = attrs.get("State", {}).get("StartedAt", "")
    restart_count = attrs.get("RestartCount", 0)

    uptime_seconds = 0
    if started_at and container.status == "running":
        try:
            start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            uptime_seconds = int((datetime.now(timezone.utc) - start).total_seconds())
        except (ValueError, TypeError):
            pass

    return {
        "cpu_percent": round(cpu_percent, 2),
        "memory_mb": round(memory_mb, 1),
        "memory_limit_mb": round(memory_limit_mb, 1),
        "memory_percent": round(memory_percent, 1),
        "network_rx_mb": round(rx_bytes / (1024 * 1024), 2),
        "network_tx_mb": round(tx_bytes / (1024 * 1024), 2),
        "uptime_seconds": uptime_seconds,
        "restart_count": restart_count,
        "started_at": started_at,
    }


def get_fleet_stats() -> dict:
    """Aggregate stats across all bot containers."""
    client = _get_client()
    containers = client.containers.list(all=True, filters={"label": "openclaw.bot=true"})

    total_bots = len(containers)
    running_bots = 0
    total_cpu = 0.0
    total_mem = 0.0
    total_mem_limit = 0.0
    total_rx = 0.0
    total_tx = 0.0
    total_storage = 0
    max_uptime = 0

    for c in containers:
        name = c.labels.get("openclaw.name", "")
        total_storage += get_bot_storage(name)

        if c.status != "running":
            continue
        running_bots += 1

        try:
            stats = c.stats(stream=False)
            attrs = c.attrs

            # CPU
            cpu_delta = stats["cpu_stats"]["cpu_usage"]["total_usage"] - \
                        stats["precpu_stats"]["cpu_usage"]["total_usage"]
            system_delta = stats["cpu_stats"]["system_cpu_usage"] - \
                           stats["precpu_stats"]["system_cpu_usage"]
            num_cpus = stats["cpu_stats"].get("online_cpus", 1)
            if system_delta > 0:
                total_cpu += cpu_delta / system_delta * num_cpus * 100.0

            # Memory
            mem_usage = stats["memory_stats"].get("usage", 0)
            mem_limit = stats["memory_stats"].get("limit", 0)
            total_mem += mem_usage / (1024 * 1024)
            total_mem_limit += mem_limit / (1024 * 1024)

            # Network
            networks = stats.get("networks", {})
            total_rx += sum(n.get("rx_bytes", 0) for n in networks.values()) / (1024 * 1024)
            total_tx += sum(n.get("tx_bytes", 0) for n in networks.values()) / (1024 * 1024)

            # Uptime
            started_at = attrs.get("State", {}).get("StartedAt", "")
            if started_at:
                try:
                    start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                    uptime = int((datetime.now(timezone.utc) - start).total_seconds())
                    max_uptime = max(max_uptime, uptime)
                except (ValueError, TypeError):
                    pass
        except Exception:
            continue

    return {
        "total_bots": total_bots,
        "running_bots": running_bots,
        "total_cpu_percent": round(total_cpu, 2),
        "total_memory_mb": round(total_mem, 1),
        "total_memory_limit_mb": round(total_mem_limit, 1),
        "total_storage_bytes": total_storage,
        "total_network_rx_mb": round(total_rx, 2),
        "total_network_tx_mb": round(total_tx, 2),
        "max_uptime_seconds": max_uptime,
    }


def get_bot_detail(name: str) -> dict:
    """Full bot info: config content, soul content, metadata, stats."""
    name = sanitize_name(name)
    bot_dir = BOTS_DIR / name

    config = {}
    soul = ""
    if (bot_dir / "config.json").exists():
        config = json.loads((bot_dir / "config.json").read_text())
    if (bot_dir / "SOUL.md").exists():
        soul = (bot_dir / "SOUL.md").read_text()

    gateway_token = get_gateway_token(name)

    meta = ensure_meta(name)

    # Try to get container info + stats
    try:
        client = _get_client()
        container = client.containers.get(f"openclaw-bot-{name}")
        status = container.status
        port = int(container.labels.get("openclaw.port", 0))
        container_name = container.name
        try:
            stats = get_bot_stats(name)
        except Exception:
            stats = None
    except docker.errors.NotFound:
        status = "not_found"
        port = 0
        container_name = ""
        stats = None

    return {
        "name": name,
        "status": status,
        "port": port,
        "container_name": container_name,
        "config": config,
        "soul": soul,
        "meta": meta,
        "stats": stats,
        "gateway_token": gateway_token,
        "storage_bytes": get_bot_storage(name),
        "cron_jobs": get_bot_cron_jobs(name),
        "ui_path": f"/claw/{name}/" if os.environ.get("HOST_BOTS_DIR") else None,
    }


# ---------------------------------------------------------------------------
# Caddy network helpers
# ---------------------------------------------------------------------------
CADDY_CONTAINER = os.environ.get("CADDY_CONTAINER", "botfarm-caddy-1")


def _connect_caddy_to_network(client, network_name: str) -> None:
    """Connect Caddy container to a bot's bridge network."""
    try:
        network = client.networks.get(network_name)
        network.connect(CADDY_CONTAINER)
    except Exception:
        pass  # Caddy not running or already connected


def _get_caddy_ip_on_network(client, network_name: str) -> str | None:
    """Get Caddy container's IP address on a specific bot bridge network."""
    try:
        network = client.networks.get(network_name)
        network.reload()
        for container_id, info in network.attrs.get("Containers", {}).items():
            # Match by name
            try:
                c = client.containers.get(container_id)
                if c.name == CADDY_CONTAINER:
                    ip = info.get("IPv4Address", "")
                    return ip.split("/")[0] if ip else None
            except Exception:
                continue
    except Exception:
        pass
    return None


def _disconnect_caddy_from_network(client, network_name: str) -> None:
    """Disconnect Caddy container from a bot's bridge network."""
    try:
        network = client.networks.get(network_name)
        network.disconnect(CADDY_CONTAINER)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Caddy reverse proxy sync
# ---------------------------------------------------------------------------
CADDY_ADMIN_URL = os.environ.get("CADDY_ADMIN_URL", "http://caddy:2019")
PORTAL_URL = os.environ.get("PORTAL_URL", "")  # e.g. "https://10.88.142.100"


def _sync_caddy_config() -> None:
    """Push updated route config to Caddy's admin API.

    Builds a JSON config with:
    - Main HTTPS server on :8443 for dashboard + frontend + path-based bot routes
    - HTTP server on :80 for HTTPS redirect
    - forward_auth subrequests to /api/auth/verify for authentication

    Bots are routed via /claw/{name}/ paths on the main :8443 port.
    OpenClaw's basePath config handles serving at the sub-path natively.

    Fails silently when Caddy is not reachable (dev mode).
    """
    try:
        import requests as _req

        client = _get_client()
        containers = client.containers.list(
            all=False, filters={"label": "openclaw.bot=true"}
        )

        caddy_port = int(os.environ.get("CADDY_PORT", "8443"))
        tls_policy = [{"certificate_selection": {"any_tag": ["cert0"]}}]

        # forward_auth handler: subrequest to dashboard's /api/auth/verify
        login_url = (
            f"{PORTAL_URL}:{caddy_port}/login"
            if PORTAL_URL else
            f"https://{{http.request.host}}:{caddy_port}/login"
        )

        def _forward_auth_handler(extra_headers=None, redirect_on_fail=False):
            """Build a Caddy forward_auth (reverse_proxy) handler.

            Uses copy_response_headers (Caddy's native forward_auth mechanism)
            so that the auth subrequest doesn't consume the original connection.
            This is critical for WebSocket upgrade requests.
            """
            handle_response = [
                {
                    "match": {"status_code": [2]},
                    "routes": [
                        {
                            "handle": [{
                                "handler": "headers",
                                "request": {
                                    "set": {
                                        "X-Forwarded-User": [
                                            "{http.reverse_proxy.header.X-Forwarded-User}"
                                        ],
                                    },
                                },
                            }],
                        },
                    ],
                },
            ]
            if redirect_on_fail:
                handle_response.append({
                    "match": {"status_code": [4]},
                    "routes": [{
                        "handle": [{
                            "handler": "static_response",
                            "headers": {"Location": [login_url]},
                            "status_code": 302,
                        }],
                    }],
                })
            # Default: pass through auth server's error response
            handle_response.append({
                "routes": [{
                    "handle": [{
                        "handler": "copy_response",
                    }],
                }],
            })
            h = {
                "handler": "reverse_proxy",
                "upstreams": [{"dial": "dashboard:8080"}],
                "rewrite": {"method": "GET", "uri": "/api/auth/verify"},
                "headers": {"request": {
                    "set": {},
                    "delete": [
                        "Connection",
                        "Upgrade",
                        "Sec-WebSocket-Version",
                        "Sec-WebSocket-Key",
                        "Sec-WebSocket-Extensions",
                        "Sec-WebSocket-Protocol",
                    ],
                }},
                "handle_response": handle_response,
            }
            if extra_headers:
                h["headers"]["request"]["set"].update(extra_headers)
            return h

        # Main HTTPS routes
        if AUTH_DISABLED:
            main_routes = [
                {
                    "match": [{"path": ["/api/*"]}],
                    "handle": [{
                        "handler": "reverse_proxy",
                        "upstreams": [{"dial": "dashboard:8080"}],
                    }],
                },
                {
                    "handle": [{
                        "handler": "reverse_proxy",
                        "upstreams": [{"dial": "frontend:3000"}],
                    }],
                },
            ]
        else:
            main_routes = [
                # Public auth endpoints
                {
                    "match": [{"path": [
                        "/api/auth/login", "/api/auth/verify", "/api/auth/logout",
                    ]}],
                    "handle": [{
                        "handler": "reverse_proxy",
                        "upstreams": [{"dial": "dashboard:8080"}],
                    }],
                },
                # Protected API
                {
                    "match": [{"path": ["/api/*"]}],
                    "handle": [
                        _forward_auth_handler(),
                        {
                            "handler": "reverse_proxy",
                            "upstreams": [{"dial": "dashboard:8080"}],
                        },
                    ],
                },
                # Public frontend routes (login, assets)
                {
                    "match": [{"path": [
                        "/login", "/login/*", "/_next/*", "/favicon.ico",
                    ]}],
                    "handle": [{
                        "handler": "reverse_proxy",
                        "upstreams": [{"dial": "frontend:3000"}],
                    }],
                },
                # Protected frontend (everything else) — redirect to login on 4xx
                {
                    "handle": [
                        _forward_auth_handler(redirect_on_fail=True),
                        {
                            "handler": "reverse_proxy",
                            "upstreams": [{"dial": "frontend:3000"}],
                        },
                    ],
                },
            ]

        # Path-based bot routes — insert before catch-all frontend route.
        # Caddy strips /claw/{name} prefix before proxying to the bot, so
        # OpenClaw serves everything at root (no basePath needed).
        # OpenClaw Control UI connects WebSocket to wss://{host}/ (root),
        # so we set a cfm_bot cookie and route root WS via that cookie.
        if os.environ.get("HOST_BOTS_DIR"):
            for c in containers:
                name = c.labels.get("openclaw.name", "")
                if not name:
                    continue
                container_name = f"openclaw-bot-{name}"
                bot_proxy = {"handler": "reverse_proxy", "upstreams": [{"dial": f"{container_name}:18789"}]}
                strip_prefix = {"handler": "rewrite", "strip_path_prefix": f"/claw/{name}"}
                set_cookie = {"handler": "headers", "response": {"set": {
                    "Set-Cookie": [f"cfm_bot={name}; Path=/; HttpOnly; Secure; SameSite=Lax"],
                }}}
                ws_cookie_re = f"(?:^|;\\s*)cfm_bot={re.escape(name)}(?:;|$)"
                if AUTH_DISABLED:
                    fwd_user = {"handler": "headers", "request": {"set": {"X-Forwarded-User": ["dev"]}}}
                    handlers = [fwd_user, set_cookie, strip_prefix, bot_proxy]
                else:
                    handlers = [
                        _forward_auth_handler(extra_headers={"X-Original-Bot": [name]}, redirect_on_fail=True),
                        set_cookie,
                        strip_prefix,
                        bot_proxy,
                    ]
                # Bot route: matches path /claw/{name}/* OR root / WebSocket
                # with cfm_bot cookie. strip_path_prefix is a no-op for root /.
                main_routes.insert(-1, {
                    "match": [
                        {"path": [f"/claw/{name}/*", f"/claw/{name}"]},
                        {
                            "path": ["/"],
                            "header": {"Upgrade": ["websocket"]},
                            "header_regexp": {"Cookie": {"name": "cfm_bot", "pattern": ws_cookie_re}},
                        },
                    ],
                    "handle": handlers,
                })

        config = {
            "admin": {"listen": ":2019"},
            "apps": {
                "http": {
                    "servers": {
                        "https": {
                            "listen": [f":{caddy_port}"],
                            "routes": main_routes,
                            "tls_connection_policies": tls_policy,
                        },
                        "http": {
                            "listen": [":80"],
                            "routes": [{
                                "handle": [{
                                    "handler": "static_response",
                                    "headers": {
                                        "Location": [
                                            f"{PORTAL_URL}:{caddy_port}{{http.request.uri}}"
                                            if PORTAL_URL else
                                            f"https://{{http.request.host}}:{caddy_port}{{http.request.uri}}"
                                        ]
                                    },
                                    "status_code": 302,
                                }],
                            }],
                        },
                    },
                },
                "tls": {
                    "certificates": {
                        "load_files": [{
                            "certificate": "/certs/cert.pem",
                            "key": "/certs/key.pem",
                            "tags": ["cert0"],
                        }]
                    }
                },
            },
        }

        _req.post(
            f"{CADDY_ADMIN_URL}/load",
            json=config,
            headers={"Content-Type": "application/json"},
            timeout=5,
        )
    except Exception:
        pass  # Caddy not running (dev mode) — silently ignore


# ---------------------------------------------------------------------------
# Backup retention
# ---------------------------------------------------------------------------
def prune_scheduled_backups(name: str, keep: int | None = None) -> int:
    """Remove oldest scheduled backups beyond the retention limit. Returns count pruned."""
    if keep is None:
        keep = BACKUP_KEEP
    meta = ensure_meta(name)
    scheduled = [b for b in meta["backups"] if b.get("label") == "scheduled"]
    if len(scheduled) <= keep:
        return 0

    to_remove = scheduled[: len(scheduled) - keep]
    remove_timestamps = {b["timestamp"] for b in to_remove}
    pruned = 0

    for ts in remove_timestamps:
        # Delete the tar.gz (or old dir) from disk
        for base in [BACKUP_DIR / name if BACKUP_DIR else None, BOTS_DIR / name / ".backups"]:
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


# ---------------------------------------------------------------------------
# Backup scheduler
# ---------------------------------------------------------------------------
_backup_stop_event = threading.Event()


def _backup_scheduler():
    """Run scheduled backups of all bots at a fixed interval."""
    interval = BACKUP_INTERVAL_SECONDS
    if interval <= 0:
        return
    while not _backup_stop_event.wait(interval):
        try:
            client = _get_client()
            containers = client.containers.list(
                all=True, filters={"label": "openclaw.bot=true"}
            )
            for c in containers:
                bot_name = c.labels.get("openclaw.name", "")
                if bot_name:
                    try:
                        create_backup(bot_name, label="scheduled")
                        prune_scheduled_backups(bot_name)
                    except Exception:
                        pass
        except Exception:
            pass


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
@asynccontextmanager
async def _lifespan(app: FastAPI):
    # Bootstrap admin user on first run
    if not AUTH_DISABLED:
        _bootstrap_admin()
    # Ensure Caddy is connected to all existing bot networks on startup
    if os.environ.get("HOST_BOTS_DIR"):
        try:
            client = _get_client()
            containers = client.containers.list(
                all=True, filters={"label": "openclaw.bot=true"}
            )
            for c in containers:
                name = c.labels.get("openclaw.name", "")
                if name:
                    _connect_caddy_to_network(client, f"openclaw-net-{name}")
        except Exception:
            pass
        # Migrate existing bots: remove basePath (Caddy strip_path_prefix handles it)
        for bot_dir in BOTS_DIR.iterdir():
            if not bot_dir.is_dir() or bot_dir.name.startswith("."):
                continue
            oc_cfg = bot_dir / ".openclaw" / "openclaw.json"
            if not oc_cfg.exists():
                continue
            try:
                cfg = json.loads(oc_cfg.read_text())
                cui = cfg.get("gateway", {}).get("controlUi", {})
                if "basePath" in cui:
                    del cui["basePath"]
                    oc_cfg.write_text(json.dumps(cfg, indent=2))
            except (json.JSONDecodeError, OSError):
                pass
    _sync_caddy_config()
    # Start backup scheduler thread
    if BACKUP_INTERVAL_SECONDS > 0:
        _backup_stop_event.clear()
        t = threading.Thread(target=_backup_scheduler, daemon=True, name="backup-scheduler")
        t.start()
    yield
    _backup_stop_event.set()


app = FastAPI(title="ClawFleetManager", lifespan=_lifespan)

_cors_origins: list[str] = ["*"]
_cors_credentials = False
if PORTAL_URL:
    caddy_port = int(os.environ.get("CADDY_PORT", "8443"))
    _cors_origins = [f"{PORTAL_URL}:{caddy_port}"]
    if caddy_port == 443:
        _cors_origins.append(PORTAL_URL)
    _cors_credentials = True

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_cors_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)


class CreateBotRequest(BaseModel):
    name: str
    soul: str | None = None
    extra_config: dict | None = None


class DuplicateRequest(BaseModel):
    new_name: str


class ForkRequest(BaseModel):
    new_name: str


class RollbackRequest(BaseModel):
    timestamp: str


class LoginRequest(BaseModel):
    username: str
    password: str


class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: Literal["admin", "user"] = "user"
    bots: list[str] = []


class UpdateUserRequest(BaseModel):
    password: str | None = None
    role: Literal["admin", "user"] | None = None
    bots: list[str] | None = None


# --- Auth ---

def _set_session_cookie(response: Response, token: str) -> None:
    # Secure=True only when behind Caddy (compose mode) — TestClient uses HTTP
    secure = bool(os.environ.get("HOST_BOTS_DIR"))
    response.set_cookie(
        "cfm_session", token,
        httponly=True, secure=secure, samesite="lax",
        path="/", max_age=SESSION_TTL,
    )


@app.post("/api/auth/login")
async def api_auth_login(req: LoginRequest, request: Request, response: Response):
    if AUTH_DISABLED:
        return {"ok": True, "username": "dev", "role": "admin"}
    ip = request.client.host if request.client else "unknown"
    _check_login_rate(ip)
    users = _load_users()
    user = users.get(req.username)
    if not user or not _verify_password(req.password, user["password_hash"]):
        _record_failed_login(ip)
        raise HTTPException(status_code=401, detail="Invalid username or password")
    # Clear failed attempts on success
    with _login_lock:
        _login_attempts.pop(ip, None)
    token = _create_session(req.username)
    _set_session_cookie(response, token)
    return {"ok": True, "username": req.username, "role": user["role"]}


@app.post("/api/auth/logout")
async def api_auth_logout(response: Response, cfm_session: str | None = Cookie(None)):
    if cfm_session:
        SESSIONS.pop(cfm_session, None)
    response.delete_cookie("cfm_session", path="/")
    return {"ok": True}


@app.get("/api/auth/verify")
async def api_auth_verify(request: Request, response: Response,
                          cfm_session: str | None = Cookie(None)):
    """Caddy forward_auth endpoint. Returns 200 + X-Forwarded-User header or 401."""
    if AUTH_DISABLED:
        return Response(status_code=200, headers={"X-Forwarded-User": "dev"})

    session = _get_session(cfm_session) if cfm_session else None
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")

    # Per-bot RBAC: check if user can access this bot (via X-Original-Bot)
    original_bot = request.headers.get("X-Original-Bot")
    if original_bot:
        if not _user_can_access_bot(session, original_bot):
            raise HTTPException(status_code=403, detail="Access denied to this bot")

    return Response(status_code=200, headers={"X-Forwarded-User": session["username"]})


@app.get("/api/auth/me")
async def api_auth_me(cfm_session: str | None = Cookie(None)):
    """Return current user info for the frontend."""
    if AUTH_DISABLED:
        return {"username": "dev", "role": "admin", "bots": ["*"]}
    session = _get_session(cfm_session) if cfm_session else None
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return {
        "username": session["username"],
        "role": session["role"],
        "bots": session["bots"],
    }


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


@app.post("/api/auth/change-password")
async def api_auth_change_password(req: ChangePasswordRequest,
                                    session: dict = Depends(_require_session)):
    """Allow any user to change their own password."""
    users = _load_users()
    user = users.get(session["username"])
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not _verify_password(req.current_password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Current password is incorrect")
    if not req.new_password.strip():
        raise HTTPException(status_code=400, detail="New password cannot be empty")
    user["password_hash"] = _hash_password(req.new_password)
    _save_users(users)
    _invalidate_user_sessions(session["username"])
    return {"ok": True}


@app.get("/api/auth/users")
async def api_auth_list_users(cfm_session: str | None = Cookie(None)):
    """List all users (admin only). Password hashes are excluded."""
    if AUTH_DISABLED:
        return []
    session = _get_session(cfm_session) if cfm_session else None
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if session["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    users = _load_users()
    return [
        {"username": name, "role": u["role"], "bots": u.get("bots", [])}
        for name, u in users.items()
    ]


@app.post("/api/auth/users")
async def api_auth_create_user(req: CreateUserRequest,
                               cfm_session: str | None = Cookie(None)):
    """Create a new user (admin only)."""
    if AUTH_DISABLED:
        raise HTTPException(status_code=400, detail="Auth is disabled")
    session = _get_session(cfm_session) if cfm_session else None
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if session["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    users = _load_users()
    if req.username in users:
        raise HTTPException(status_code=409, detail="User already exists")
    if not req.username.strip():
        raise HTTPException(status_code=400, detail="Username cannot be empty")
    if not req.password.strip():
        raise HTTPException(status_code=400, detail="Password cannot be empty")
    users[req.username] = {
        "password_hash": _hash_password(req.password),
        "role": req.role,
        "bots": req.bots,
    }
    _save_users(users)
    return {"username": req.username, "role": req.role, "bots": req.bots}


@app.put("/api/auth/users/{username}")
async def api_auth_update_user(username: str, req: UpdateUserRequest,
                               cfm_session: str | None = Cookie(None)):
    """Update a user (admin only). Invalidates their sessions on change."""
    if AUTH_DISABLED:
        raise HTTPException(status_code=400, detail="Auth is disabled")
    session = _get_session(cfm_session) if cfm_session else None
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if session["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    users = _load_users()
    if username not in users:
        raise HTTPException(status_code=404, detail="User not found")

    # Prevent demoting the last admin
    if req.role and req.role != "admin" and users[username]["role"] == "admin":
        admin_count = sum(1 for u in users.values() if u["role"] == "admin")
        if admin_count <= 1:
            raise HTTPException(status_code=400, detail="Cannot demote the last admin")

    user = users[username]
    if req.password is not None and req.password.strip():
        user["password_hash"] = _hash_password(req.password)
    if req.role is not None:
        user["role"] = req.role
    if req.bots is not None:
        user["bots"] = req.bots
    _save_users(users)
    _invalidate_user_sessions(username)
    return {"username": username, "role": user["role"], "bots": user.get("bots", [])}


@app.delete("/api/auth/users/{username}")
async def api_auth_delete_user(username: str,
                               cfm_session: str | None = Cookie(None)):
    """Delete a user (admin only). Can't delete self or last admin."""
    if AUTH_DISABLED:
        raise HTTPException(status_code=400, detail="Auth is disabled")
    session = _get_session(cfm_session) if cfm_session else None
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if session["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    users = _load_users()
    if username not in users:
        raise HTTPException(status_code=404, detail="User not found")
    if username == session["username"]:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")
    if users[username]["role"] == "admin":
        admin_count = sum(1 for u in users.values() if u["role"] == "admin")
        if admin_count <= 1:
            raise HTTPException(status_code=400, detail="Cannot delete the last admin")
    _invalidate_user_sessions(username)
    del users[username]
    _save_users(users)
    return {"deleted": username}


# --- Config ---

@app.get("/api/config")
async def api_config(session: dict = Depends(_require_session)):
    """Return public configuration for the frontend."""
    return {
        "portal_url": PORTAL_URL or None,
        "caddy_port": int(os.environ.get("CADDY_PORT", "8443")),
    }


# --- Fleet stats ---

@app.get("/api/fleet/stats")
async def api_fleet_stats(session: dict = Depends(_require_session)):
    try:
        return get_fleet_stats()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- Bot CRUD ---

@app.get("/api/bots")
async def api_list_bots(session: dict = Depends(_require_session)):
    bots = list_bots()
    if session["role"] != "admin" and "*" not in session.get("bots", []):
        allowed = set(session.get("bots", []))
        bots = [b for b in bots if b["name"] in allowed]
    return bots


@app.post("/api/bots")
async def api_create_bot(req: CreateBotRequest, session: dict = Depends(_require_session)):
    try:
        result = create_bot(req.name, req.soul, req.extra_config,
                            created_by=session["username"])
        _grant_bot_to_user(session["username"], result["name"])
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except (RuntimeError, PermissionError) as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")


@app.post("/api/bots/{name}/start")
async def api_start_bot(name: str, ctx: dict = Depends(_require_bot_access)):
    name = ctx["_bot_name"]
    client = _get_client()
    try:
        container = client.containers.get(f"openclaw-bot-{name}")
        container.start()
        _sync_caddy_config()
        return {"name": name, "status": "running"}
    except docker.errors.NotFound:
        raise HTTPException(status_code=404, detail=f"Bot {name!r} not found")


@app.post("/api/bots/{name}/stop")
async def api_stop_bot(name: str, ctx: dict = Depends(_require_bot_access)):
    name = ctx["_bot_name"]
    client = _get_client()
    try:
        container = client.containers.get(f"openclaw-bot-{name}")
        container.stop()
        _sync_caddy_config()
        return {"name": name, "status": "stopped"}
    except docker.errors.NotFound:
        raise HTTPException(status_code=404, detail=f"Bot {name!r} not found")


@app.post("/api/bots/{name}/restart")
async def api_restart_bot(name: str, ctx: dict = Depends(_require_bot_access)):
    name = ctx["_bot_name"]
    client = _get_client()
    try:
        container = client.containers.get(f"openclaw-bot-{name}")
        container.restart()
        return {"name": name, "status": "running"}
    except docker.errors.NotFound:
        raise HTTPException(status_code=404, detail=f"Bot {name!r} not found")


@app.delete("/api/bots/{name}")
async def api_delete_bot(name: str, ctx: dict = Depends(_require_bot_access)):
    try:
        return delete_bot(ctx["_bot_name"])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/bots/{name}/logs")
async def api_bot_logs(name: str, ctx: dict = Depends(_require_bot_access)):
    name = ctx["_bot_name"]
    client = _get_client()
    try:
        container = client.containers.get(f"openclaw-bot-{name}")
        logs = container.logs(tail=200).decode("utf-8", errors="replace")
        return {"name": name, "logs": logs}
    except docker.errors.NotFound:
        raise HTTPException(status_code=404, detail=f"Bot {name!r} not found")


# --- Duplicate & Fork ---

@app.post("/api/bots/{name}/duplicate")
async def api_duplicate_bot(name: str, req: DuplicateRequest,
                            ctx: dict = Depends(_require_bot_access)):
    try:
        result = duplicate_bot(ctx["_bot_name"], req.new_name,
                               created_by=ctx["username"])
        _grant_bot_to_user(ctx["username"], result["name"])
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except FileExistsError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except (RuntimeError, PermissionError) as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")


@app.post("/api/bots/{name}/fork")
async def api_fork_bot(name: str, req: ForkRequest,
                       ctx: dict = Depends(_require_bot_access)):
    try:
        result = fork_bot(ctx["_bot_name"], req.new_name,
                          created_by=ctx["username"])
        _grant_bot_to_user(ctx["username"], result["name"])
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except FileExistsError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except (RuntimeError, PermissionError) as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")


# --- Backup & Rollback ---

@app.post("/api/bots/{name}/backup")
async def api_create_backup(name: str, ctx: dict = Depends(_require_bot_access)):
    name = ctx["_bot_name"]
    try:
        return create_backup(name)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/api/bots/{name}/backups")
async def api_list_backups(name: str, ctx: dict = Depends(_require_bot_access)):
    name = ctx["_bot_name"]
    bot_dir = BOTS_DIR / name
    if not bot_dir.exists():
        raise HTTPException(status_code=404, detail=f"Bot {name!r} not found")
    return list_backups(name)


@app.post("/api/bots/{name}/rollback")
async def api_rollback_bot(name: str, req: RollbackRequest,
                           ctx: dict = Depends(_require_bot_access)):
    name = ctx["_bot_name"]
    try:
        result = rollback_to_backup(name, req.timestamp)
        client = _get_client()
        try:
            container = client.containers.get(f"openclaw-bot-{name}")
            container.restart()
            result["status"] = "running"
        except docker.errors.NotFound:
            result["status"] = "not_found"
        return result
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# --- Metadata & Stats ---

@app.get("/api/bots/{name}/meta")
async def api_bot_meta(name: str, ctx: dict = Depends(_require_bot_access)):
    name = ctx["_bot_name"]
    bot_dir = BOTS_DIR / name
    if not bot_dir.exists():
        raise HTTPException(status_code=404, detail=f"Bot {name!r} not found")
    return ensure_meta(name)


@app.get("/api/bots/{name}/stats")
async def api_bot_stats(name: str, ctx: dict = Depends(_require_bot_access)):
    try:
        return get_bot_stats(ctx["_bot_name"])
    except docker.errors.NotFound:
        raise HTTPException(status_code=404, detail=f"Bot {name!r} not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/bots/{name}/detail")
async def api_bot_detail(name: str, ctx: dict = Depends(_require_bot_access)):
    try:
        return get_bot_detail(ctx["_bot_name"])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# --- Device pairing helpers ---

@app.post("/api/bots/{name}/approve-devices")
async def api_approve_devices(name: str, ctx: dict = Depends(_require_bot_access)):
    """Approve all pending device pairing requests for a bot."""
    name = ctx["_bot_name"]
    client = _get_client()
    container_name = f"openclaw-bot-{name}"
    try:
        container = client.containers.get(container_name)
    except docker.errors.NotFound:
        raise HTTPException(status_code=404, detail=f"Bot {name!r} not found")

    # List pending devices
    result = container.exec_run(["node", "openclaw.mjs", "devices", "list", "--json"])
    if result.exit_code != 0:
        raise HTTPException(status_code=500, detail="Failed to list devices")

    try:
        devices = json.loads(result.output.decode())
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {"approved": 0, "message": "Could not parse device list"}

    pending = devices.get("pending", [])
    approved = []
    for req in pending:
        req_id = req.get("requestId", req.get("id", ""))
        if req_id:
            approve_result = container.exec_run(
                ["node", "openclaw.mjs", "devices", "approve", req_id]
            )
            if approve_result.exit_code == 0:
                approved.append(req_id)

    return {"approved": len(approved), "request_ids": approved}
