import os
import threading

import config
import docker_utils


def _connect_caddy_to_network(client, network_name: str) -> None:
    """Connect Caddy container to a bot's bridge network."""
    try:
        network = client.networks.get(network_name)
        network.connect(config.CADDY_CONTAINER)
    except Exception:
        pass  # Caddy not running or already connected


def _get_caddy_ip_on_network(client, network_name: str) -> str | None:
    """Get Caddy container's IP address on a specific bot bridge network."""
    try:
        network = client.networks.get(network_name)
        network.reload()
        for container_id, info in network.attrs.get("Containers", {}).items():
            # Match by name
            try:
                c = client.containers.get(container_id)
                if c.name == config.CADDY_CONTAINER:
                    ip = info.get("IPv4Address", "")
                    return ip.split("/")[0] if ip else None
            except Exception:
                continue
    except Exception:
        pass
    return None


def _disconnect_caddy_from_network(client, network_name: str) -> None:
    """Disconnect Caddy container from a bot's bridge network."""
    try:
        network = client.networks.get(network_name)
        network.disconnect(config.CADDY_CONTAINER)
    except Exception:
        pass


def _build_tls_config() -> tuple[list, dict, str]:
    """Build TLS connection policies, TLS app config, and scheme based on TLS_MODE.

    Returns (tls_connection_policies, tls_app_config, scheme).
    tls_connection_policies must be [{}] (not []) for HTTPS modes — Caddy
    requires this field on the server to activate TLS on the listener.
    """
    if config.TLS_MODE == "off":
        return [], {}, "http"
    elif config.TLS_MODE == "acme":
        email = os.environ.get("ACME_EMAIL", "")
        issuer = {"module": "acme"}
        if email:
            issuer["email"] = email
        policy = {"issuers": [issuer]}
        if config.DOMAIN:
            policy["subjects"] = [config.DOMAIN]
        return [{}], {"automation": {"policies": [policy]}}, "https"
    elif config.TLS_MODE == "custom":
        return (
            [{"certificate_selection": {"any_tag": ["cert0"]}}],
            {"certificates": {"load_files": [{
                "certificate": "/certs/cert.pem",
                "key": "/certs/key.pem",
                "tags": ["cert0"],
            }]}},
            "https",
        )
    else:  # "internal" — default
        # on_demand: true is required for port-only listeners (no hostname).
        # Without it, Caddy can't determine what hostname to put on the cert
        # and TLS handshakes fail silently.
        return [{}], {"automation": {"policies": [{
            "issuers": [{"module": "internal"}],
            "on_demand": True,
        }]}}, "https"


def _sync_caddy_config() -> None:
    """Push updated route config to Caddy's admin API.

    Builds a JSON config with:
    - Main server for dashboard + frontend + path-based bot routes
    - HTTP redirect server (when TLS is enabled)
    - forward_auth subrequests to /api/auth/verify for authentication

    TLS_MODE controls certificate handling:
    - internal (default): Caddy auto-generates a self-signed cert
    - acme: Let's Encrypt via DOMAIN
    - custom: Load user-provided cert files from /certs/
    - off: Plain HTTP (for use behind an upstream proxy)

    Bots are routed via /claw/{name}/ paths on the main port.

    Fails silently when Caddy is not reachable (dev mode).
    """
    try:
        import requests as _req

        client = docker_utils._get_client()
        containers = client.containers.list(
            all=False, filters={"label": "openclaw.bot=true"}
        )

        caddy_port = config._CADDY_LISTEN_PORT
        tls_policy, tls_app, scheme = _build_tls_config()

        # forward_auth handler: subrequest to dashboard's /api/auth/verify
        # PORTAL_URL already includes port if needed (e.g. "http://host:8080")
        login_url = (
            f"{config.PORTAL_URL}/login"
            if config.PORTAL_URL else
            f"{scheme}://{{http.request.host}}:{config.CADDY_PORT}/login"
        )

        def _forward_auth_handler(extra_headers=None, redirect_on_fail=False):
            """Build a Caddy forward_auth (reverse_proxy) handler.

            Uses copy_response (Caddy's native forward_auth mechanism)
            so that the auth subrequest doesn't consume the original connection.
            This is critical for WebSocket upgrade requests.
            """
            handle_response = [
                {
                    "match": {"status_code": [2]},
                    "routes": [
                        {
                            "handle": [{
                                "handler": "headers",
                                "request": {
                                    "set": {
                                        "X-Forwarded-User": [
                                            "{http.reverse_proxy.header.X-Forwarded-User}"
                                        ],
                                    },
                                },
                            }],
                        },
                    ],
                },
            ]
            if redirect_on_fail:
                handle_response.append({
                    "match": {"status_code": [4]},
                    "routes": [{
                        "handle": [{
                            "handler": "static_response",
                            "headers": {"Location": [login_url]},
                            "status_code": 302,
                        }],
                    }],
                })
            # Default: pass through auth server's error response
            handle_response.append({
                "routes": [{
                    "handle": [{
                        "handler": "copy_response",
                    }],
                }],
            })
            h = {
                "handler": "reverse_proxy",
                "upstreams": [{"dial": "dashboard:8080"}],
                "rewrite": {"method": "GET", "uri": "/api/auth/verify"},
                "headers": {"request": {
                    "set": {},
                    "delete": [
                        "Connection",
                        "Upgrade",
                        "Sec-WebSocket-Version",
                        "Sec-WebSocket-Key",
                        "Sec-WebSocket-Extensions",
                        "Sec-WebSocket-Protocol",
                    ],
                }},
                "handle_response": handle_response,
            }
            if extra_headers:
                h["headers"]["request"]["set"].update(extra_headers)
            return h

        # Main HTTPS routes
        if config.AUTH_DISABLED:
            main_routes = [
                {
                    "match": [{"path": ["/api/*"]}],
                    "handle": [{
                        "handler": "reverse_proxy",
                        "upstreams": [{"dial": "dashboard:8080"}],
                    }],
                },
                {
                    "handle": [{
                        "handler": "reverse_proxy",
                        "upstreams": [{"dial": "frontend:3000"}],
                    }],
                },
            ]
        else:
            main_routes = [
                # Public auth endpoints
                {
                    "match": [{"path": [
                        "/api/auth/login", "/api/auth/verify", "/api/auth/logout",
                        "/api/health",
                    ]}],
                    "handle": [{
                        "handler": "reverse_proxy",
                        "upstreams": [{"dial": "dashboard:8080"}],
                    }],
                },
                # Protected API
                {
                    "match": [{"path": ["/api/*"]}],
                    "handle": [
                        _forward_auth_handler(),
                        {
                            "handler": "reverse_proxy",
                            "upstreams": [{"dial": "dashboard:8080"}],
                        },
                    ],
                },
                # Public frontend routes (login, assets)
                {
                    "match": [{"path": [
                        "/login", "/login/*", "/_next/*", "/favicon.ico", "/logo.svg",
                    ]}],
                    "handle": [{
                        "handler": "reverse_proxy",
                        "upstreams": [{"dial": "frontend:3000"}],
                    }],
                },
                # Protected frontend (everything else) — redirect to login on 4xx
                {
                    "handle": [
                        _forward_auth_handler(redirect_on_fail=True),
                        {
                            "handler": "reverse_proxy",
                            "upstreams": [{"dial": "frontend:3000"}],
                        },
                    ],
                },
            ]

        # Path-based bot routes — insert before catch-all frontend route.
        # OpenClaw serves with basePath=/claw/{name}, so no strip_path_prefix needed.
        # WebSocket URLs include basePath natively (upstream PR #30228).
        if os.environ.get("HOST_BOTS_DIR"):
            for c in containers:
                name = c.labels.get("openclaw.name", "")
                if not name:
                    continue
                container_name = f"openclaw-bot-{name}"
                bot_proxy = {"handler": "reverse_proxy", "upstreams": [{"dial": f"{container_name}:18789"}]}
                if config.AUTH_DISABLED:
                    fwd_user = {"handler": "headers", "request": {"set": {"X-Forwarded-User": ["dev"]}}}
                    handlers = [fwd_user, bot_proxy]
                else:
                    handlers = [
                        _forward_auth_handler(extra_headers={"X-Original-Bot": [name]}, redirect_on_fail=True),
                        bot_proxy,
                    ]
                main_routes.insert(-1, {
                    "match": [{"path": [f"/claw/{name}/*", f"/claw/{name}"]}],
                    "handle": handlers,
                })

        # Security response headers — matchless route (non-terminal middleware).
        # X-Frame-Options is SAMEORIGIN (not DENY) because the dashboard
        # legitimately iframes bot Control UI at the same origin.
        security_headers = {
            "X-Content-Type-Options": ["nosniff"],
            "X-Frame-Options": ["SAMEORIGIN"],
            "Referrer-Policy": ["strict-origin-when-cross-origin"],
            "Permissions-Policy": ["camera=(), microphone=(), geolocation=()"],
        }
        if config.TLS_MODE != "off":
            security_headers["Strict-Transport-Security"] = [
                "max-age=63072000; includeSubDomains"
            ]
        main_routes.insert(0, {
            "handle": [{
                "handler": "headers",
                "response": {"set": security_headers},
            }],
        })

        # Build main server config
        main_server = {
            "listen": [f":{caddy_port}"],
            "routes": main_routes,
        }
        if tls_policy:
            main_server["tls_connection_policies"] = tls_policy
        # In TLS_MODE=off, Caddy sits behind an upstream proxy (Traefik, nginx,
        # Cloudflare Tunnel). Trust private-range IPs so Caddy reads the real
        # client IP from X-Forwarded-For instead of replacing it.
        if config.TLS_MODE == "off":
            main_server["trusted_proxies"] = {
                "source": "static",
                "ranges": [
                    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
                    "127.0.0.0/8", "fc00::/7", "::1/128",
                ],
            }

        servers = {"main": main_server}

        # Add HTTP→HTTPS redirect server when TLS is enabled
        if config.TLS_MODE != "off":
            redirect_location = (
                f"{config.PORTAL_URL}{{http.request.uri}}"
                if config.PORTAL_URL else
                f"https://{{http.request.host}}:{config.CADDY_PORT}{{http.request.uri}}"
            )
            servers["redirect"] = {
                "listen": [":80"],
                "routes": [{
                    "handle": [{
                        "handler": "static_response",
                        "headers": {"Location": [redirect_location]},
                        "status_code": 302,
                    }],
                }],
            }

        apps = {"http": {"servers": servers}}
        if tls_app:
            apps["tls"] = tls_app

        # Note: Caddy disables origin enforcement when the admin API listens on
        # an open interface (":2019"), so the "origins" field is documentation only.
        # The real protection is Docker network isolation — port 2019 is not
        # published to the host, so only containers on the compose network can
        # reach the admin API.
        caddy_config = {
            "admin": {"listen": ":2019", "origins": ["dashboard"]},
            "apps": apps,
        }

        _req.post(
            f"{config.CADDY_ADMIN_URL}/load",
            json=caddy_config,
            headers={"Content-Type": "application/json"},
            timeout=5,
        )
    except Exception:
        pass  # Caddy not running (dev mode) — silently ignore


def _sync_caddy_config_async() -> None:
    """Fire-and-forget Caddy config sync in a background thread.

    Decouples the Caddy admin API POST from the API response path so that
    Docker network churn (ERR_NETWORK_CHANGED) doesn't hit the browser while
    the HTTP response is still in-flight.  A 1-second delay ensures the
    response has been flushed before Caddy reloads.  Safe because
    _sync_caddy_config() is idempotent (full-state reconciliation) and Caddy
    serialises admin requests internally.
    """
    import time as _time

    def _delayed_sync():
        _time.sleep(1)
        _sync_caddy_config()

    threading.Thread(target=_delayed_sync, daemon=True,
                     name="caddy-sync").start()
