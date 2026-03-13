import copy
import io
import json
import logging
import os
import re
import secrets
import shutil
import tarfile
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import bcrypt
import docker
from dotenv import load_dotenv
from fastapi import Cookie, Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv()

log = logging.getLogger(__name__)

# Ensure numeric template vars have defaults (unquoted in templates → must resolve)
os.environ.setdefault("LLM_CONTEXT_WINDOW", "128000")
os.environ.setdefault("LLM_MAX_TOKENS", "8192")

# Provider-specific model defaults
os.environ.setdefault("ANTHROPIC_MODEL", "claude-sonnet-4-6")
os.environ.setdefault("OPENAI_MODEL", "gpt-4o")
os.environ.setdefault("MINIMAX_MODEL", "MiniMax-M1")
os.environ.setdefault("MINIMAX_CONTEXT_WINDOW", "1000000")
os.environ.setdefault("MINIMAX_MAX_TOKENS", "8192")
os.environ.setdefault("QWEN_MODEL", "qwen-plus")
os.environ.setdefault("QWEN_CONTEXT_WINDOW", "131072")
os.environ.setdefault("QWEN_MAX_TOKENS", "8192")

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


# Dummy hash used to burn constant time when user doesn't exist (prevents timing enumeration)
_DUMMY_HASH = bcrypt.hashpw(b"dummy", bcrypt.gensalt(rounds=12)).decode()


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
    generated = False
    if not password:
        password = secrets.token_urlsafe(16)
        generated = True
    users[ADMIN_USER] = {
        "password_hash": _hash_password(password),
        "role": "admin",
        "bots": ["*"],
    }
    _save_users(users)
    if generated:
        print("")
        print("\u2554" + "\u2550" * 50 + "\u2557")
        print("\u2551  ClawFarm — First Run Setup" + " " * 22 + "\u2551")
        print("\u2551" + " " * 50 + "\u2551")
        print(f"\u2551  Admin user:  {ADMIN_USER:<36}\u2551")
        print(f"\u2551  Password:    {password:<36}\u2551")
        print("\u2551" + " " * 50 + "\u2551")
        print("\u2551  Save this \u2014 it won't be shown again." + " " * 12 + "\u2551")
        print("\u255a" + "\u2550" * 50 + "\u255d")
        print("")
    else:
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


def _cleanup_stale_rate_limits() -> int:
    """Remove stale entries from _login_attempts. Returns count removed."""
    now = time.time()
    with _login_lock:
        stale = [ip for ip, ts in _login_attempts.items() if all(now - t >= 300 for t in ts)]
        for ip in stale:
            del _login_attempts[ip]
    return len(stale)


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


def _resolve_template(template_text: str) -> str:
    """Replace {{VAR_NAME}} placeholders with env var values."""
    def replacer(match):
        var_name = match.group(1)
        value = os.environ.get(var_name, "")
        return value if value else match.group(0)  # Keep placeholder if unset
    return re.sub(r"\{\{(\w+)\}\}", replacer, template_text)


def list_templates(resolve_config: bool = False) -> list[dict]:
    """List available bot templates.

    Args:
        resolve_config: If True (admin), resolve {{VAR}} placeholders in config
                        preview. If False (regular user), show raw template with
                        placeholders intact.
    """
    templates = []
    for d in sorted(TEMPLATE_DIR.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        soul_path = d / "SOUL.md"
        soul_preview = ""
        if soul_path.exists():
            soul_preview = soul_path.read_text()[:200]
        description = ""
        env_hint = ""
        meta_path = d / "template.meta.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text())
                description = meta.get("description", "")
                env_hint = meta.get("env_hint", "")
            except (json.JSONDecodeError, OSError):
                pass
        config_preview = ""
        missing_vars: list[str] = []
        tmpl_path = d / "openclaw.template.json"
        if tmpl_path.exists():
            try:
                raw = tmpl_path.read_text()
                placeholders = re.findall(r"\{\{(\w+)\}\}", raw)
                missing_vars = sorted(set(v for v in placeholders if not os.environ.get(v)))
                if resolve_config:
                    resolved = _resolve_template(raw)
                    config_preview = json.dumps(json.loads(resolved), indent=2)
                else:
                    config_preview = raw.strip()
            except (json.JSONDecodeError, OSError):
                config_preview = ""
        templates.append({
            "name": d.name,
            "soul_preview": soul_preview,
            "description": description,
            "env_hint": env_hint,
            "config_preview": config_preview,
            "missing_vars": missing_vars,
        })
    return templates


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
def generate_config(name: str, extra_config: dict | None = None,
                    template: str = "default") -> dict:
    """Load and resolve an OpenClaw template, deep-merge extra_config."""
    tmpl_path = TEMPLATE_DIR / template / "openclaw.template.json"
    if not tmpl_path.exists():
        tmpl_path = TEMPLATE_DIR / "default" / "openclaw.template.json"
    raw = tmpl_path.read_text()
    resolved = _resolve_template(raw)
    config = json.loads(resolved)

    if extra_config:
        config = deep_merge(config, extra_config)

    return config


def write_bot_files(name: str, config: dict, soul: str | None = None,
                    forked_from: str | None = None, created_by: str | None = None,
                    template: str = "default", network_isolation: bool = True) -> Path:
    """Create bots/{name}/, write config.json + SOUL.md + .meta.json. Returns bot dir."""
    bot_dir = BOTS_DIR / name
    bot_dir.mkdir(parents=True, exist_ok=True)

    with open(bot_dir / "config.json", "w") as f:
        json.dump(config, f, indent=2)

    if soul and soul.strip():
        soul_text = soul
    else:
        # Look for SOUL.md in the template directory first, then fall back
        tmpl_soul = TEMPLATE_DIR / template / "SOUL.md"
        if tmpl_soul.exists():
            soul_text = tmpl_soul.read_text()
        else:
            default_soul = TEMPLATE_DIR / "default" / "SOUL.md"
            soul_text = default_soul.read_text() if default_soul.exists() else ""

    with open(bot_dir / "SOUL.md", "w") as f:
        f.write(soul_text)

    now = _now_iso()
    meta = {
        "created_at": now,
        "modified_at": now,
        "forked_from": forked_from,
        "created_by": created_by,
        "template": template,
        "network_isolation": network_isolation,
        "backups": [],
    }
    write_meta(name, meta)

    return bot_dir


# ---------------------------------------------------------------------------
# Container launcher (shared by create, duplicate, fork)
# ---------------------------------------------------------------------------
def _prepare_openclaw_home(bot_dir: Path, soul_text: str, trusted_proxies: list[str] | None = None,
                           bot_name: str | None = None, template_name: str = "default") -> Path:
    """Create .openclaw/ config dir for a bot so it's usable from the start.

    If a workspace already exists (e.g. copied from a source bot during
    duplicate/fork), its files are preserved — only SOUL.md is written
    if it doesn't already exist in the workspace.

    If openclaw.json already exists (duplicate/fork), it is used as the base
    config instead of the template — only ClawFarm-managed fields are re-applied.
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

    # Load base config: existing openclaw.json (duplicate/fork) or template
    existing_oc = oc_dir / "openclaw.json"
    if existing_oc.exists():
        try:
            oc_config = json.loads(existing_oc.read_text())
        except (json.JSONDecodeError, OSError):
            oc_config = {}
    else:
        tmpl_path = TEMPLATE_DIR / template_name / "openclaw.template.json"
        if not tmpl_path.exists():
            tmpl_path = TEMPLATE_DIR / "default" / "openclaw.template.json"
        if tmpl_path.exists():
            raw = tmpl_path.read_text()
            resolved = _resolve_template(raw)
            oc_config = json.loads(resolved)
        else:
            oc_config = {}

    # Deep-merge ClawFarm-managed fields on top
    in_compose = bool(os.environ.get("HOST_BOTS_DIR"))
    managed: dict = {
        "gateway": {
            "controlUi": {
                "dangerouslyAllowHostHeaderOriginFallback": True,
                **({"basePath": f"/claw/{bot_name}"} if os.environ.get("HOST_BOTS_DIR") else {}),
            },
            "trustedProxies": trusted_proxies or ["127.0.0.1"],
        },
    }
    if in_compose:
        managed["gateway"]["auth"] = {
            "mode": "trusted-proxy",
            "trustedProxy": {"userHeader": "X-Forwarded-User"},
        }

    brave_api_key = os.environ.get("BRAVE_API_KEY", "")
    if brave_api_key:
        managed["tools"] = {"web": {"search": {"provider": "brave", "apiKey": brave_api_key}}}

    oc_config = deep_merge(oc_config, managed)

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


def _effective_status(container) -> str:
    """Return health-aware status for a container.

    Docker status: created, running, exited, paused, dead, ...
    Health status (when healthcheck configured): starting, healthy, unhealthy

    Mapping:
      running + starting  -> "starting"
      running + healthy   -> "running"
      running + unhealthy -> "unhealthy"
      running + no health -> "running"  (backward compat: containers without healthcheck)
      anything else       -> container.status as-is
    """
    if container.status != "running":
        return container.status
    try:
        container.reload()
        health = container.attrs.get("State", {}).get("Health", {}).get("Status")
    except Exception:
        return container.status
    if health == "starting":
        return "starting"
    if health == "unhealthy":
        return "unhealthy"
    return "running"


def _launch_container(name: str, bot_dir: Path, template: str = "default",
                      network_isolation: bool = True) -> dict:
    """Allocate port, create network, start container. Returns bot info."""
    port = allocate_port()
    client = _get_client()
    network_name = f"openclaw-net-{name}"
    try:
        client.networks.create(network_name, driver="bridge")
    except docker.errors.APIError:
        pass  # Network already exists (e.g. container recreation)

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
                                    bot_name=name, template_name=template)

    # Forward provider API keys so OpenClaw's built-in providers can detect them.
    # MiniMax/Qwen keys are baked into openclaw.json via template placeholders instead.
    _provider_env_keys = ["ANTHROPIC_API_KEY", "OPENAI_API_KEY"]
    container_env = {k: v for k in _provider_env_keys if (v := os.environ.get(k))}

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
        environment=container_env,
        network=network_name,
        restart_policy={"Name": "unless-stopped"},
        healthcheck={
            "Test": ["CMD", "wget", "-qO-", "--spider",
                     f"http://localhost:18789/claw/{name}/" if in_compose else "http://localhost:18789/"],
            "Interval": 3_000_000_000,       # 3s (nanoseconds)
            "Timeout": 2_000_000_000,        # 2s
            "StartPeriod": 90_000_000_000,   # 90s grace (gateway takes ~60s to boot)
            "Retries": 3,                    # 3 consecutive failures after start_period -> unhealthy
        },
    )

    _sync_caddy_config_async()

    if network_isolation and in_compose:
        _apply_network_isolation(client, network_name, name)

    return {
        "name": name,
        "status": _effective_status(container),
        "port": port,
        "container_name": container_name,
        "ui_path": f"/claw/{name}/" if os.environ.get("HOST_BOTS_DIR") else None,
    }


# ---------------------------------------------------------------------------
# Bot lifecycle
# ---------------------------------------------------------------------------
def create_bot(name: str, soul: str | None = None, extra_config: dict | None = None,
               created_by: str | None = None, template: str = "default",
               network_isolation: bool = True) -> dict:
    """Full orchestration: sanitize, gen config, write files, launch container."""
    name = sanitize_name(name)
    if (BOTS_DIR / name).exists():
        raise ValueError(f"Bot already exists: {name!r}")
    config = generate_config(name, extra_config, template=template)
    bot_dir = write_bot_files(name, config, soul, created_by=created_by, template=template,
                              network_isolation=network_isolation)
    return _launch_container(name, bot_dir, template=template, network_isolation=network_isolation)


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
    src_meta = read_meta(name)
    isolation = src_meta.get("network_isolation", True)

    bot_dir = write_bot_files(new_name, config, soul, forked_from=None, created_by=created_by,
                              network_isolation=isolation)
    _copy_workspace(src_dir, bot_dir)
    return _launch_container(new_name, bot_dir, network_isolation=isolation)


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
    src_meta = read_meta(name)
    isolation = src_meta.get("network_isolation", True)

    bot_dir = write_bot_files(new_name, config, soul, forked_from=name, created_by=created_by,
                              network_isolation=isolation)
    _copy_workspace(src_dir, bot_dir)
    result = _launch_container(new_name, bot_dir, network_isolation=isolation)
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

        # Compute uptime for running containers
        uptime_seconds = 0
        started_at = None
        if c.status == "running":
            raw = c.attrs.get("State", {}).get("StartedAt", "")
            if raw:
                try:
                    start = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                    started_at = raw
                    uptime_seconds = int((datetime.now(timezone.utc) - start).total_seconds())
                except (ValueError, TypeError):
                    pass

        bots.append({
            "name": name,
            "status": _effective_status(c),
            "port": int(c.labels.get("openclaw.port", 0)),
            "container_name": c.name,
            "forked_from": meta.get("forked_from"),
            "created_by": meta.get("created_by"),
            "created_at": meta.get("created_at"),
            "template": meta.get("template"),
            "network_isolation": meta.get("network_isolation", True),
            "backup_count": len(meta.get("backups", [])),
            "storage_bytes": get_bot_storage(name),
            "cron_jobs": get_bot_cron_jobs(name),
            "token_usage": get_bot_token_usage(name),
            "uptime_seconds": uptime_seconds,
            "started_at": started_at,
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

    # Remove network isolation rules before tearing down the network
    meta = read_meta(name)
    if meta.get("network_isolation", True):
        _remove_network_isolation(client, network_name, name)

    _disconnect_caddy_from_network(client, network_name)

    try:
        network = client.networks.get(network_name)
        network.remove()
    except docker.errors.NotFound:
        pass

    bot_dir = BOTS_DIR / name
    if bot_dir.exists():
        shutil.rmtree(bot_dir)

    _sync_caddy_config_async()

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


def get_bot_token_usage(name: str) -> dict:
    """Read aggregate token usage from the bot's sessions.json."""
    sessions_path = BOTS_DIR / name / ".openclaw" / "agents" / "main" / "sessions" / "sessions.json"
    if not sessions_path.exists():
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "context_tokens": 0, "model": None}
    try:
        data = json.loads(sessions_path.read_text())
        total_in = 0
        total_out = 0
        context_tokens = 0
        model = None
        for session in data.values():
            if not isinstance(session, dict):
                continue
            total_in += session.get("inputTokens", 0)
            total_out += session.get("outputTokens", 0)
            ctx = session.get("contextTokens", 0)
            if ctx > context_tokens:
                context_tokens = ctx
            if session.get("model"):
                model = session["model"]
        return {
            "input_tokens": total_in,
            "output_tokens": total_out,
            "total_tokens": total_in + total_out,
            "context_tokens": context_tokens,
            "model": model,
        }
    except (json.JSONDecodeError, OSError):
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "context_tokens": 0, "model": None}


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

    # CPU calculation — percentage of total CPU capacity (0–100%)
    cpu_delta = stats["cpu_stats"]["cpu_usage"]["total_usage"] - \
                stats["precpu_stats"]["cpu_usage"]["total_usage"]
    system_delta = stats["cpu_stats"]["system_cpu_usage"] - \
                   stats["precpu_stats"]["system_cpu_usage"]
    cpu_percent = (cpu_delta / system_delta * 100.0) if system_delta > 0 else 0.0

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


def _collect_bot_stats(c) -> dict:
    """Collect stats for a single bot container. Runs in thread pool."""
    name = c.labels.get("openclaw.name", "")
    token_usage = get_bot_token_usage(name)
    result = {
        "storage": get_bot_storage(name),
        "tokens": token_usage.get("total_tokens", 0),
        "token_model": token_usage.get("model"),
        "running": 0, "starting": 0,
        "cpu": 0.0, "mem": 0.0, "mem_limit": 0.0,
        "rx": 0.0, "tx": 0.0, "uptime": 0,
    }

    if c.status != "running":
        return result
    effective = _effective_status(c)
    if effective == "starting":
        result["starting"] = 1
    elif effective == "running":
        result["running"] = 1

    try:
        stats = c.stats(stream=False)
        attrs = c.attrs

        # CPU — percentage of total CPU capacity (0–100%)
        cpu_delta = stats["cpu_stats"]["cpu_usage"]["total_usage"] - \
                    stats["precpu_stats"]["cpu_usage"]["total_usage"]
        system_delta = stats["cpu_stats"]["system_cpu_usage"] - \
                       stats["precpu_stats"]["system_cpu_usage"]
        if system_delta > 0:
            result["cpu"] = cpu_delta / system_delta * 100.0

        # Memory
        result["mem"] = stats["memory_stats"].get("usage", 0) / (1024 * 1024)
        result["mem_limit"] = stats["memory_stats"].get("limit", 0) / (1024 * 1024)

        # Network
        networks = stats.get("networks", {})
        result["rx"] = sum(n.get("rx_bytes", 0) for n in networks.values()) / (1024 * 1024)
        result["tx"] = sum(n.get("tx_bytes", 0) for n in networks.values()) / (1024 * 1024)

        # Uptime
        started_at = attrs.get("State", {}).get("StartedAt", "")
        if started_at:
            try:
                start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                result["uptime"] = int((datetime.now(timezone.utc) - start).total_seconds())
            except (ValueError, TypeError):
                pass
    except Exception:
        pass

    return result


def get_fleet_stats(allowed_bots: set[str] | None = None) -> dict:
    """Aggregate stats across bot containers.

    Args:
        allowed_bots: If set, only include these bot names (RBAC filtering).
                      None means all bots (admin / wildcard user).
    """
    client = _get_client()
    containers = client.containers.list(all=True, filters={"label": "openclaw.bot=true"})

    if allowed_bots is not None:
        containers = [c for c in containers if c.labels.get("openclaw.name", "") in allowed_bots]

    if not containers:
        return {
            "total_bots": 0, "running_bots": 0, "starting_bots": 0,
            "total_cpu_percent": 0.0, "total_memory_mb": 0.0,
            "total_memory_limit_mb": 0.0, "total_storage_bytes": 0,
            "total_network_rx_mb": 0.0, "total_network_tx_mb": 0.0,
            "max_uptime_seconds": 0, "total_tokens_used": 0,
            "tokens_by_model": {},
        }

    with ThreadPoolExecutor(max_workers=min(len(containers), 8)) as pool:
        results = list(pool.map(_collect_bot_stats, containers))

    tokens_by_model: dict[str, int] = {}
    for r in results:
        model = r.get("token_model")
        tokens = r.get("tokens", 0)
        if model and tokens > 0:
            tokens_by_model[model] = tokens_by_model.get(model, 0) + tokens

    return {
        "total_bots": len(containers),
        "running_bots": sum(r["running"] for r in results),
        "starting_bots": sum(r["starting"] for r in results),
        "total_cpu_percent": round(sum(r["cpu"] for r in results), 2),
        "total_memory_mb": round(sum(r["mem"] for r in results), 1),
        "total_memory_limit_mb": round(sum(r["mem_limit"] for r in results), 1),
        "total_storage_bytes": sum(r["storage"] for r in results),
        "total_network_rx_mb": round(sum(r["rx"] for r in results), 2),
        "total_network_tx_mb": round(sum(r["tx"] for r in results), 2),
        "max_uptime_seconds": max((r["uptime"] for r in results), default=0),
        "total_tokens_used": sum(r["tokens"] for r in results),
        "tokens_by_model": tokens_by_model,
    }


def _redact_config(config: dict) -> dict:
    """Deep-copy config and replace API keys with '***'."""
    display = copy.deepcopy(config)
    for provider in display.get("models", {}).get("providers", {}).values():
        if isinstance(provider, dict) and "apiKey" in provider:
            provider["apiKey"] = "***"
    tools = display.get("tools", {})
    if isinstance(tools, dict):
        for tool_cat in tools.values():
            if isinstance(tool_cat, dict):
                for tool in tool_cat.values():
                    if isinstance(tool, dict) and "apiKey" in tool:
                        tool["apiKey"] = "***"
    gw_auth = display.get("gateway", {}).get("auth", {})
    if isinstance(gw_auth, dict) and "token" in gw_auth:
        gw_auth["token"] = "***"
    return display


def get_bot_detail(name: str) -> dict:
    """Full bot info: config content, soul content, metadata, stats."""
    name = sanitize_name(name)
    bot_dir = BOTS_DIR / name

    # Prefer openclaw.json (the real config) over dead config.json
    config = {}
    oc_json_path = bot_dir / ".openclaw" / "openclaw.json"
    if oc_json_path.exists():
        try:
            config = _redact_config(json.loads(oc_json_path.read_text()))
        except (json.JSONDecodeError, OSError):
            pass
    if not config and (bot_dir / "config.json").exists():
        config = json.loads((bot_dir / "config.json").read_text())

    soul = ""
    if (bot_dir / "SOUL.md").exists():
        soul = (bot_dir / "SOUL.md").read_text()

    gateway_token = get_gateway_token(name)

    meta = ensure_meta(name)

    # Try to get container info + stats
    try:
        client = _get_client()
        container = client.containers.get(f"openclaw-bot-{name}")
        status = _effective_status(container)
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
        "token_usage": get_bot_token_usage(name),
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
# Per-bot network isolation (iptables)
# ---------------------------------------------------------------------------
_IPTABLES_IMAGE = "clawfarm-iptables:local"


def _build_iptables_image(client) -> None:
    """Build the lightweight iptables utility image if not already present."""
    try:
        client.images.get(_IPTABLES_IMAGE)
    except docker.errors.ImageNotFound:
        dockerfile = io.BytesIO(b"FROM alpine:3.21\nRUN apk add --no-cache iptables\n")
        client.images.build(fileobj=dockerfile, tag=_IPTABLES_IMAGE, rm=True)


def _apply_network_isolation(client, network_name: str, bot_name: str) -> bool:
    """Apply iptables rules blocking LAN access for a bot's network. Returns True on success."""
    try:
        network = client.networks.get(network_name)
    except docker.errors.NotFound:
        return False

    network_id = network.id[:12]
    chain = f"CF-{bot_name[:25]}"
    llm_host = os.environ.get("LLM_HOST", "")
    llm_port = os.environ.get("LLM_PORT", "")

    llm_rule = ""
    if llm_host and llm_port:
        llm_rule = f"iptables -A {chain} -d {llm_host} -p tcp --dport {llm_port} -j RETURN"

    script = f"""
        iptables -N {chain} 2>/dev/null || iptables -F {chain}
        iptables -A {chain} -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
        {llm_rule}
        iptables -A {chain} -d 10.0.0.0/8 -j DROP
        iptables -A {chain} -d 172.16.0.0/12 -j DROP
        iptables -A {chain} -d 192.168.0.0/16 -j DROP
        iptables -A {chain} -j RETURN
        iptables -C DOCKER-USER -i br-{network_id} -j {chain} 2>/dev/null || \
        iptables -I DOCKER-USER -i br-{network_id} -j {chain}
    """

    try:
        client.containers.run(
            _IPTABLES_IMAGE,
            command=["sh", "-c", script],
            network_mode="host",
            cap_add=["NET_ADMIN"],
            remove=True,
        )
        return True
    except Exception as e:
        log.warning("Network isolation failed for %s: %s", bot_name, e)
        return False


def _remove_network_isolation(client, network_name: str, bot_name: str) -> bool:
    """Remove iptables rules for a bot's network. Returns True on success."""
    network_id = ""
    try:
        network = client.networks.get(network_name)
        network_id = network.id[:12]
    except docker.errors.NotFound:
        pass

    chain = f"CF-{bot_name[:25]}"

    # If we don't have a network_id, we can't remove the DOCKER-USER jump rule,
    # but we can still flush and delete the chain itself.
    delete_jump = f'iptables -D DOCKER-USER -i br-{network_id} -j {chain} 2>/dev/null || true' if network_id else "true"

    script = f"""
        {delete_jump}
        iptables -F {chain} 2>/dev/null || true
        iptables -X {chain} 2>/dev/null || true
    """

    try:
        client.containers.run(
            _IPTABLES_IMAGE,
            command=["sh", "-c", script],
            network_mode="host",
            cap_add=["NET_ADMIN"],
            remove=True,
        )
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Caddy reverse proxy sync
# ---------------------------------------------------------------------------
CADDY_ADMIN_URL = os.environ.get("CADDY_ADMIN_URL", "http://caddy:2019")
TLS_MODE = os.environ.get("TLS_MODE", "internal").lower()  # internal, acme, custom, off
DOMAIN = os.environ.get("DOMAIN", "")  # Required for acme mode
PORTAL_URL = os.environ.get("PORTAL_URL", "")  # e.g. "https://farm.example.com"
_CADDY_LISTEN_PORT = 8080  # Fixed internal container port — not user-configurable
CADDY_PORT = int(os.environ.get("CADDY_PORT", "8443"))  # External host-facing port

# Auto-derive PORTAL_URL in acme mode
if not PORTAL_URL and TLS_MODE == "acme" and DOMAIN:
    PORTAL_URL = f"https://{DOMAIN}"

if not PORTAL_URL and TLS_MODE in ("off", "acme"):
    log.warning(
        "TLS_MODE=%s but PORTAL_URL is not set. "
        "CORS will use wildcard origins and URL construction will fall back to "
        "dynamic host detection. Set PORTAL_URL for production deployments.",
        TLS_MODE,
    )


def _build_tls_config() -> tuple[list, dict, str]:
    """Build TLS connection policies, TLS app config, and scheme based on TLS_MODE.

    Returns (tls_connection_policies, tls_app_config, scheme).
    tls_connection_policies must be [{}] (not []) for HTTPS modes — Caddy
    requires this field on the server to activate TLS on the listener.
    """
    if TLS_MODE == "off":
        return [], {}, "http"
    elif TLS_MODE == "acme":
        email = os.environ.get("ACME_EMAIL", "")
        issuer = {"module": "acme"}
        if email:
            issuer["email"] = email
        policy = {"issuers": [issuer]}
        if DOMAIN:
            policy["subjects"] = [DOMAIN]
        return [{}], {"automation": {"policies": [policy]}}, "https"
    elif TLS_MODE == "custom":
        return (
            [{"certificate_selection": {"any_tag": ["cert0"]}}],
            {"certificates": {"load_files": [{
                "certificate": "/certs/cert.pem",
                "key": "/certs/key.pem",
                "tags": ["cert0"],
            }]}},
            "https",
        )
    else:  # "internal" — default
        # on_demand: true is required for port-only listeners (no hostname).
        # Without it, Caddy can't determine what hostname to put on the cert
        # and TLS handshakes fail silently.
        return [{}], {"automation": {"policies": [{
            "issuers": [{"module": "internal"}],
            "on_demand": True,
        }]}}, "https"


def _sync_caddy_config() -> None:
    """Push updated route config to Caddy's admin API.

    Builds a JSON config with:
    - Main server for dashboard + frontend + path-based bot routes
    - HTTP redirect server (when TLS is enabled)
    - forward_auth subrequests to /api/auth/verify for authentication

    TLS_MODE controls certificate handling:
    - internal (default): Caddy auto-generates a self-signed cert
    - acme: Let's Encrypt via DOMAIN
    - custom: Load user-provided cert files from /certs/
    - off: Plain HTTP (for use behind an upstream proxy)

    Bots are routed via /claw/{name}/ paths on the main port.

    Fails silently when Caddy is not reachable (dev mode).
    """
    try:
        import requests as _req

        client = _get_client()
        containers = client.containers.list(
            all=False, filters={"label": "openclaw.bot=true"}
        )

        caddy_port = _CADDY_LISTEN_PORT
        tls_policy, tls_app, scheme = _build_tls_config()

        # forward_auth handler: subrequest to dashboard's /api/auth/verify
        # PORTAL_URL already includes port if needed (e.g. "http://host:8080")
        login_url = (
            f"{PORTAL_URL}/login"
            if PORTAL_URL else
            f"{scheme}://{{http.request.host}}:{CADDY_PORT}/login"
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
                        "/api/health",
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
                        "/login", "/login/*", "/_next/*", "/favicon.ico", "/logo.svg",
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
        # OpenClaw serves with basePath=/claw/{name}, so no strip_path_prefix needed.
        # WebSocket URLs include basePath natively (upstream PR #30228).
        if os.environ.get("HOST_BOTS_DIR"):
            for c in containers:
                name = c.labels.get("openclaw.name", "")
                if not name:
                    continue
                container_name = f"openclaw-bot-{name}"
                bot_proxy = {"handler": "reverse_proxy", "upstreams": [{"dial": f"{container_name}:18789"}]}
                if AUTH_DISABLED:
                    fwd_user = {"handler": "headers", "request": {"set": {"X-Forwarded-User": ["dev"]}}}
                    handlers = [fwd_user, bot_proxy]
                else:
                    handlers = [
                        _forward_auth_handler(extra_headers={"X-Original-Bot": [name]}, redirect_on_fail=True),
                        bot_proxy,
                    ]
                main_routes.insert(-1, {
                    "match": [{"path": [f"/claw/{name}/*", f"/claw/{name}"]}],
                    "handle": handlers,
                })

        # Security response headers — matchless route (non-terminal middleware).
        # X-Frame-Options is SAMEORIGIN (not DENY) because the dashboard
        # legitimately iframes bot Control UI at the same origin.
        security_headers = {
            "X-Content-Type-Options": ["nosniff"],
            "X-Frame-Options": ["SAMEORIGIN"],
            "Referrer-Policy": ["strict-origin-when-cross-origin"],
            "Permissions-Policy": ["camera=(), microphone=(), geolocation=()"],
        }
        if TLS_MODE != "off":
            security_headers["Strict-Transport-Security"] = [
                "max-age=63072000; includeSubDomains"
            ]
        main_routes.insert(0, {
            "handle": [{
                "handler": "headers",
                "response": {"set": security_headers},
            }],
        })

        # Build main server config
        main_server = {
            "listen": [f":{caddy_port}"],
            "routes": main_routes,
        }
        if tls_policy:
            main_server["tls_connection_policies"] = tls_policy
        # In TLS_MODE=off, Caddy sits behind an upstream proxy (Traefik, nginx,
        # Cloudflare Tunnel). Trust private-range IPs so Caddy reads the real
        # client IP from X-Forwarded-For instead of replacing it.
        if TLS_MODE == "off":
            main_server["trusted_proxies"] = {
                "source": "static",
                "ranges": [
                    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
                    "127.0.0.0/8", "fc00::/7", "::1/128",
                ],
            }

        servers = {"main": main_server}

        # Add HTTP→HTTPS redirect server when TLS is enabled
        if TLS_MODE != "off":
            redirect_location = (
                f"{PORTAL_URL}{{http.request.uri}}"
                if PORTAL_URL else
                f"https://{{http.request.host}}:{CADDY_PORT}{{http.request.uri}}"
            )
            servers["redirect"] = {
                "listen": [":80"],
                "routes": [{
                    "handle": [{
                        "handler": "static_response",
                        "headers": {"Location": [redirect_location]},
                        "status_code": 302,
                    }],
                }],
            }

        apps = {"http": {"servers": servers}}
        if tls_app:
            apps["tls"] = tls_app

        # Note: Caddy disables origin enforcement when the admin API listens on
        # an open interface (":2019"), so the "origins" field is documentation only.
        # The real protection is Docker network isolation — port 2019 is not
        # published to the host, so only containers on the compose network can
        # reach the admin API.
        config = {
            "admin": {"listen": ":2019", "origins": ["dashboard"]},
            "apps": apps,
        }

        _req.post(
            f"{CADDY_ADMIN_URL}/load",
            json=config,
            headers={"Content-Type": "application/json"},
            timeout=5,
        )
    except Exception:
        pass  # Caddy not running (dev mode) — silently ignore


def _sync_caddy_config_async() -> None:
    """Fire-and-forget Caddy config sync in a background thread.

    Decouples the Caddy admin API POST from the API response path so that
    Docker network churn (ERR_NETWORK_CHANGED) doesn't hit the browser while
    the HTTP response is still in-flight.  A 1-second delay ensures the
    response has been flushed before Caddy reloads.  Safe because
    _sync_caddy_config() is idempotent (full-state reconciliation) and Caddy
    serialises admin requests internally.
    """
    import time as _time

    def _delayed_sync():
        _time.sleep(1)
        _sync_caddy_config()

    threading.Thread(target=_delayed_sync, daemon=True,
                     name="caddy-sync").start()


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
# Housekeeping scheduler (session + rate-limit cleanup)
# ---------------------------------------------------------------------------
_housekeeping_stop_event = threading.Event()
_HOUSEKEEPING_INTERVAL = 1800  # 30 minutes


def _housekeeping_scheduler():
    """Periodically clean up expired sessions and stale rate-limit entries."""
    while not _housekeeping_stop_event.wait(_HOUSEKEEPING_INTERVAL):
        try:
            _cleanup_expired_sessions()
            _cleanup_stale_rate_limits()
        except Exception:
            pass


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
    if AUTH_DISABLED:
        log.warning(
            "\n"
            "  ┌─────────────────────────────────────────────┐\n"
            "  │  WARNING: AUTH_DISABLED=true                 │\n"
            "  │  All endpoints are accessible without login. │\n"
            "  │  Do NOT use this setting in production.      │\n"
            "  └─────────────────────────────────────────────┘"
        )
    else:
        _bootstrap_admin()
    # Check backup directory writability early so admins see the problem in logs
    if BACKUP_DIR:
        try:
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            _test = BACKUP_DIR / ".write_test"
            _test.touch()
            _test.unlink()
        except PermissionError:
            log.error(
                "BACKUP_DIR %s is not writable by UID %d — "
                "backups will fail. Fix with: chown %d:%d %s",
                BACKUP_DIR, os.getuid(), os.getuid(), os.getgid(), BACKUP_DIR,
            )
    # Ensure Caddy is connected to all existing bot networks on startup
    if os.environ.get("HOST_BOTS_DIR"):
        try:
            client = _get_client()
            # Build iptables utility image for network isolation
            try:
                _build_iptables_image(client)
            except Exception:
                log.warning("Failed to build iptables utility image — network isolation unavailable")
            containers = client.containers.list(
                all=True, filters={"label": "openclaw.bot=true"}
            )
            for c in containers:
                name = c.labels.get("openclaw.name", "")
                if name:
                    _connect_caddy_to_network(client, f"openclaw-net-{name}")
            # Re-apply network isolation rules (handles host reboot where iptables rules are lost)
            for c in containers:
                name = c.labels.get("openclaw.name", "")
                if name:
                    meta = read_meta(name)
                    if meta.get("network_isolation", True):
                        _apply_network_isolation(client, f"openclaw-net-{name}", name)
        except Exception:
            pass
        # Migrate existing bots: ensure basePath is set (native basePath routing)
        for bot_dir in BOTS_DIR.iterdir():
            if not bot_dir.is_dir() or bot_dir.name.startswith("."):
                continue
            oc_cfg = bot_dir / ".openclaw" / "openclaw.json"
            if not oc_cfg.exists():
                continue
            try:
                cfg = json.loads(oc_cfg.read_text())
                expected = f"/claw/{bot_dir.name}"
                if cfg.get("gateway", {}).get("controlUi", {}).get("basePath") != expected:
                    cfg.setdefault("gateway", {}).setdefault("controlUi", {})["basePath"] = expected
                    oc_cfg.write_text(json.dumps(cfg, indent=2))
            except (json.JSONDecodeError, OSError):
                pass
    _sync_caddy_config()
    # Start housekeeping scheduler (session + rate-limit cleanup every 30 min)
    _housekeeping_stop_event.clear()
    threading.Thread(target=_housekeeping_scheduler, daemon=True, name="housekeeping").start()
    # Start backup scheduler thread
    if BACKUP_INTERVAL_SECONDS > 0:
        _backup_stop_event.clear()
        t = threading.Thread(target=_backup_scheduler, daemon=True, name="backup-scheduler")
        t.start()
    yield
    _housekeeping_stop_event.set()
    _backup_stop_event.set()


_in_compose = bool(os.environ.get("HOST_BOTS_DIR"))
app = FastAPI(
    title="ClawFarm",
    lifespan=_lifespan,
    docs_url=None if _in_compose else "/docs",
    redoc_url=None if _in_compose else "/redoc",
    openapi_url=None if _in_compose else "/openapi.json",
)

_cors_origins: list[str] = ["*"]
_cors_credentials = False
if PORTAL_URL:
    _cors_origins = [PORTAL_URL.rstrip("/")]
    _cors_credentials = True
elif _in_compose and TLS_MODE != "off":
    log.warning("PORTAL_URL not set — CORS allows all origins. Set PORTAL_URL for production.")

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
    template: str = "default"
    network_isolation: bool = True


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
    # Secure=True in compose mode when TLS is on OR behind an HTTPS upstream proxy
    secure = bool(os.environ.get("HOST_BOTS_DIR")) and (
        TLS_MODE != "off" or PORTAL_URL.startswith("https://")
    )
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
    # Always run bcrypt to prevent timing-based user enumeration
    valid = _verify_password(req.password, user["password_hash"] if user else _DUMMY_HASH)
    if not user or not valid:
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


# --- Health ---

@app.get("/api/health")
async def api_health():
    """Minimal health check — no auth required, no system info leaked."""
    return {"ok": True}


# --- Config ---

@app.get("/api/config")
async def api_config(session: dict = Depends(_require_session)):
    """Return public configuration for the frontend."""
    return {
        "portal_url": PORTAL_URL or None,
        "caddy_port": CADDY_PORT,
        "tls_mode": TLS_MODE,
    }


# --- Templates ---

@app.get("/api/templates")
async def api_list_templates(session: dict = Depends(_require_session)):
    """List available bot templates."""
    is_admin = session.get("role") == "admin"
    return list_templates(resolve_config=is_admin)


# --- Fleet stats ---

@app.get("/api/fleet/stats")
async def api_fleet_stats(session: dict = Depends(_require_session)):
    try:
        allowed = None
        if session["role"] != "admin" and "*" not in session.get("bots", []):
            allowed = set(session.get("bots", []))
        return get_fleet_stats(allowed_bots=allowed)
    except Exception as e:
        log.warning("fleet_stats failed: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")


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
                            created_by=session["username"], template=req.template,
                            network_isolation=req.network_isolation)
        _grant_bot_to_user(session["username"], result["name"])
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except (RuntimeError, PermissionError) as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        log.warning("create_bot failed: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/api/bots/{name}/start")
async def api_start_bot(name: str, ctx: dict = Depends(_require_bot_access)):
    name = ctx["_bot_name"]
    client = _get_client()
    try:
        container = client.containers.get(f"openclaw-bot-{name}")
        container.start()
        _sync_caddy_config_async()
        return {"name": name, "status": _effective_status(container)}
    except docker.errors.NotFound:
        # Container gone but bot dir exists — recreate with current image
        bot_dir = BOTS_DIR / name
        if not bot_dir.exists():
            raise HTTPException(status_code=404, detail=f"Bot {name!r} not found")
        meta = read_meta(name)
        template = meta.get("template", "default")
        isolation = meta.get("network_isolation", True)
        return _launch_container(name, bot_dir, template=template, network_isolation=isolation)


@app.post("/api/bots/{name}/stop")
async def api_stop_bot(name: str, ctx: dict = Depends(_require_bot_access)):
    name = ctx["_bot_name"]
    client = _get_client()
    try:
        container = client.containers.get(f"openclaw-bot-{name}")
        container.stop()
        _sync_caddy_config_async()
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
        return {"name": name, "status": _effective_status(container)}
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
        log.warning("duplicate_bot failed: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")


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
        log.warning("fork_bot failed: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")


# --- Backup & Rollback ---

@app.post("/api/bots/{name}/backup")
async def api_create_backup(name: str, ctx: dict = Depends(_require_bot_access)):
    name = ctx["_bot_name"]
    try:
        return create_backup(name)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=503, detail=f"Backup directory not writable: {e}")


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
    # In compose mode (trusted-proxy auth), device approval is unnecessary —
    # Caddy handles all auth via X-Forwarded-User.  The CLI can't connect
    # to the gateway anyway because it lacks the trusted-proxy header.
    if os.environ.get("HOST_BOTS_DIR"):
        return {"approved": 0, "message": "trusted-proxy mode, device approval not needed"}

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
