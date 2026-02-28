import copy
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

import docker
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv()

# ---------------------------------------------------------------------------
# Path constants (overridable via env for Docker mount paths)
# ---------------------------------------------------------------------------
TEMPLATE_DIR = Path(os.environ.get("TEMPLATE_DIR", Path(__file__).resolve().parent.parent / "bot-template"))
BOTS_DIR = Path(os.environ.get("BOTS_DIR", Path(__file__).resolve().parent.parent / "bots"))

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
def create_backup(name: str, label: str = "manual") -> dict:
    """Copy current config.json + SOUL.md to .backups/{timestamp}/."""
    bot_dir = BOTS_DIR / name
    if not bot_dir.exists():
        raise FileNotFoundError(f"Bot directory not found: {name}")

    ts = _now_timestamp()
    backup_dir = bot_dir / ".backups" / ts
    backup_dir.mkdir(parents=True, exist_ok=True)

    config_src = bot_dir / "config.json"
    soul_src = bot_dir / "SOUL.md"
    if config_src.exists():
        shutil.copy2(config_src, backup_dir / "config.json")
    if soul_src.exists():
        shutil.copy2(soul_src, backup_dir / "SOUL.md")

    now = _now_iso()
    meta = ensure_meta(name)
    meta["backups"].append({"timestamp": ts, "created_at": now, "label": label})
    meta["modified_at"] = now
    write_meta(name, meta)

    return {"timestamp": ts, "created_at": now, "label": label}


def list_backups(name: str) -> list[dict]:
    """Return backup list from .meta.json."""
    meta = ensure_meta(name)
    return meta.get("backups", [])


def rollback_to_backup(name: str, timestamp: str) -> dict:
    """Auto-backup current state, then restore from backup."""
    bot_dir = BOTS_DIR / name
    if not bot_dir.exists():
        raise FileNotFoundError(f"Bot directory not found: {name}")

    backup_dir = bot_dir / ".backups" / timestamp
    if not backup_dir.exists():
        raise ValueError(f"Backup not found: {timestamp}")

    # Safety: auto-backup current state before overwriting
    create_backup(name, label="pre-rollback")

    # Restore files
    backup_config = backup_dir / "config.json"
    backup_soul = backup_dir / "SOUL.md"
    if backup_config.exists():
        shutil.copy2(backup_config, bot_dir / "config.json")
    if backup_soul.exists():
        shutil.copy2(backup_soul, bot_dir / "SOUL.md")

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
                    forked_from: str | None = None) -> Path:
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
        "backups": [],
    }
    write_meta(name, meta)

    return bot_dir


# ---------------------------------------------------------------------------
# Container launcher (shared by create, duplicate, fork)
# ---------------------------------------------------------------------------
def _prepare_openclaw_home(bot_dir: Path, soul_text: str) -> Path:
    """Create .openclaw/ config dir for a bot so it's usable from the start."""
    oc_dir = bot_dir / ".openclaw"
    oc_dir.mkdir(exist_ok=True)
    (oc_dir / "workspace").mkdir(exist_ok=True)

    # Write SOUL.md into workspace so OpenClaw picks up personality
    (oc_dir / "workspace" / "SOUL.md").write_text(soul_text)

    # Gateway config: register local model provider, set as default
    llm_model = os.environ.get("LLM_MODEL", "Qwen3.5-122B-A10B")
    llm_api_key = os.environ.get("LLM_API_KEY", "none")
    llm_base_url = os.environ.get("LLM_BASE_URL", "http://localhost:8000/v1")

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
            "controlUi": {"dangerouslyAllowHostHeaderOriginFallback": True},
        },
    }
    with open(oc_dir / "openclaw.json", "w") as f:
        json.dump(oc_config, f, indent=2)

    # Make everything writable by the container's node user
    for p in oc_dir.rglob("*"):
        p.chmod(0o777 if p.is_dir() else 0o666)
    oc_dir.chmod(0o777)

    return oc_dir


def _launch_container(name: str, bot_dir: Path) -> dict:
    """Allocate port, create network, start container. Returns bot info."""
    port = allocate_port()
    client = _get_client()
    network_name = f"openclaw-net-{name}"
    client.networks.create(network_name, driver="bridge")

    container_name = f"openclaw-bot-{name}"
    image = os.environ.get("OPENCLAW_IMAGE", "ghcr.io/openclaw/openclaw:latest")

    # Read soul text for OpenClaw workspace injection
    soul_path = bot_dir / "SOUL.md"
    soul_text = soul_path.read_text() if soul_path.exists() else ""
    oc_dir = _prepare_openclaw_home(bot_dir, soul_text)

    container = client.containers.run(
        image,
        name=container_name,
        detach=True,
        command=["node", "openclaw.mjs", "gateway", "--allow-unconfigured", "--bind", "lan"],
        ports={"18789/tcp": port},
        volumes={
            str(bot_dir.resolve()): {"bind": "/data", "mode": "rw"},
            str(oc_dir.resolve()): {"bind": "/home/node/.openclaw", "mode": "rw"},
        },
        labels={
            "openclaw.bot": "true",
            "openclaw.port": str(port),
            "openclaw.name": name,
        },
        network=network_name,
        restart_policy={"Name": "unless-stopped"},
    )

    return {
        "name": name,
        "status": container.status,
        "port": port,
        "container_name": container_name,
    }


# ---------------------------------------------------------------------------
# Bot lifecycle
# ---------------------------------------------------------------------------
def create_bot(name: str, soul: str | None = None, extra_config: dict | None = None) -> dict:
    """Full orchestration: sanitize, gen config, write files, launch container."""
    name = sanitize_name(name)
    config = generate_config(name, extra_config)
    bot_dir = write_bot_files(name, config, soul)
    return _launch_container(name, bot_dir)


def duplicate_bot(name: str, new_name: str) -> dict:
    """Copy a bot's actual config + soul to a new bot."""
    name = sanitize_name(name)
    new_name = sanitize_name(new_name)

    src_dir = BOTS_DIR / name
    if not src_dir.exists():
        raise FileNotFoundError(f"Source bot not found: {name}")
    if (BOTS_DIR / new_name).exists():
        raise FileExistsError(f"Bot already exists: {new_name}")

    config = json.loads((src_dir / "config.json").read_text())
    soul = (src_dir / "SOUL.md").read_text() if (src_dir / "SOUL.md").exists() else ""

    bot_dir = write_bot_files(new_name, config, soul, forked_from=None)
    return _launch_container(new_name, bot_dir)


def fork_bot(name: str, new_name: str) -> dict:
    """Fork a bot: copy config + soul, track lineage."""
    name = sanitize_name(name)
    new_name = sanitize_name(new_name)

    src_dir = BOTS_DIR / name
    if not src_dir.exists():
        raise FileNotFoundError(f"Source bot not found: {name}")
    if (BOTS_DIR / new_name).exists():
        raise FileExistsError(f"Bot already exists: {new_name}")

    config = json.loads((src_dir / "config.json").read_text())
    soul = (src_dir / "SOUL.md").read_text() if (src_dir / "SOUL.md").exists() else ""

    bot_dir = write_bot_files(new_name, config, soul, forked_from=name)
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
            "created_at": meta.get("created_at"),
            "backup_count": len(meta.get("backups", [])),
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

    try:
        network = client.networks.get(network_name)
        network.remove()
    except docker.errors.NotFound:
        pass

    bot_dir = BOTS_DIR / name
    if bot_dir.exists():
        shutil.rmtree(bot_dir)

    return {"deleted": name}


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

    # Read gateway token from openclaw.json so the UI can display it
    gateway_token = ""
    oc_config_path = bot_dir / ".openclaw" / "openclaw.json"
    if oc_config_path.exists():
        try:
            oc_cfg = json.loads(oc_config_path.read_text())
            gateway_token = oc_cfg.get("gateway", {}).get("auth", {}).get("token", "")
        except (json.JSONDecodeError, KeyError):
            pass

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
    }


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="ClawFleetManager")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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


# --- Bot CRUD ---

@app.get("/api/bots")
async def api_list_bots():
    return list_bots()


@app.post("/api/bots")
async def api_create_bot(req: CreateBotRequest):
    try:
        return create_bot(req.name, req.soul, req.extra_config)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except (RuntimeError, PermissionError) as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")


@app.post("/api/bots/{name}/start")
async def api_start_bot(name: str):
    name = sanitize_name(name)
    client = _get_client()
    try:
        container = client.containers.get(f"openclaw-bot-{name}")
        container.start()
        return {"name": name, "status": "running"}
    except docker.errors.NotFound:
        raise HTTPException(status_code=404, detail=f"Bot {name!r} not found")


@app.post("/api/bots/{name}/stop")
async def api_stop_bot(name: str):
    name = sanitize_name(name)
    client = _get_client()
    try:
        container = client.containers.get(f"openclaw-bot-{name}")
        container.stop()
        return {"name": name, "status": "stopped"}
    except docker.errors.NotFound:
        raise HTTPException(status_code=404, detail=f"Bot {name!r} not found")


@app.post("/api/bots/{name}/restart")
async def api_restart_bot(name: str):
    name = sanitize_name(name)
    client = _get_client()
    try:
        container = client.containers.get(f"openclaw-bot-{name}")
        container.restart()
        return {"name": name, "status": "running"}
    except docker.errors.NotFound:
        raise HTTPException(status_code=404, detail=f"Bot {name!r} not found")


@app.delete("/api/bots/{name}")
async def api_delete_bot(name: str):
    try:
        return delete_bot(name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/bots/{name}/logs")
async def api_bot_logs(name: str):
    name = sanitize_name(name)
    client = _get_client()
    try:
        container = client.containers.get(f"openclaw-bot-{name}")
        logs = container.logs(tail=200).decode("utf-8", errors="replace")
        return {"name": name, "logs": logs}
    except docker.errors.NotFound:
        raise HTTPException(status_code=404, detail=f"Bot {name!r} not found")


# --- Duplicate & Fork ---

@app.post("/api/bots/{name}/duplicate")
async def api_duplicate_bot(name: str, req: DuplicateRequest):
    try:
        return duplicate_bot(name, req.new_name)
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
async def api_fork_bot(name: str, req: ForkRequest):
    try:
        return fork_bot(name, req.new_name)
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
async def api_create_backup(name: str):
    name = sanitize_name(name)
    try:
        return create_backup(name)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/api/bots/{name}/backups")
async def api_list_backups(name: str):
    name = sanitize_name(name)
    bot_dir = BOTS_DIR / name
    if not bot_dir.exists():
        raise HTTPException(status_code=404, detail=f"Bot {name!r} not found")
    return list_backups(name)


@app.post("/api/bots/{name}/rollback")
async def api_rollback_bot(name: str, req: RollbackRequest):
    name = sanitize_name(name)
    try:
        result = rollback_to_backup(name, req.timestamp)
        # Restart the container so it picks up the restored config
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
async def api_bot_meta(name: str):
    name = sanitize_name(name)
    bot_dir = BOTS_DIR / name
    if not bot_dir.exists():
        raise HTTPException(status_code=404, detail=f"Bot {name!r} not found")
    return ensure_meta(name)


@app.get("/api/bots/{name}/stats")
async def api_bot_stats(name: str):
    try:
        return get_bot_stats(name)
    except docker.errors.NotFound:
        raise HTTPException(status_code=404, detail=f"Bot {name!r} not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/bots/{name}/detail")
async def api_bot_detail(name: str):
    try:
        return get_bot_detail(name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# --- Device pairing helpers ---

@app.post("/api/bots/{name}/approve-devices")
async def api_approve_devices(name: str):
    """Approve all pending device pairing requests for a bot."""
    name = sanitize_name(name)
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
