from fastapi import APIRouter, Depends, HTTPException

import config
import docker_utils
from auth import _require_session
from bots import get_fleet_stats
from templates import list_templates
from token_history import get_fleet_token_chart, get_sparkline_data

router = APIRouter()


@router.get("/api/health")
async def api_health():
    """Minimal health check — no auth required, no system info leaked."""
    return {"ok": True}


@router.get("/api/config")
async def api_config(session: dict = Depends(_require_session)):
    """Return public configuration for the frontend."""
    return {
        "portal_url": config.PORTAL_URL or None,
        "caddy_port": config.CADDY_PORT,
        "tls_mode": config.TLS_MODE,
    }


@router.get("/api/templates")
async def api_list_templates(session: dict = Depends(_require_session)):
    """List available bot templates."""
    is_admin = session.get("role") == "admin"
    return list_templates(resolve_config=is_admin)


@router.get("/api/fleet/stats")
async def api_fleet_stats(session: dict = Depends(_require_session)):
    try:
        allowed = None
        if session["role"] != "admin" and "*" not in session.get("bots", []):
            allowed = set(session.get("bots", []))
        return get_fleet_stats(allowed_bots=allowed)
    except Exception as e:
        config.log.warning("fleet_stats failed: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/api/fleet/sparklines")
async def api_fleet_sparklines(session: dict = Depends(_require_session)):
    """Bulk sparkline data for all accessible bots."""
    try:
        client = docker_utils._get_client()
        containers = client.containers.list(all=True, filters={"label": "openclaw.bot=true"})
        names = [c.labels.get("openclaw.name", "") for c in containers if c.labels.get("openclaw.name")]
        # RBAC filter
        if session["role"] != "admin" and "*" not in session.get("bots", []):
            allowed = set(session.get("bots", []))
            names = [n for n in names if n in allowed]
        return {name: get_sparkline_data(name) for name in names}
    except Exception as e:
        config.log.warning("fleet_sparklines failed: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/api/fleet/token-chart")
async def api_fleet_token_chart(session: dict = Depends(_require_session)):
    """Hourly token usage by model for the fleet chart (7 days)."""
    allowed = None
    if session["role"] != "admin" and "*" not in session.get("bots", []):
        allowed = set(session.get("bots", []))
    return get_fleet_token_chart(allowed_bots=allowed)
