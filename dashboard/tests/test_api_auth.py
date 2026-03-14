import pytest

import config
from auth import _bootstrap_admin


class TestAuthAPI:
    @pytest.fixture(autouse=True)
    def setup(self, auth_env, monkeypatch):
        """Set up test client and seed admin user."""
        from fastapi.testclient import TestClient

        from app import app
        monkeypatch.setenv("ADMIN_PASSWORD", "admin-pass")
        _bootstrap_admin()
        self.client = TestClient(app)
        self.auth_env = auth_env

    def _login_client(self, username="admin", password="admin-pass"):
        """Login and return a fresh client with the session cookie set."""
        from fastapi.testclient import TestClient

        from app import app
        c = TestClient(app)
        r = c.post("/api/auth/login", json={"username": username, "password": password})
        assert r.status_code == 200
        # Transfer the cookie to the client's cookie jar
        token = r.cookies.get("cfm_session")
        if token:
            c.cookies.set("cfm_session", token)
        return c

    def test_login_sets_cookie(self):
        r = self.client.post("/api/auth/login", json={"username": "admin", "password": "admin-pass"})
        assert r.status_code == 200
        assert "cfm_session" in r.cookies

    def test_login_wrong_password(self):
        r = self.client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
        assert r.status_code == 401

    def test_verify_returns_x_user(self):
        c = self._login_client()
        r = c.get("/api/auth/verify")
        assert r.status_code == 200
        assert r.headers.get("x-forwarded-user") == "admin"

    def test_verify_without_cookie(self):
        r = self.client.get("/api/auth/verify")
        assert r.status_code == 401

    def test_me_returns_user_info(self):
        c = self._login_client()
        r = c.get("/api/auth/me")
        assert r.status_code == 200
        data = r.json()
        assert data["username"] == "admin"
        assert data["role"] == "admin"

    def test_logout_clears_session(self):
        c = self._login_client()
        c.post("/api/auth/logout")
        r = c.get("/api/auth/me")
        assert r.status_code == 401

    def test_create_user(self):
        c = self._login_client()
        r = c.post("/api/auth/users", json={
            "username": "alice", "password": "alice-pass", "role": "user", "bots": ["bot-a"],
        })
        assert r.status_code == 200
        assert r.json()["username"] == "alice"

    def test_list_users(self):
        c = self._login_client()
        c.post("/api/auth/users", json={
            "username": "bob", "password": "bob-pass", "role": "user", "bots": [],
        })
        r = c.get("/api/auth/users")
        assert r.status_code == 200
        names = [u["username"] for u in r.json()]
        assert "admin" in names
        assert "bob" in names

    def test_update_user(self):
        c = self._login_client()
        c.post("/api/auth/users", json={
            "username": "carol", "password": "p", "role": "user", "bots": [],
        })
        r = c.put("/api/auth/users/carol", json={
            "role": "admin", "bots": ["*"],
        })
        assert r.status_code == 200
        assert r.json()["role"] == "admin"

    def test_delete_user(self):
        c = self._login_client()
        c.post("/api/auth/users", json={
            "username": "dave", "password": "p", "role": "user", "bots": [],
        })
        r = c.delete("/api/auth/users/dave")
        assert r.status_code == 200
        assert r.json()["deleted"] == "dave"

    def test_cannot_delete_self(self):
        c = self._login_client()
        r = c.delete("/api/auth/users/admin")
        assert r.status_code == 400

    def test_cannot_delete_last_admin(self):
        c = self._login_client()
        # Create another admin
        c.post("/api/auth/users", json={
            "username": "admin2", "password": "p", "role": "admin", "bots": ["*"],
        })
        # Login as admin2 and delete admin (OK -- admin2 is still an admin)
        c2 = self._login_client("admin2", "p")
        r = c2.delete("/api/auth/users/admin")
        assert r.status_code == 200
        # Now try to delete admin2 (the last admin) -- should fail (can't delete self)
        r2 = c2.delete("/api/auth/users/admin2")
        assert r2.status_code == 400

    def test_non_admin_cannot_list_users(self):
        c = self._login_client()
        c.post("/api/auth/users", json={
            "username": "eve", "password": "eve-pass", "role": "user", "bots": ["*"],
        })
        c2 = self._login_client("eve", "eve-pass")
        r = c2.get("/api/auth/users")
        assert r.status_code == 403

    def test_verify_with_bot_name_rbac(self):
        """Test that verify checks per-bot RBAC when X-Original-Bot is set."""
        c = self._login_client()
        c.post("/api/auth/users", json={
            "username": "limited", "password": "lp", "role": "user", "bots": ["bot-a"],
        })
        c2 = self._login_client("limited", "lp")
        # Access allowed bot
        r = c2.get("/api/auth/verify", headers={"X-Original-Bot": "bot-a"})
        assert r.status_code == 200
        # Access denied bot
        r2 = c2.get("/api/auth/verify", headers={"X-Original-Bot": "bot-b"})
        assert r2.status_code == 403


class TestRBACEndpoints:
    @pytest.fixture(autouse=True)
    def setup(self, auth_env, monkeypatch):
        from fastapi.testclient import TestClient

        from app import app
        monkeypatch.setenv("ADMIN_PASSWORD", "admin-pass")
        _bootstrap_admin()
        self.client = TestClient(app)

    def _login_client(self, username="admin", password="admin-pass"):
        from fastapi.testclient import TestClient

        from app import app
        c = TestClient(app)
        r = c.post("/api/auth/login", json={"username": username, "password": password})
        token = r.cookies.get("cfm_session")
        if token:
            c.cookies.set("cfm_session", token)
        return c

    def test_list_bots_requires_auth(self):
        r = self.client.get("/api/bots")
        assert r.status_code == 401

    def test_fleet_stats_requires_auth(self):
        r = self.client.get("/api/fleet/stats")
        assert r.status_code == 401

    def test_change_password(self):
        c = self._login_client()
        r = c.post("/api/auth/change-password", json={
            "current_password": "admin-pass",
            "new_password": "new-pass",
        })
        assert r.status_code == 200
        # Old password should fail
        r2 = self.client.post("/api/auth/login", json={"username": "admin", "password": "admin-pass"})
        assert r2.status_code == 401
        # New password should work
        r3 = self.client.post("/api/auth/login", json={"username": "admin", "password": "new-pass"})
        assert r3.status_code == 200

    def test_change_password_wrong_current(self):
        c = self._login_client()
        r = c.post("/api/auth/change-password", json={
            "current_password": "wrong",
            "new_password": "new-pass",
        })
        assert r.status_code == 401

    def test_change_password_empty(self):
        c = self._login_client()
        r = c.post("/api/auth/change-password", json={
            "current_password": "admin-pass",
            "new_password": "   ",
        })
        assert r.status_code == 400

    def test_role_validation_rejects_invalid(self):
        """Test that creating a user with an invalid role returns 422."""
        c = self._login_client()
        r = c.post("/api/auth/users", json={
            "username": "test", "password": "pass", "role": "superadmin",
        })
        assert r.status_code == 422

    def test_role_validation_accepts_valid(self):
        """Test that valid roles 'admin' and 'user' are accepted."""
        c = self._login_client()
        r1 = c.post("/api/auth/users", json={
            "username": "u1", "password": "pass", "role": "user",
        })
        assert r1.status_code == 200
        r2 = c.post("/api/auth/users", json={
            "username": "u2", "password": "pass", "role": "admin",
        })
        assert r2.status_code == 200

    def test_login_rate_limiting_endpoint(self):
        """Test that 5+ rapid failed logins return 429."""
        from fastapi.testclient import TestClient

        from app import app
        c = TestClient(app)
        for _ in range(5):
            c.post("/api/auth/login", json={"username": "nobody", "password": "wrong"})
        r = c.post("/api/auth/login", json={"username": "nobody", "password": "wrong"})
        assert r.status_code == 429

    def test_config_requires_auth(self):
        """Test that /api/config requires authentication."""
        r = self.client.get("/api/config")
        assert r.status_code == 401


class TestSessionCookieSecureFlag:
    @pytest.fixture(autouse=True)
    def setup(self, auth_env, monkeypatch):
        from fastapi.testclient import TestClient

        from app import app
        monkeypatch.setenv("ADMIN_PASSWORD", "admin-pass")
        _bootstrap_admin()
        self.client = TestClient(app)
        self.monkeypatch = monkeypatch

    def _login_and_get_cookie_header(self):
        r = self.client.post("/api/auth/login", json={"username": "admin", "password": "admin-pass"})
        assert r.status_code == 200
        return r.headers.get("set-cookie", "")

    def test_compose_tls_on_sets_secure(self):
        """Compose mode + TLS enabled -> Secure flag set."""
        self.monkeypatch.setenv("HOST_BOTS_DIR", "/host/bots")
        self.monkeypatch.setattr(config, "TLS_MODE", "internal")
        self.monkeypatch.setattr(config, "PORTAL_URL", "")
        cookie = self._login_and_get_cookie_header()
        assert "secure" in cookie.lower()

    def test_compose_tls_off_with_https_portal_sets_secure(self):
        """Compose mode + TLS off + PORTAL_URL=https -> Secure flag set."""
        self.monkeypatch.setenv("HOST_BOTS_DIR", "/host/bots")
        self.monkeypatch.setattr(config, "TLS_MODE", "off")
        self.monkeypatch.setattr(config, "PORTAL_URL", "https://farm.example.com")
        cookie = self._login_and_get_cookie_header()
        assert "secure" in cookie.lower()

    def test_compose_tls_off_without_https_portal_no_secure(self):
        """Compose mode + TLS off + no HTTPS PORTAL_URL -> no Secure flag."""
        self.monkeypatch.setenv("HOST_BOTS_DIR", "/host/bots")
        self.monkeypatch.setattr(config, "TLS_MODE", "off")
        self.monkeypatch.setattr(config, "PORTAL_URL", "")
        cookie = self._login_and_get_cookie_header()
        assert "secure" not in cookie.lower()

    def test_dev_mode_no_secure(self):
        """Dev mode (no HOST_BOTS_DIR) -> no Secure flag regardless of TLS mode."""
        self.monkeypatch.delenv("HOST_BOTS_DIR", raising=False)
        self.monkeypatch.setattr(config, "TLS_MODE", "internal")
        self.monkeypatch.setattr(config, "PORTAL_URL", "")
        cookie = self._login_and_get_cookie_header()
        assert "secure" not in cookie.lower()
