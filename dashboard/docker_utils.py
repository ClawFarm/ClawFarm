import os
from pathlib import Path

import docker

import config

# ---------------------------------------------------------------------------
# Lazy Docker client
# ---------------------------------------------------------------------------
_client = None


def _get_client():
    global _client
    if _client is None:
        _client = docker.from_env()
    return _client


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
    rel = container_path.resolve().relative_to(config.BOTS_DIR.resolve())
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
