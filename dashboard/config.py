import logging
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger("clawfarm")

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

# ---------------------------------------------------------------------------
# Caddy reverse proxy
# ---------------------------------------------------------------------------
CADDY_CONTAINER = os.environ.get("CADDY_CONTAINER", "botfarm-caddy-1")
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

# ---------------------------------------------------------------------------
# Network isolation
# ---------------------------------------------------------------------------
_IPTABLES_IMAGE = "clawfarm-iptables:local"

# ---------------------------------------------------------------------------
# Housekeeping
# ---------------------------------------------------------------------------
_HOUSEKEEPING_INTERVAL = 1800  # 30 minutes
