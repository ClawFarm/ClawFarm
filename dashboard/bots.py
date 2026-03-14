import copy
import json
import os
import shutil
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import docker

import caddy
import config
import docker_utils
from isolation import _apply_network_isolation, _remove_network_isolation
from templates import _resolve_template, generate_config, write_bot_files
from utils import deep_merge, read_meta, sanitize_name


# ---------------------------------------------------------------------------
# Port allocation
# ---------------------------------------------------------------------------
def allocate_port() -> int:
    """Find first free port in configured range by scanning labeled containers."""
    start = int(os.environ.get("BOT_PORT_START", 3001))
    end = int(os.environ.get("BOT_PORT_END", 3100))
    client = docker_utils._get_client()
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
        tmpl_path = config.TEMPLATE_DIR / template_name / "openclaw.template.json"
        if not tmpl_path.exists():
            tmpl_path = config.TEMPLATE_DIR / "default" / "openclaw.template.json"
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


def _launch_container(name: str, bot_dir: Path, template: str = "default",
                      network_isolation: bool = True) -> dict:
    """Allocate port, create network, start container. Returns bot info."""
    port = allocate_port()
    client = docker_utils._get_client()
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
        caddy._connect_caddy_to_network(client, network_name)
        caddy_ip = caddy._get_caddy_ip_on_network(client, network_name)
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
            docker_utils._host_path(bot_dir): {"bind": "/data", "mode": "rw"},
            docker_utils._host_path(oc_dir): {"bind": "/home/node/.openclaw", "mode": "rw"},
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

    caddy._sync_caddy_config_async()

    if network_isolation and in_compose:
        _apply_network_isolation(client, network_name, name)

    return {
        "name": name,
        "status": docker_utils._effective_status(container),
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
    if (config.BOTS_DIR / name).exists():
        raise ValueError(f"Bot already exists: {name!r}")
    cfg = generate_config(name, extra_config, template=template)
    bot_dir = write_bot_files(name, cfg, soul, created_by=created_by, template=template,
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

    src_dir = config.BOTS_DIR / name
    if not src_dir.exists():
        raise FileNotFoundError(f"Source bot not found: {name}")
    if (config.BOTS_DIR / new_name).exists():
        raise FileExistsError(f"Bot already exists: {new_name}")

    cfg = json.loads((src_dir / "config.json").read_text())
    soul = (src_dir / "SOUL.md").read_text() if (src_dir / "SOUL.md").exists() else ""
    src_meta = read_meta(name)
    isolation = src_meta.get("network_isolation", True)

    bot_dir = write_bot_files(new_name, cfg, soul, forked_from=None, created_by=created_by,
                              network_isolation=isolation)
    _copy_workspace(src_dir, bot_dir)
    return _launch_container(new_name, bot_dir, network_isolation=isolation)


def fork_bot(name: str, new_name: str, created_by: str | None = None) -> dict:
    """Fork a bot: copy full identity + memories, track lineage."""
    name = sanitize_name(name)
    new_name = sanitize_name(new_name)

    src_dir = config.BOTS_DIR / name
    if not src_dir.exists():
        raise FileNotFoundError(f"Source bot not found: {name}")
    if (config.BOTS_DIR / new_name).exists():
        raise FileExistsError(f"Bot already exists: {new_name}")

    cfg = json.loads((src_dir / "config.json").read_text())
    soul = (src_dir / "SOUL.md").read_text() if (src_dir / "SOUL.md").exists() else ""
    src_meta = read_meta(name)
    isolation = src_meta.get("network_isolation", True)

    bot_dir = write_bot_files(new_name, cfg, soul, forked_from=name, created_by=created_by,
                              network_isolation=isolation)
    _copy_workspace(src_dir, bot_dir)
    result = _launch_container(new_name, bot_dir, network_isolation=isolation)
    result["forked_from"] = name
    return result


def list_bots() -> list[dict]:
    """Query containers by label, merge metadata."""
    client = docker_utils._get_client()
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
            "status": docker_utils._effective_status(c),
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
    client = docker_utils._get_client()
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

    caddy._disconnect_caddy_from_network(client, network_name)

    try:
        network = client.networks.get(network_name)
        network.remove()
    except docker.errors.NotFound:
        pass

    bot_dir = config.BOTS_DIR / name
    if bot_dir.exists():
        shutil.rmtree(bot_dir)

    caddy._sync_caddy_config_async()

    return {"deleted": name}


# ---------------------------------------------------------------------------
# Bot storage & cron
# ---------------------------------------------------------------------------
def get_bot_storage(name: str) -> int:
    """Return total disk usage in bytes for a bot directory."""
    bot_dir = config.BOTS_DIR / name
    if not bot_dir.exists():
        return 0
    return sum(f.stat().st_size for f in bot_dir.rglob("*") if f.is_file())


def get_bot_cron_jobs(name: str) -> list[dict]:
    """Read cron jobs from the bot's .openclaw/cron/jobs.json."""
    cron_path = config.BOTS_DIR / name / ".openclaw" / "cron" / "jobs.json"
    if not cron_path.exists():
        return []
    try:
        data = json.loads(cron_path.read_text())
        return data.get("jobs", [])
    except (json.JSONDecodeError, OSError):
        return []


def get_bot_token_usage(name: str) -> dict:
    """Read aggregate token usage from the bot's sessions.json."""
    sessions_path = config.BOTS_DIR / name / ".openclaw" / "agents" / "main" / "sessions" / "sessions.json"
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
    oc_path = config.BOTS_DIR / name / ".openclaw" / "openclaw.json"
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
    client = docker_utils._get_client()
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
    effective = docker_utils._effective_status(c)
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
    client = docker_utils._get_client()
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


def _redact_config(cfg: dict) -> dict:
    """Deep-copy config and replace API keys with '***'."""
    display = copy.deepcopy(cfg)
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
    bot_dir = config.BOTS_DIR / name

    # Prefer openclaw.json (the real config) over dead config.json
    cfg = {}
    oc_json_path = bot_dir / ".openclaw" / "openclaw.json"
    if oc_json_path.exists():
        try:
            cfg = _redact_config(json.loads(oc_json_path.read_text()))
        except (json.JSONDecodeError, OSError):
            pass
    if not cfg and (bot_dir / "config.json").exists():
        cfg = json.loads((bot_dir / "config.json").read_text())

    soul = ""
    if (bot_dir / "SOUL.md").exists():
        soul = (bot_dir / "SOUL.md").read_text()

    gateway_token = get_gateway_token(name)

    from utils import ensure_meta
    meta = ensure_meta(name)

    # Try to get container info + stats
    try:
        client = docker_utils._get_client()
        container = client.containers.get(f"openclaw-bot-{name}")
        status = docker_utils._effective_status(container)
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
        "config": cfg,
        "soul": soul,
        "meta": meta,
        "stats": stats,
        "gateway_token": gateway_token,
        "storage_bytes": get_bot_storage(name),
        "cron_jobs": get_bot_cron_jobs(name),
        "token_usage": get_bot_token_usage(name),
        "ui_path": f"/claw/{name}/" if os.environ.get("HOST_BOTS_DIR") else None,
    }
