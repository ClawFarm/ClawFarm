import config
from auth import _require_session
from fastapi import APIRouter, Depends, HTTPException
from templates import list_templates

from bots import get_fleet_stats

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
