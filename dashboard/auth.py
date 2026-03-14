import json
import os
import secrets
import tempfile
import threading
import time

import bcrypt
import config
from fastapi import Cookie, Depends, HTTPException
from utils import sanitize_name

# ---------------------------------------------------------------------------
# Session store (in-memory)
# ---------------------------------------------------------------------------
SESSIONS: dict[str, dict] = {}  # token -> {username, role, bots, created_at}


def _users_file_path():
    """Return the resolved path to users.json."""
    env = os.environ.get("USERS_FILE", "")
    if env:
        return __import__("pathlib").Path(env)
    return config.BOTS_DIR / ".users.json"


def _load_users() -> dict:
    """Load users from JSON file. Returns {username: {password_hash, role, bots}}."""
    path = _users_file_path()
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_users(users: dict) -> None:
    """Atomically write users to JSON file."""
    path = _users_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(users, f, indent=2)
        os.replace(tmp, str(path))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()


# Dummy hash used to burn constant time when user doesn't exist (prevents timing enumeration)
_DUMMY_HASH = bcrypt.hashpw(b"dummy", bcrypt.gensalt(rounds=12)).decode()


def _verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except Exception:
        return False


def _bootstrap_admin() -> None:
    """Create default admin user if users file is empty or missing."""
    users = _load_users()
    if users:
        return
    password = os.environ.get("ADMIN_PASSWORD", "")
    generated = False
    if not password:
        password = secrets.token_urlsafe(16)
        generated = True
    users[config.ADMIN_USER] = {
        "password_hash": _hash_password(password),
        "role": "admin",
        "bots": ["*"],
    }
    _save_users(users)
    if generated:
        print("")
        print("\u2554" + "\u2550" * 50 + "\u2557")
        print("\u2551  ClawFarm \u2014 First Run Setup" + " " * 22 + "\u2551")
        print("\u2551" + " " * 50 + "\u2551")
        print(f"\u2551  Admin user:  {config.ADMIN_USER:<36}\u2551")
        print(f"\u2551  Password:    {password:<36}\u2551")
        print("\u2551" + " " * 50 + "\u2551")
        print("\u2551  Save this \u2014 it won't be shown again." + " " * 12 + "\u2551")
        print("\u255a" + "\u2550" * 50 + "\u255d")
        print("")
    else:
        print(f"[AUTH] Created admin user: {config.ADMIN_USER}")


def _create_session(username: str) -> str:
    """Create a new session token for a user. Returns the token."""
    token = secrets.token_urlsafe(32)
    users = _load_users()
    user = users.get(username, {})
    SESSIONS[token] = {
        "username": username,
        "role": user.get("role", "user"),
        "bots": user.get("bots", []),
        "created_at": time.time(),
    }
    return token


def _get_session(token: str) -> dict | None:
    """Validate a session token. Re-reads users.json for current RBAC. Returns None if invalid."""
    session = SESSIONS.get(token)
    if not session:
        return None
    # Check expiry
    if time.time() - session["created_at"] > config.SESSION_TTL:
        SESSIONS.pop(token, None)
        return None
    # Re-read user data for always-current permissions
    users = _load_users()
    user = users.get(session["username"])
    if not user:
        # User was deleted
        SESSIONS.pop(token, None)
        return None
    # Update session with current permissions
    session["role"] = user.get("role", "user")
    session["bots"] = user.get("bots", [])
    return session


def _cleanup_expired_sessions() -> int:
    """Remove all expired sessions. Returns count removed."""
    now = time.time()
    expired = [t for t, s in SESSIONS.items() if now - s["created_at"] > config.SESSION_TTL]
    for t in expired:
        del SESSIONS[t]
    return len(expired)


_login_attempts: dict[str, list[float]] = {}
_login_lock = threading.Lock()


def _cleanup_stale_rate_limits() -> int:
    """Remove stale entries from _login_attempts. Returns count removed."""
    now = time.time()
    with _login_lock:
        stale = [ip for ip, ts in _login_attempts.items() if all(now - t >= 300 for t in ts)]
        for ip in stale:
            del _login_attempts[ip]
    return len(stale)


def _invalidate_user_sessions(username: str) -> int:
    """Remove all sessions for a given user. Returns count removed."""
    to_remove = [t for t, s in SESSIONS.items() if s["username"] == username]
    for t in to_remove:
        del SESSIONS[t]
    return len(to_remove)


def _check_login_rate(ip: str) -> None:
    now = time.time()
    with _login_lock:
        attempts = [t for t in _login_attempts.get(ip, []) if now - t < 300]
        _login_attempts[ip] = attempts
        if len(attempts) >= 5:
            raise HTTPException(status_code=429, detail="Too many login attempts. Try again later.")


def _record_failed_login(ip: str) -> None:
    with _login_lock:
        _login_attempts.setdefault(ip, []).append(time.time())


def _user_can_access_bot(session: dict, bot_name: str) -> bool:
    """Check if user's session grants access to a specific bot."""
    if session["role"] == "admin":
        return True
    bots = session.get("bots", [])
    if "*" in bots:
        return True
    return bot_name in bots


def _grant_bot_to_user(username: str, bot_name: str) -> None:
    """Add bot_name to a user's bots list (no-op for admins/wildcard)."""
    users = _load_users()
    user = users.get(username)
    if not user:
        return
    if user.get("role") == "admin":
        return
    bots = user.get("bots", [])
    if "*" in bots or bot_name in bots:
        return
    bots.append(bot_name)
    user["bots"] = bots
    _save_users(users)


def _require_session(cfm_session: str | None = Cookie(None)) -> dict:
    """FastAPI dependency: require a valid session. Returns session dict."""
    if config.AUTH_DISABLED:
        return {"username": "dev", "role": "admin", "bots": ["*"]}
    session = _get_session(cfm_session) if cfm_session else None
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return session


def _require_bot_access(name: str, session: dict = Depends(_require_session)) -> dict:
    """FastAPI dependency: require session + access to a specific bot."""
    sname = sanitize_name(name)
    if not _user_can_access_bot(session, sname):
        raise HTTPException(status_code=403, detail=f"Access denied to bot {sname!r}")
    return {**session, "_bot_name": sname}
