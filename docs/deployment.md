# Deployment Guide

ClawFarm supports four TLS modes. Pick the one that matches your setup.

## Quick Start (Default)

Self-signed certificate, HTTPS on port 8443. Zero configuration beyond your LLM provider key.

```bash
cp .env.example .env
# Edit .env with your LLM provider API key
docker compose up -d
# Access: https://<server-ip>:8443
```

## Deployment Modes

### Internal (default) — Self-Signed TLS

Best for: LAN access, development, trying things out.

```env
TLS_MODE=internal
```

That's it. Caddy auto-generates a self-signed certificate. Your browser will show a certificate warning — accept it to proceed.

Access: `https://<server-ip>:8443`

### ACME — Let's Encrypt

Best for: public-facing deployments with a domain name.

```env
TLS_MODE=acme
DOMAIN=farm.example.com
CADDY_PORT=443
# Optional:
# ACME_EMAIL=admin@example.com
```

Caddy auto-provisions a Let's Encrypt certificate. Port 443 must be reachable from the internet. HTTP (port 80) redirects to HTTPS.

Access: `https://farm.example.com`

### Custom Certificate

Best for: corporate PKI, existing certificates.

```env
TLS_MODE=custom
```

Place your certificate files at:
- `certs/cert.pem` — certificate (or chain)
- `certs/key.pem` — private key

Access: `https://<server-ip>:8443`

### Off — Behind a Reverse Proxy

Best for: Traefik, nginx, Cloudflare Tunnel, or any upstream proxy that terminates TLS.

```env
TLS_MODE=off
PORTAL_URL=https://claws.example.com
```

Caddy serves plain HTTP inside the container. Your upstream proxy terminates TLS and forwards traffic to ClawFarm. No port changes needed — the default host port (8443) works fine; it's just HTTP despite the port number.

Access: whatever your proxy serves (e.g., `https://claws.example.com`)

## Upstream Proxy Configuration

When using `TLS_MODE=off`, your upstream proxy needs to:

1. **Forward traffic** to ClawFarm's host port (default 8443) over plain HTTP
2. **Support WebSockets** — the bot Control UI requires persistent WebSocket connections
3. **Pass proxy headers** — `X-Forwarded-For`, `X-Forwarded-Proto`, `X-Forwarded-Host`
4. **Not strip paths** — forward the full request path as-is
5. **Not buffer responses** — bots stream data

### Traefik

Docker labels (if Traefik auto-discovers containers):

```yaml
labels:
  - "traefik.enable=true"
  - "traefik.http.routers.clawfarm.rule=Host(`claws.example.com`)"
  - "traefik.http.routers.clawfarm.entrypoints=websecure"
  - "traefik.http.routers.clawfarm.tls.certresolver=letsencrypt"
  - "traefik.http.services.clawfarm.loadbalancer.server.port=8443"
  - "traefik.http.services.clawfarm.loadbalancer.passHostHeader=true"
```

Or dynamic config file:

```yaml
http:
  routers:
    clawfarm:
      rule: "Host(`claws.example.com`)"
      entryPoints:
        - websecure
      service: clawfarm
      tls:
        certResolver: letsencrypt

  services:
    clawfarm:
      loadBalancer:
        servers:
          - url: "http://<CLAWFARM_HOST_IP>:8443"
        passHostHeader: true
```

Traefik v2+ handles WebSocket upgrades automatically.

### nginx

```nginx
upstream clawfarm {
    server <CLAWFARM_HOST_IP>:8443;
}

server {
    listen 443 ssl;
    server_name claws.example.com;

    ssl_certificate     /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    location / {
        proxy_pass http://clawfarm;

        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host  $host;

        # WebSocket support
        proxy_http_version 1.1;
        proxy_set_header Upgrade    $http_upgrade;
        proxy_set_header Connection $connection_upgrade;

        # No buffering
        proxy_buffering off;

        # Long-lived connections
        proxy_read_timeout 300s;
    }
}

map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}
```

### Cloudflare Tunnel

```yaml
# cloudflared config
tunnel: <YOUR_TUNNEL_ID>
ingress:
  - hostname: claws.example.com
    service: http://<CLAWFARM_HOST_IP>:8443
  - service: http_status:404
```

Ensure the Cloudflare dashboard has WebSocket support enabled for the domain.

## Port Architecture

Caddy listens on a fixed internal port (8080) inside its container. Docker Compose maps the configurable host port to it:

```
Host :${CADDY_PORT:-8443}  -->  Container :8080  (Caddy)
Host :80                   -->  Container :80    (HTTP redirect, when TLS enabled)
```

This means `TLS_MODE` changes never require port changes. The host port stays 8443 by default regardless of whether Caddy is serving HTTPS or plain HTTP.

Override the host port with `CADDY_PORT` only when needed:
- `CADDY_PORT=443` for ACME mode (standard HTTPS port)
- `CADDY_PORT=<custom>` if your network requires a specific port

## Environment Variable Reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `TLS_MODE` | No | `internal` | TLS handling: `internal`, `acme`, `custom`, `off` |
| `CADDY_PORT` | No | `8443` | Host port for accessing the dashboard |
| `PORTAL_URL` | For `off` mode | (none) | Full external URL (scheme + host + port if non-standard) |
| `DOMAIN` | For `acme` mode | (none) | Public domain for Let's Encrypt |
| `ACME_EMAIL` | No | (none) | Email for Let's Encrypt notifications |

See `.env.example` for the full list including LLM provider keys, backup settings, and auth config.

## Health Check

All modes expose a health check endpoint:

```
GET /api/health  -->  200 OK
```

Use `http://<host>:<CADDY_PORT>/api/health` for monitoring.

## WebSocket Requirements

The bot Control UI uses WebSocket connections for real-time interaction. Your infrastructure must:

- Forward `Connection: Upgrade` and `Upgrade: websocket` headers
- Not impose short idle timeouts (300s+ recommended)
- Not buffer WebSocket frames
- Support long-lived connections (bot sessions can last hours)

WebSocket connections go to the root path (`wss://<host>/`). No special WebSocket-specific routing is needed — just ensure your proxy doesn't strip Upgrade headers.
