import json
import os
import threading
from contextlib import asynccontextmanager

import config
import docker_utils
from auth import _bootstrap_admin
from caddy import _connect_caddy_to_network, _sync_caddy_config
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from isolation import _apply_network_isolation, _build_iptables_image
from routes import all_routers
from scheduler import _backup_scheduler, _backup_stop_event, _housekeeping_scheduler, _housekeeping_stop_event
from utils import read_meta


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # Bootstrap admin user on first run
    if config.AUTH_DISABLED:
        config.log.warning(
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
    if config.BACKUP_DIR:
        try:
            config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            _test = config.BACKUP_DIR / ".write_test"
            _test.touch()
            _test.unlink()
        except PermissionError:
            config.log.error(
                "BACKUP_DIR %s is not writable by UID %d — "
                "backups will fail. Fix with: chown %d:%d %s",
                config.BACKUP_DIR, os.getuid(), os.getuid(), os.getgid(), config.BACKUP_DIR,
            )
    # Ensure Caddy is connected to all existing bot networks on startup
    if os.environ.get("HOST_BOTS_DIR"):
        try:
            client = docker_utils._get_client()
            # Build iptables utility image for network isolation
            try:
                _build_iptables_image(client)
            except Exception:
                config.log.warning("Failed to build iptables utility image — network isolation unavailable")
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
        for bot_dir in config.BOTS_DIR.iterdir():
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
    if config.BACKUP_INTERVAL_SECONDS > 0:
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
if config.PORTAL_URL:
    _cors_origins = [config.PORTAL_URL.rstrip("/")]
    _cors_credentials = True
elif _in_compose and config.TLS_MODE != "off":
    config.log.warning("PORTAL_URL not set — CORS allows all origins. Set PORTAL_URL for production.")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_cors_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

for router in all_routers:
    app.include_router(router)
