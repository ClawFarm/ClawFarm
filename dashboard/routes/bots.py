import json
import os

import docker
from fastapi import APIRouter, Depends, HTTPException

import bots as bots_mod
import caddy
import config
import docker_utils
from auth import _grant_bot_to_user, _require_bot_access, _require_session
from backup import create_backup, list_backups, rollback_to_backup
from models import CloneRequest, CreateBotRequest, DuplicateRequest, ForkRequest, RollbackRequest
from token_history import get_sparkline_data
from utils import ensure_meta, read_meta

router = APIRouter()


@router.get("/api/bots")
async def api_list_bots(session: dict = Depends(_require_session)):
    bots = bots_mod.list_bots()
    if session["role"] != "admin" and "*" not in session.get("bots", []):
        allowed = set(session.get("bots", []))
        bots = [b for b in bots if b["name"] in allowed]
    return bots


@router.post("/api/bots")
async def api_create_bot(req: CreateBotRequest, session: dict = Depends(_require_session)):
    try:
        result = bots_mod.create_bot(req.name, req.soul, req.extra_config,
                            created_by=session["username"], template=req.template,
                            network_isolation=req.network_isolation)
        _grant_bot_to_user(session["username"], result["name"])
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except (RuntimeError, PermissionError) as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        config.log.warning("create_bot failed: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/api/bots/{name}/start")
async def api_start_bot(name: str, ctx: dict = Depends(_require_bot_access)):
    name = ctx["_bot_name"]
    client = docker_utils._get_client()
    try:
        container = client.containers.get(f"openclaw-bot-{name}")
        container.start()
        caddy._sync_caddy_config_async()
        return {"name": name, "status": docker_utils._effective_status(container)}
    except docker.errors.NotFound:
        # Container gone but bot dir exists — recreate with current image
        bot_dir = config.BOTS_DIR / name
        if not bot_dir.exists():
            raise HTTPException(status_code=404, detail=f"Bot {name!r} not found")
        meta = read_meta(name)
        template = meta.get("template", "default")
        isolation = meta.get("network_isolation", True)
        return bots_mod._launch_container(name, bot_dir, template=template, network_isolation=isolation)


@router.post("/api/bots/{name}/stop")
async def api_stop_bot(name: str, ctx: dict = Depends(_require_bot_access)):
    name = ctx["_bot_name"]
    client = docker_utils._get_client()
    try:
        container = client.containers.get(f"openclaw-bot-{name}")
        container.stop()
        caddy._sync_caddy_config_async()
        return {"name": name, "status": "stopped"}
    except docker.errors.NotFound:
        raise HTTPException(status_code=404, detail=f"Bot {name!r} not found")


@router.post("/api/bots/{name}/restart")
async def api_restart_bot(name: str, ctx: dict = Depends(_require_bot_access)):
    name = ctx["_bot_name"]
    client = docker_utils._get_client()
    try:
        container = client.containers.get(f"openclaw-bot-{name}")
        container.restart()
        return {"name": name, "status": docker_utils._effective_status(container)}
    except docker.errors.NotFound:
        raise HTTPException(status_code=404, detail=f"Bot {name!r} not found")


@router.delete("/api/bots/{name}")
async def api_delete_bot(name: str, ctx: dict = Depends(_require_bot_access)):
    try:
        return bots_mod.delete_bot(ctx["_bot_name"])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/bots/{name}/logs")
async def api_bot_logs(name: str, ctx: dict = Depends(_require_bot_access)):
    name = ctx["_bot_name"]
    client = docker_utils._get_client()
    try:
        container = client.containers.get(f"openclaw-bot-{name}")
        logs = container.logs(tail=200).decode("utf-8", errors="replace")
        return {"name": name, "logs": logs}
    except docker.errors.NotFound:
        raise HTTPException(status_code=404, detail=f"Bot {name!r} not found")


# --- Duplicate & Fork ---

@router.post("/api/bots/{name}/duplicate")
async def api_duplicate_bot(name: str, req: DuplicateRequest,
                            ctx: dict = Depends(_require_bot_access)):
    try:
        result = bots_mod.duplicate_bot(ctx["_bot_name"], req.new_name,
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
        config.log.warning("duplicate_bot failed: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/api/bots/{name}/fork")
async def api_fork_bot(name: str, req: ForkRequest,
                       ctx: dict = Depends(_require_bot_access)):
    try:
        result = bots_mod.fork_bot(ctx["_bot_name"], req.new_name,
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
        config.log.warning("fork_bot failed: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")


# --- Clone (unified duplicate/fork) ---

@router.post("/api/bots/{name}/clone")
async def api_clone_bot(name: str, req: CloneRequest, ctx: dict = Depends(_require_bot_access)):
    try:
        if req.track_fork:
            result = bots_mod.fork_bot(ctx["_bot_name"], req.new_name, created_by=ctx["username"])
        else:
            result = bots_mod.duplicate_bot(ctx["_bot_name"], req.new_name, created_by=ctx["username"])
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
        config.log.warning("clone_bot failed: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")


# --- Sparkline ---

@router.get("/api/bots/{name}/sparkline")
async def api_bot_sparkline(name: str, ctx: dict = Depends(_require_bot_access)):
    return get_sparkline_data(ctx["_bot_name"])


# --- Backup & Rollback ---

@router.post("/api/bots/{name}/backup")
async def api_create_backup(name: str, ctx: dict = Depends(_require_bot_access)):
    name = ctx["_bot_name"]
    try:
        return create_backup(name)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=503, detail=f"Backup directory not writable: {e}")


@router.get("/api/bots/{name}/backups")
async def api_list_backups(name: str, ctx: dict = Depends(_require_bot_access)):
    name = ctx["_bot_name"]
    bot_dir = config.BOTS_DIR / name
    if not bot_dir.exists():
        raise HTTPException(status_code=404, detail=f"Bot {name!r} not found")
    return list_backups(name)


@router.post("/api/bots/{name}/rollback")
async def api_rollback_bot(name: str, req: RollbackRequest,
                           ctx: dict = Depends(_require_bot_access)):
    name = ctx["_bot_name"]
    try:
        result = rollback_to_backup(name, req.timestamp)
        client = docker_utils._get_client()
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

@router.get("/api/bots/{name}/meta")
async def api_bot_meta(name: str, ctx: dict = Depends(_require_bot_access)):
    name = ctx["_bot_name"]
    bot_dir = config.BOTS_DIR / name
    if not bot_dir.exists():
        raise HTTPException(status_code=404, detail=f"Bot {name!r} not found")
    return ensure_meta(name)


@router.get("/api/bots/{name}/stats")
async def api_bot_stats(name: str, ctx: dict = Depends(_require_bot_access)):
    try:
        return bots_mod.get_bot_stats(ctx["_bot_name"])
    except docker.errors.NotFound:
        raise HTTPException(status_code=404, detail=f"Bot {name!r} not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/bots/{name}/detail")
async def api_bot_detail(name: str, ctx: dict = Depends(_require_bot_access)):
    try:
        return bots_mod.get_bot_detail(ctx["_bot_name"])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# --- Device pairing helpers ---

@router.post("/api/bots/{name}/approve-devices")
async def api_approve_devices(name: str, ctx: dict = Depends(_require_bot_access)):
    """Approve all pending device pairing requests for a bot."""
    if os.environ.get("HOST_BOTS_DIR"):
        return {"approved": 0, "message": "trusted-proxy mode, device approval not needed"}

    name = ctx["_bot_name"]
    client = docker_utils._get_client()
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
