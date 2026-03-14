from unittest.mock import MagicMock

import pytest

import config
from caddy import _build_tls_config, _sync_caddy_config


class TestBuildTlsConfig:
    """Tests for _build_tls_config() TLS mode selection."""

    def test_internal_mode_default(self, monkeypatch):
        """Internal mode (default): Caddy auto-generates self-signed cert."""
        monkeypatch.setattr(config, "TLS_MODE", "internal")
        policies, tls_app, scheme = _build_tls_config()
        assert policies == [{}]  # empty policy activates TLS on listener
        assert scheme == "https"
        assert "automation" in tls_app
        policy = tls_app["automation"]["policies"][0]
        assert policy["issuers"][0]["module"] == "internal"
        assert policy["on_demand"] is True  # required for port-only listeners

    def test_custom_mode(self, monkeypatch):
        """Custom mode: load user-provided cert files."""
        monkeypatch.setattr(config, "TLS_MODE", "custom")
        policies, tls_app, scheme = _build_tls_config()
        assert len(policies) == 1
        assert policies[0]["certificate_selection"]["any_tag"] == ["cert0"]
        assert scheme == "https"
        load_files = tls_app["certificates"]["load_files"]
        assert load_files[0]["certificate"] == "/certs/cert.pem"
        assert load_files[0]["key"] == "/certs/key.pem"
        assert load_files[0]["tags"] == ["cert0"]

    def test_off_mode(self, monkeypatch):
        """Off mode: no TLS, plain HTTP."""
        monkeypatch.setattr(config, "TLS_MODE", "off")
        policies, tls_app, scheme = _build_tls_config()
        assert policies == []
        assert tls_app == {}
        assert scheme == "http"

    def test_acme_mode_with_domain(self, monkeypatch):
        """ACME mode: Let's Encrypt with domain."""
        monkeypatch.setattr(config, "TLS_MODE", "acme")
        monkeypatch.setattr(config, "DOMAIN", "farm.example.com")
        monkeypatch.setenv("ACME_EMAIL", "admin@example.com")
        policies, tls_app, scheme = _build_tls_config()
        assert policies == [{}]  # empty policy activates TLS on listener
        assert scheme == "https"
        policy = tls_app["automation"]["policies"][0]
        assert policy["subjects"] == ["farm.example.com"]
        assert policy["issuers"][0]["module"] == "acme"
        assert policy["issuers"][0]["email"] == "admin@example.com"

    def test_acme_mode_without_email(self, monkeypatch):
        """ACME mode without email: no email in issuer config."""
        monkeypatch.setattr(config, "TLS_MODE", "acme")
        monkeypatch.setattr(config, "DOMAIN", "farm.example.com")
        monkeypatch.delenv("ACME_EMAIL", raising=False)
        policies, tls_app, scheme = _build_tls_config()
        issuer = tls_app["automation"]["policies"][0]["issuers"][0]
        assert issuer == {"module": "acme"}

    def test_acme_mode_without_domain(self, monkeypatch):
        """ACME mode without domain: no subjects in policy."""
        monkeypatch.setattr(config, "TLS_MODE", "acme")
        monkeypatch.setattr(config, "DOMAIN", "")
        monkeypatch.delenv("ACME_EMAIL", raising=False)
        policies, tls_app, scheme = _build_tls_config()
        policy = tls_app["automation"]["policies"][0]
        assert "subjects" not in policy

    def test_unknown_mode_defaults_to_internal(self, monkeypatch):
        """Unknown TLS_MODE values fall back to internal."""
        monkeypatch.setattr(config, "TLS_MODE", "bogus")
        policies, tls_app, scheme = _build_tls_config()
        assert scheme == "https"
        policy = tls_app["automation"]["policies"][0]
        assert policy["issuers"][0]["module"] == "internal"
        assert policy["on_demand"] is True


class TestSyncCaddyConfig:
    """Tests for _sync_caddy_config() — validates the full Caddy JSON config
    pushed to Caddy's admin API under each TLS mode and auth state."""

    @pytest.fixture
    def caddy_env(self, monkeypatch):
        """Set up mocks for _sync_caddy_config: Docker client + requests.post.

        Returns a dict with:
        - captured: list that receives the JSON config posted to Caddy
        - mock_client: the mocked Docker client
        - add_bot(name): helper to add a running bot container to the mock
        """
        # Mock Docker client with empty container list by default
        mock_client = MagicMock()
        containers = []
        mock_client.containers.list.return_value = containers
        monkeypatch.setattr("docker_utils._get_client", lambda: mock_client)

        # Capture what gets POSTed to Caddy admin API
        # _sync_caddy_config does `import requests as _req` internally,
        # so we need to mock requests.post on the real module.
        import requests as _real_req
        captured = []

        def _capture_post(url, json=None, headers=None, timeout=None):
            captured.append(json)

        monkeypatch.setattr(_real_req, "post", _capture_post)

        # Enable compose mode (bot routes only added when HOST_BOTS_DIR is set)
        monkeypatch.setenv("HOST_BOTS_DIR", "/host/bots")

        # Defaults
        monkeypatch.setattr(config, "CADDY_ADMIN_URL", "http://caddy:2019")
        monkeypatch.setattr(config, "AUTH_DISABLED", False)
        monkeypatch.setattr(config, "PORTAL_URL", "")

        def add_bot(name):
            bot = MagicMock()
            bot.labels = {"openclaw.name": name, "openclaw.bot": "true"}
            containers.append(bot)

        return {"captured": captured, "mock_client": mock_client, "add_bot": add_bot}

    def test_internal_mode_no_bots(self, monkeypatch, caddy_env):
        """Internal mode with no bots: self-signed TLS + redirect server."""
        monkeypatch.setattr(config, "TLS_MODE", "internal")
        monkeypatch.setattr(config, "CADDY_PORT", 8443)

        _sync_caddy_config()

        assert len(caddy_env["captured"]) == 1
        caddy_cfg = caddy_env["captured"][0]

        # Admin API
        assert caddy_cfg["admin"]["listen"] == ":2019"

        # Main server listens on fixed internal port
        main = caddy_cfg["apps"]["http"]["servers"]["main"]
        assert main["listen"] == [":8080"]

        # TLS app: internal issuer with on_demand
        tls_app = caddy_cfg["apps"]["tls"]
        policy = tls_app["automation"]["policies"][0]
        assert policy["issuers"][0]["module"] == "internal"
        assert policy["on_demand"] is True

        # tls_connection_policies with empty policy activates TLS
        assert main["tls_connection_policies"] == [{}]

        # Redirect server exists (HTTP->HTTPS)
        redirect = caddy_cfg["apps"]["http"]["servers"]["redirect"]
        assert redirect["listen"] == [":80"]
        redir_loc = redirect["routes"][0]["handle"][0]["headers"]["Location"][0]
        assert "https://" in redir_loc
        assert ":8443" in redir_loc

    def test_internal_mode_with_bots(self, monkeypatch, caddy_env):
        """Internal mode with bots: bot routes inserted before catch-all."""
        monkeypatch.setattr(config, "TLS_MODE", "internal")
        monkeypatch.setattr(config, "CADDY_PORT", 8443)

        caddy_env["add_bot"]("alice")
        caddy_env["add_bot"]("bob")

        _sync_caddy_config()

        caddy_cfg = caddy_env["captured"][0]
        main = caddy_cfg["apps"]["http"]["servers"]["main"]
        routes = main["routes"]

        # Find bot routes by matching path patterns
        bot_routes = [r for r in routes if any(
            "/claw/" in p
            for m in r.get("match", [])
            for p in m.get("path", [])
        )]
        assert len(bot_routes) == 2

        bot_names_found = set()
        for r in bot_routes:
            for m in r["match"]:
                for p in m.get("path", []):
                    if p.startswith("/claw/"):
                        name = p.split("/")[2]
                        bot_names_found.add(name)
        assert bot_names_found == {"alice", "bob"}

        # Bot route handlers: forward_auth + proxy (basePath routing, no strip_prefix)
        for r in bot_routes:
            handlers = r["handle"]
            handler_types = [h["handler"] for h in handlers]
            # Auth enabled: forward_auth (reverse_proxy) + reverse_proxy (bot)
            assert "reverse_proxy" in handler_types

        # Bot routes are before the catch-all (last route)
        last_route = routes[-1]
        # Last route should be the catch-all frontend route (no "match" or broad match)
        assert "match" not in last_route or last_route["match"] == []

    def test_off_mode_no_redirect_server(self, monkeypatch, caddy_env):
        """Off mode: plain HTTP, no TLS app, no redirect server."""
        monkeypatch.setattr(config, "TLS_MODE", "off")
        monkeypatch.setattr(config, "CADDY_PORT", 8443)

        _sync_caddy_config()

        caddy_cfg = caddy_env["captured"][0]
        servers = caddy_cfg["apps"]["http"]["servers"]

        # Only "main" server, no "redirect"
        assert "redirect" not in servers
        assert "main" in servers

        # No TLS app
        assert "tls" not in caddy_cfg["apps"]

        # Main server on fixed internal port (independent of CADDY_PORT)
        assert servers["main"]["listen"] == [":8080"]

        # Off mode trusts private-range proxies for X-Forwarded-For
        tp = servers["main"]["trusted_proxies"]
        assert tp["source"] == "static"
        assert "10.0.0.0/8" in tp["ranges"]
        assert "172.16.0.0/12" in tp["ranges"]

    def test_custom_mode_tls_policies(self, monkeypatch, caddy_env):
        """Custom mode: cert file references in TLS config."""
        monkeypatch.setattr(config, "TLS_MODE", "custom")
        monkeypatch.setattr(config, "CADDY_PORT", 8443)

        _sync_caddy_config()

        caddy_cfg = caddy_env["captured"][0]
        main = caddy_cfg["apps"]["http"]["servers"]["main"]

        # tls_connection_policies with cert tag
        assert "tls_connection_policies" in main
        assert main["tls_connection_policies"][0]["certificate_selection"]["any_tag"] == ["cert0"]

        # TLS app with load_files
        tls_app = caddy_cfg["apps"]["tls"]
        load = tls_app["certificates"]["load_files"][0]
        assert load["certificate"] == "/certs/cert.pem"
        assert load["key"] == "/certs/key.pem"

        # Redirect server exists
        assert "redirect" in caddy_cfg["apps"]["http"]["servers"]

    def test_acme_mode_full_config(self, monkeypatch, caddy_env):
        """ACME mode: Let's Encrypt automation policy + domain subjects."""
        monkeypatch.setattr(config, "TLS_MODE", "acme")
        monkeypatch.setattr(config, "DOMAIN", "farm.example.com")
        monkeypatch.setenv("ACME_EMAIL", "admin@example.com")
        monkeypatch.setattr(config, "CADDY_PORT", 443)

        _sync_caddy_config()

        caddy_cfg = caddy_env["captured"][0]

        # TLS app has ACME automation
        tls_app = caddy_cfg["apps"]["tls"]
        policy = tls_app["automation"]["policies"][0]
        assert policy["issuers"][0]["module"] == "acme"
        assert policy["issuers"][0]["email"] == "admin@example.com"
        assert policy["subjects"] == ["farm.example.com"]

        # Main server on fixed internal port (CADDY_PORT=443 is external only)
        assert caddy_cfg["apps"]["http"]["servers"]["main"]["listen"] == [":8080"]

        # Redirect server (HTTPS enabled)
        assert "redirect" in caddy_cfg["apps"]["http"]["servers"]

    def test_auth_disabled_no_forward_auth(self, monkeypatch, caddy_env):
        """When AUTH_DISABLED, routes should NOT have forward_auth handlers."""
        monkeypatch.setattr(config, "TLS_MODE", "internal")
        monkeypatch.setattr(config, "AUTH_DISABLED", True)
        monkeypatch.setattr(config, "CADDY_PORT", 8443)

        caddy_env["add_bot"]("testbot")

        _sync_caddy_config()

        caddy_cfg = caddy_env["captured"][0]
        routes = caddy_cfg["apps"]["http"]["servers"]["main"]["routes"]

        # Auth disabled: simpler route structure (no forward_auth subrequests)
        # API route should be direct reverse_proxy, no auth check
        api_routes = [r for r in routes if any(
            "/api/*" in p for m in r.get("match", []) for p in m.get("path", [])
        )]
        assert len(api_routes) == 1
        # Should have exactly 1 handler (direct proxy), not 2 (auth + proxy)
        assert len(api_routes[0]["handle"]) == 1
        assert api_routes[0]["handle"][0]["handler"] == "reverse_proxy"

        # Bot route should have hardcoded X-Forwarded-User: dev
        bot_routes = [r for r in routes if any(
            "/claw/testbot" in p
            for m in r.get("match", [])
            for p in m.get("path", [])
        )]
        assert len(bot_routes) == 1
        handlers = bot_routes[0]["handle"]
        # First handler sets X-Forwarded-User to "dev"
        fwd_handler = handlers[0]
        assert fwd_handler["handler"] == "headers"
        assert fwd_handler["request"]["set"]["X-Forwarded-User"] == ["dev"]

    def test_bot_route_path_only_matching(self, monkeypatch, caddy_env):
        """Bot routes use path-only matching (basePath handles WebSocket natively)."""
        monkeypatch.setattr(config, "TLS_MODE", "internal")
        monkeypatch.setattr(config, "AUTH_DISABLED", True)  # simpler to inspect
        monkeypatch.setattr(config, "CADDY_PORT", 8443)

        caddy_env["add_bot"]("mybot")

        _sync_caddy_config()

        caddy_cfg = caddy_env["captured"][0]
        routes = caddy_cfg["apps"]["http"]["servers"]["main"]["routes"]

        bot_routes = [r for r in routes if any(
            "/claw/mybot" in p
            for m in r.get("match", [])
            for p in m.get("path", [])
        )]
        assert len(bot_routes) == 1
        match_clauses = bot_routes[0]["match"]

        # Single path-only match clause (no cookie/WS workaround)
        assert len(match_clauses) == 1
        assert "/claw/mybot/*" in match_clauses[0]["path"]
        assert "/claw/mybot" in match_clauses[0]["path"]
        assert "header_regexp" not in match_clauses[0]

    def test_portal_url_in_redirect(self, monkeypatch, caddy_env):
        """When PORTAL_URL is set, redirect and login URLs use it."""
        monkeypatch.setattr(config, "TLS_MODE", "internal")
        monkeypatch.setattr(config, "PORTAL_URL", "https://farm.example.com")
        monkeypatch.setattr(config, "CADDY_PORT", 8443)

        _sync_caddy_config()

        caddy_cfg = caddy_env["captured"][0]

        # Redirect server uses PORTAL_URL
        redirect = caddy_cfg["apps"]["http"]["servers"]["redirect"]
        redir_loc = redirect["routes"][0]["handle"][0]["headers"]["Location"][0]
        # PORTAL_URL is used directly (already includes port if needed)
        assert redir_loc.startswith("https://farm.example.com")

    def test_caddy_unreachable_fails_silently(self, monkeypatch):
        """When Caddy admin API is unreachable, _sync_caddy_config doesn't raise."""
        import requests as _real_req

        mock_client = MagicMock()
        mock_client.containers.list.return_value = []
        monkeypatch.setattr("docker_utils._get_client", lambda: mock_client)
        monkeypatch.setenv("HOST_BOTS_DIR", "/host/bots")
        monkeypatch.setattr(config, "TLS_MODE", "internal")
        monkeypatch.setattr(config, "AUTH_DISABLED", False)
        monkeypatch.setattr(config, "PORTAL_URL", "")
        monkeypatch.setattr(config, "CADDY_ADMIN_URL", "http://localhost:99999")

        # Mock requests.post to raise ConnectionError
        def _failing_post(*args, **kwargs):
            raise ConnectionError("Caddy not reachable")
        monkeypatch.setattr(_real_req, "post", _failing_post)

        # Should not raise -- fails silently
        _sync_caddy_config()

    def test_security_headers_present(self, monkeypatch, caddy_env):
        """First route is a matchless security headers route with correct headers."""
        monkeypatch.setattr(config, "TLS_MODE", "internal")
        monkeypatch.setattr(config, "CADDY_PORT", 8443)

        _sync_caddy_config()

        caddy_cfg = caddy_env["captured"][0]
        routes = caddy_cfg["apps"]["http"]["servers"]["main"]["routes"]
        first = routes[0]

        # No matcher -- applies to all requests (non-terminal middleware)
        assert "match" not in first

        headers_handler = first["handle"][0]
        assert headers_handler["handler"] == "headers"
        hdr = headers_handler["response"]["set"]
        assert hdr["X-Content-Type-Options"] == ["nosniff"]
        assert hdr["X-Frame-Options"] == ["SAMEORIGIN"]
        assert hdr["Referrer-Policy"] == ["strict-origin-when-cross-origin"]
        assert hdr["Permissions-Policy"] == ["camera=(), microphone=(), geolocation=()"]
        # HSTS present when TLS is enabled
        assert "Strict-Transport-Security" in hdr
        assert "max-age=63072000" in hdr["Strict-Transport-Security"][0]

    def test_security_headers_no_hsts_when_tls_off(self, monkeypatch, caddy_env):
        """No HSTS header when TLS_MODE=off."""
        monkeypatch.setattr(config, "TLS_MODE", "off")
        monkeypatch.setattr(config, "CADDY_PORT", 8443)

        _sync_caddy_config()

        caddy_cfg = caddy_env["captured"][0]
        routes = caddy_cfg["apps"]["http"]["servers"]["main"]["routes"]
        first = routes[0]
        hdr = first["handle"][0]["response"]["set"]
        assert "Strict-Transport-Security" not in hdr
        # Other headers still present
        assert hdr["X-Content-Type-Options"] == ["nosniff"]

    def test_x_frame_options_sameorigin_global(self, monkeypatch, caddy_env):
        """X-Frame-Options is SAMEORIGIN globally (allows bot Control UI iframes)."""
        monkeypatch.setattr(config, "TLS_MODE", "internal")
        monkeypatch.setattr(config, "AUTH_DISABLED", True)
        monkeypatch.setattr(config, "CADDY_PORT", 8443)

        caddy_env["add_bot"]("testbot")

        _sync_caddy_config()

        caddy_cfg = caddy_env["captured"][0]
        routes = caddy_cfg["apps"]["http"]["servers"]["main"]["routes"]
        first = routes[0]

        # Global security headers use SAMEORIGIN (not DENY) because the
        # dashboard iframes bot Control UI at the same origin.
        assert "match" not in first
        hdr = first["handle"][0]["response"]["set"]
        assert hdr["X-Frame-Options"] == ["SAMEORIGIN"]

    def test_health_in_public_routes(self, monkeypatch, caddy_env):
        """Health endpoint is in the public (unauthenticated) routes."""
        monkeypatch.setattr(config, "TLS_MODE", "internal")
        monkeypatch.setattr(config, "CADDY_PORT", 8443)

        _sync_caddy_config()

        caddy_cfg = caddy_env["captured"][0]
        routes = caddy_cfg["apps"]["http"]["servers"]["main"]["routes"]

        # Find the public auth routes (match includes /api/auth/login)
        public_routes = [r for r in routes if any(
            "/api/auth/login" in p
            for m in r.get("match", [])
            for p in m.get("path", [])
        )]
        assert len(public_routes) == 1
        paths = public_routes[0]["match"][0]["path"]
        assert "/api/health" in paths
