import threading

import config
import docker_utils
from auth import _cleanup_expired_sessions, _cleanup_stale_rate_limits
from backup import create_backup, prune_scheduled_backups

# ---------------------------------------------------------------------------
# Housekeeping scheduler (session + rate-limit cleanup)
# ---------------------------------------------------------------------------
_housekeeping_stop_event = threading.Event()


def _housekeeping_scheduler():
    """Periodically clean up expired sessions and stale rate-limit entries."""
    while not _housekeeping_stop_event.wait(config._HOUSEKEEPING_INTERVAL):
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
    interval = config.BACKUP_INTERVAL_SECONDS
    if interval <= 0:
        return
    while not _backup_stop_event.wait(interval):
        try:
            client = docker_utils._get_client()
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
