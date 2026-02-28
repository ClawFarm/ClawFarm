import copy
import json
import os
import re
import shutil
from pathlib import Path

import docker
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
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


def write_bot_files(name: str, config: dict, soul: str | None = None) -> Path:
    """Create bots/{name}/, write config.json + SOUL.md. Returns bot dir."""
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

    return bot_dir


# ---------------------------------------------------------------------------
# Bot lifecycle
# ---------------------------------------------------------------------------
def create_bot(name: str, soul: str | None = None, extra_config: dict | None = None) -> dict:
    """Full orchestration: sanitize, allocate port, gen config, write files, create network, start container."""
    name = sanitize_name(name)
    port = allocate_port()
    config = generate_config(name, extra_config)
    bot_dir = write_bot_files(name, config, soul)

    client = _get_client()
    network_name = f"openclaw-net-{name}"
    network = client.networks.create(network_name, driver="bridge")

    container_name = f"openclaw-bot-{name}"
    image = os.environ.get("OPENCLAW_IMAGE", "ghcr.io/openclaw/openclaw:latest")

    container = client.containers.run(
        image,
        name=container_name,
        detach=True,
        ports={"3000/tcp": port},
        volumes={str(bot_dir.resolve()): {"bind": "/data", "mode": "rw"}},
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


def list_bots() -> list[dict]:
    """Query containers by label."""
    client = _get_client()
    containers = client.containers.list(all=True, filters={"label": "openclaw.bot=true"})
    bots = []
    for c in containers:
        bots.append({
            "name": c.labels.get("openclaw.name", ""),
            "status": c.status,
            "port": int(c.labels.get("openclaw.port", 0)),
            "container_name": c.name,
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
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="OpenClaw Fleet Manager")
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))


class CreateBotRequest(BaseModel):
    name: str
    soul: str | None = None
    extra_config: dict | None = None


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api/bots")
async def api_list_bots():
    return list_bots()


@app.post("/api/bots")
async def api_create_bot(req: CreateBotRequest):
    try:
        return create_bot(req.name, req.soul, req.extra_config)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


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
