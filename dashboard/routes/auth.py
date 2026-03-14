import os

import config
from auth import (
    _DUMMY_HASH,
    SESSIONS,
    _check_login_rate,
    _create_session,
    _get_session,
    _hash_password,
    _invalidate_user_sessions,
    _load_users,
    _login_attempts,
    _login_lock,
    _record_failed_login,
    _require_session,
    _save_users,
    _user_can_access_bot,
    _verify_password,
)
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from models import ChangePasswordRequest, CreateUserRequest, LoginRequest, UpdateUserRequest

router = APIRouter()


def _set_session_cookie(response: Response, token: str) -> None:
    # Secure=True in compose mode when TLS is on OR behind an HTTPS upstream proxy
    secure = bool(os.environ.get("HOST_BOTS_DIR")) and (
        config.TLS_MODE != "off" or config.PORTAL_URL.startswith("https://")
    )
    response.set_cookie(
        "cfm_session", token,
        httponly=True, secure=secure, samesite="lax",
        path="/", max_age=config.SESSION_TTL,
    )


@router.post("/api/auth/login")
async def api_auth_login(req: LoginRequest, request: Request, response: Response):
    if config.AUTH_DISABLED:
        return {"ok": True, "username": "dev", "role": "admin"}
    ip = request.client.host if request.client else "unknown"
    _check_login_rate(ip)
    users = _load_users()
    user = users.get(req.username)
    # Always run bcrypt to prevent timing-based user enumeration
    valid = _verify_password(req.password, user["password_hash"] if user else _DUMMY_HASH)
    if not user or not valid:
        _record_failed_login(ip)
        raise HTTPException(status_code=401, detail="Invalid username or password")
    # Clear failed attempts on success
    with _login_lock:
        _login_attempts.pop(ip, None)
    token = _create_session(req.username)
    _set_session_cookie(response, token)
    return {"ok": True, "username": req.username, "role": user["role"]}


@router.post("/api/auth/logout")
async def api_auth_logout(response: Response, cfm_session: str | None = Cookie(None)):
    if cfm_session:
        SESSIONS.pop(cfm_session, None)
    response.delete_cookie("cfm_session", path="/")
    return {"ok": True}


@router.get("/api/auth/verify")
async def api_auth_verify(request: Request, response: Response,
                          cfm_session: str | None = Cookie(None)):
    """Caddy forward_auth endpoint. Returns 200 + X-Forwarded-User header or 401."""
    if config.AUTH_DISABLED:
        return Response(status_code=200, headers={"X-Forwarded-User": "dev"})

    session = _get_session(cfm_session) if cfm_session else None
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")

    # Per-bot RBAC: check if user can access this bot (via X-Original-Bot)
    original_bot = request.headers.get("X-Original-Bot")
    if original_bot:
        if not _user_can_access_bot(session, original_bot):
            raise HTTPException(status_code=403, detail="Access denied to this bot")

    return Response(status_code=200, headers={"X-Forwarded-User": session["username"]})


@router.get("/api/auth/me")
async def api_auth_me(cfm_session: str | None = Cookie(None)):
    """Return current user info for the frontend."""
    if config.AUTH_DISABLED:
        return {"username": "dev", "role": "admin", "bots": ["*"]}
    session = _get_session(cfm_session) if cfm_session else None
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return {
        "username": session["username"],
        "role": session["role"],
        "bots": session["bots"],
    }


@router.post("/api/auth/change-password")
async def api_auth_change_password(req: ChangePasswordRequest,
                                    session: dict = Depends(_require_session)):
    """Allow any user to change their own password."""
    users = _load_users()
    user = users.get(session["username"])
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not _verify_password(req.current_password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Current password is incorrect")
    if not req.new_password.strip():
        raise HTTPException(status_code=400, detail="New password cannot be empty")
    user["password_hash"] = _hash_password(req.new_password)
    _save_users(users)
    _invalidate_user_sessions(session["username"])
    return {"ok": True}


@router.get("/api/auth/users")
async def api_auth_list_users(cfm_session: str | None = Cookie(None)):
    """List all users (admin only). Password hashes are excluded."""
    if config.AUTH_DISABLED:
        return []
    session = _get_session(cfm_session) if cfm_session else None
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if session["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    users = _load_users()
    return [
        {"username": name, "role": u["role"], "bots": u.get("bots", [])}
        for name, u in users.items()
    ]


@router.post("/api/auth/users")
async def api_auth_create_user(req: CreateUserRequest,
                               cfm_session: str | None = Cookie(None)):
    """Create a new user (admin only)."""
    if config.AUTH_DISABLED:
        raise HTTPException(status_code=400, detail="Auth is disabled")
    session = _get_session(cfm_session) if cfm_session else None
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if session["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    users = _load_users()
    if req.username in users:
        raise HTTPException(status_code=409, detail="User already exists")
    if not req.username.strip():
        raise HTTPException(status_code=400, detail="Username cannot be empty")
    if not req.password.strip():
        raise HTTPException(status_code=400, detail="Password cannot be empty")
    users[req.username] = {
        "password_hash": _hash_password(req.password),
        "role": req.role,
        "bots": req.bots,
    }
    _save_users(users)
    return {"username": req.username, "role": req.role, "bots": req.bots}


@router.put("/api/auth/users/{username}")
async def api_auth_update_user(username: str, req: UpdateUserRequest,
                               cfm_session: str | None = Cookie(None)):
    """Update a user (admin only). Invalidates their sessions on change."""
    if config.AUTH_DISABLED:
        raise HTTPException(status_code=400, detail="Auth is disabled")
    session = _get_session(cfm_session) if cfm_session else None
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if session["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    users = _load_users()
    if username not in users:
        raise HTTPException(status_code=404, detail="User not found")

    # Prevent demoting the last admin
    if req.role and req.role != "admin" and users[username]["role"] == "admin":
        admin_count = sum(1 for u in users.values() if u["role"] == "admin")
        if admin_count <= 1:
            raise HTTPException(status_code=400, detail="Cannot demote the last admin")

    user = users[username]
    if req.password is not None and req.password.strip():
        user["password_hash"] = _hash_password(req.password)
    if req.role is not None:
        user["role"] = req.role
    if req.bots is not None:
        user["bots"] = req.bots
    _save_users(users)
    _invalidate_user_sessions(username)
    return {"username": username, "role": user["role"], "bots": user.get("bots", [])}


@router.delete("/api/auth/users/{username}")
async def api_auth_delete_user(username: str,
                               cfm_session: str | None = Cookie(None)):
    """Delete a user (admin only). Can't delete self or last admin."""
    if config.AUTH_DISABLED:
        raise HTTPException(status_code=400, detail="Auth is disabled")
    session = _get_session(cfm_session) if cfm_session else None
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if session["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    users = _load_users()
    if username not in users:
        raise HTTPException(status_code=404, detail="User not found")
    if username == session["username"]:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")
    if users[username]["role"] == "admin":
        admin_count = sum(1 for u in users.values() if u["role"] == "admin")
        if admin_count <= 1:
            raise HTTPException(status_code=400, detail="Cannot delete the last admin")
    _invalidate_user_sessions(username)
    del users[username]
    _save_users(users)
    return {"deleted": username}
