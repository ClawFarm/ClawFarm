# ClawFleetManager

Docker-based fleet manager for OpenClaw bots. Single-page dashboard to create, duplicate, fork, backup/rollback, and monitor bot containers.

## Project Structure

```
botfarm/
├── dashboard/              # FastAPI backend (Python 3.12)
│   ├── app.py              # All backend logic: bot lifecycle, backup, metrics, API routes
│   ├── Dockerfile
│   ├── requirements.txt
│   └── tests/test_fleet.py # ~100 unit + integration tests (pytest)
├── frontend/               # Next.js 16 dashboard UI (React 19, Tailwind 4, shadcn/ui)
│   ├── src/app/            # Pages: / (dashboard), /bots/[name] (detail)
│   ├── src/components/     # UI components (bot-card, bot-actions, logs-dialog, etc.)
│   ├── src/hooks/          # SWR data fetching hooks (use-bots, use-bot-detail)
│   ├── src/lib/            # api.ts (API client), types.ts, format.ts
│   ├── next.config.ts      # Proxies /api/* to backend via rewrites
│   └── Dockerfile
├── bot-template/           # Template files for new bots
│   ├── config.template.json
│   └── SOUL.md
├── bots/                   # Runtime: each bot gets a subdirectory (gitignored)
├── network/setup-isolation.sh  # iptables rules for bot network isolation
├── docker-compose.yml      # Production: dashboard + frontend + Caddy
├── Caddyfile               # Initial Caddy config (overwritten by admin API)
├── certs/                  # Self-signed TLS cert (gitignored)
├── screenshots/            # UI screenshots (gitignored)
├── .env                    # Local config (gitignored)
└── .env.example            # Template for .env
```

## Development Setup

```bash
# 1. Environment
cp .env.example .env   # Edit with your LLM endpoint details

# 2. Backend
python -m venv .venv && source .venv/bin/activate
pip install -r dashboard/requirements.txt
cd dashboard && uvicorn app:app --host 0.0.0.0 --port 8080 --reload

# 3. Frontend (separate terminal)
cd frontend && npm install && npm run dev

# 4. Open http://localhost:3000
```

## Docker Compose Deployment

**Production deployment is via Docker Compose.** All three services (dashboard, frontend, Caddy) run as containers. Caddy handles TLS termination and is the only publicly exposed service.

```bash
docker compose up --build -d
# Access at https://<server-ip>:8443
# HTTP :80 redirects to HTTPS :8443
```

Bot containers are also Docker containers created by the dashboard via the Docker socket — so the full stack is Docker-in-Docker (dashboard container → Docker socket → bot containers on the host).

### Screenshots

UI screenshots go in the `screenshots/` directory (gitignored). Use this for documenting UI changes or sharing progress.

## Running Tests

```bash
source .venv/bin/activate
cd dashboard && python -m pytest tests/test_fleet.py -v
```

All tests are filesystem-based with monkeypatched paths — no Docker needed.

## Key Architecture Decisions

### OpenClaw API Mode
Bots use `openai-completions` API mode (NOT `openai-responses`). This maps to `/v1/chat/completions` which is vLLM's core API with full tool calling support. The `openai-responses` mode uses `/v1/responses` and does NOT send tool definitions to local models.

### Bot Container Setup
Each bot gets:
- Its own Docker bridge network (isolation)
- Port allocated from `BOT_PORT_START`–`BOT_PORT_END` range
- `.openclaw/` directory mounted as `/home/node/.openclaw` with:
  - `openclaw.json` — model provider config, gateway settings
  - `workspace/SOUL.md` — personality
  - `workspace/MEMORY.md` — agent memory (pre-created empty)
  - `workspace/memory/` — date-based memory files
- Container command: `node openclaw.mjs gateway --allow-unconfigured --bind lan --auth trusted-proxy` (Docker Compose mode)
- Restart policy: `unless-stopped`

### Bot Durability and Image Updates

**Failure recovery:** Bot containers use `restart: unless-stopped`, so they automatically restart after crashes, OOM kills, or host reboots. The Docker daemon handles this — no supervisor needed. The only way a bot stays down is if it's explicitly stopped via the dashboard or `docker stop`.

**Data durability:** All bot state lives on the host filesystem under `bots/{name}/` (mounted into the container). Container destruction doesn't lose data — only deleting the bot directory does. Scheduled backups (hourly by default) provide an additional safety net, especially when stored in an external `BACKUP_DIR`.

**Image updates:** Bot containers are created with the image specified by `OPENCLAW_IMAGE` (default: `ghcr.io/openclaw/openclaw:latest`). To update:
1. Pull the new image: `docker pull ghcr.io/openclaw/openclaw:latest`
2. Stop and delete existing bot containers via the dashboard (or `docker rm -f`)
3. Recreate bots from the dashboard — they'll use the new image with existing data

There is no automatic rolling update. Each bot must be recreated individually. Since bot state is on the host filesystem, recreation is non-destructive — the new container picks up the existing `.openclaw/` directory.

**Per-agent management:** Each bot is an independent Docker container with its own network, port, config, and state directory. Bots can be started, stopped, deleted, duplicated, and forked independently. There is no shared state between bots.

### Gateway Auth (Trusted Proxy)
In Docker Compose mode, OpenClaw runs in `trusted-proxy` auth mode. Caddy handles TLS termination and injects an `X-Forwarded-User` header. OpenClaw reads this header for user identity (configured via `gateway.auth.trustedProxy.userHeader` in `openclaw.json`). This bypasses device pairing entirely — Caddy is the single gatekeeper.

In dev mode (no Caddy), OpenClaw uses default token auth. The gateway token is surfaced on the bot detail page and passed via URL hash (`#token=...`) when opening the Control UI.

### Backup/Rollback
Backups are compressed `tar.gz` archives containing the full agent state: `config.json`, `SOUL.md`, and the entire `.openclaw/` directory (workspace, sessions, cron — excluding logs, `.bak` files, and temp files). Each backup records `size_bytes` in metadata.

**Storage:** By default, backups are stored in `bots/{name}/.backups/{timestamp}.tar.gz`. When `BACKUP_DIR` is set, backups go to `{BACKUP_DIR}/{bot_name}/{timestamp}.tar.gz` instead — allowing backups to survive bot deletion and be stored on a separate volume.

**Scheduled backups:** A background thread runs hourly (configurable via `BACKUP_INTERVAL_SECONDS`, 0 to disable) creating backups labeled `"scheduled"` for all bot containers.

**Retention:** After each scheduled backup, old scheduled backups beyond `BACKUP_KEEP` (default 24) are pruned. Manual backups are never auto-pruned.

**Rollback** restores everything but preserves the current gateway auth token so active UI connections aren't broken. A pre-rollback auto-backup is always created first. Rollback supports both new tar.gz backups and old directory-based backups (backward compatible).

### Docker-in-Docker Volume Mount Paths (HOST_BOTS_DIR Workaround)

**This is a critical gotcha for docker-compose deployments.**

The dashboard container creates bot containers via the Docker socket (`/var/run/docker.sock`). When it calls `docker.containers.run()` with volume mounts, the paths must be **host** paths — because the Docker daemon runs on the host, not inside the dashboard container.

The problem: inside the dashboard container, `bot_dir.resolve()` returns `/data/bots/captain-jack` (the container-internal mount point). But the Docker daemon needs the host path, e.g., `/home/storm/projects/botfarm/bots/captain-jack`.

The solution: `HOST_BOTS_DIR` env var is set in `docker-compose.yml` to `${PWD}/bots`. The `_host_path()` function in `app.py` converts container-internal paths to host paths:

```python
def _host_path(container_path: Path) -> str:
    host_bots = os.environ.get("HOST_BOTS_DIR", "")
    if not host_bots:
        return str(container_path.resolve())  # dev mode: paths are already host paths
    rel = container_path.resolve().relative_to(BOTS_DIR.resolve())
    return str(Path(host_bots) / rel)
```

In `docker-compose.yml`:
```yaml
environment:
  - BOTS_DIR=/data/bots
  - HOST_BOTS_DIR=${PWD}/bots
```

Without this, bot containers get volume mounts pointing to `/data/bots/...` which doesn't exist on the host, causing silent mount failures or permission errors.

**When running outside Docker** (dev mode), `HOST_BOTS_DIR` is unset and `_host_path()` falls back to `resolve()` which returns the correct host path directly.

### File Permissions for Bot Containers

The dashboard container runs as UID 1000 (matching OpenClaw's `node` user and the typical host user). This means all files created by the dashboard are naturally readable/writable by bot containers — no `chown`/`chmod` fixups needed. Docker socket access is granted via `group_add` in `docker-compose.yml` using the host's docker group GID (`DOCKER_GID` env var).

### Caddy HTTPS Reverse Proxy

Single entry point for all services via Caddy on port 8443 (HTTPS). This is required because OpenClaw Control UI uses `crypto.subtle` which only works in a Secure Context (HTTPS or localhost).

**Architecture:** Per-port TLS termination. Each bot gets its own HTTPS port through Caddy because OpenClaw's Control UI connects WebSocket to the root `/` of the origin (path-based routing is impossible).

**Route structure:**
- `:8443/` and `:8443/*` → Next.js frontend (`frontend:3000`)
- `:8443/api/*` → FastAPI dashboard (`dashboard:8080`)
- `:{bot_port}/` → Per-bot TLS-terminated reverse proxy to `openclaw-bot-{name}:18789`

**Dynamic route sync:** `_sync_caddy_config()` in `app.py` pushes the full JSON config to Caddy's admin API (`POST http://caddy:2019/load`) on every bot lifecycle event (create, delete, start, stop) and on dashboard startup. Uses full-state reconciliation — rebuilds the entire config from current Docker container state. Caddy is connected to each bot's bridge network to reach containers directly.

**TLS:** Self-signed cert at `certs/cert.pem` + `certs/key.pem`. Generate with:
```bash
openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes \
  -keyout certs/key.pem -out certs/cert.pem -days 3650 \
  -subj "/CN=ClawFleetManager" \
  -addext "subjectAltName=IP:<server-ip>,IP:127.0.0.1,DNS:localhost"
```

### Duplicate vs Fork
- **Duplicate**: Copies config, soul, and workspace. No lineage tracked.
- **Fork**: Same as duplicate but records `forked_from` in metadata.
- Both copy the source bot's workspace (memories, identity files) but NOT sessions or gateway auth — each bot gets fresh conversation history and its own auth token.

### Authentication & RBAC

**Architecture:** Caddy `forward_auth` → FastAPI session-based auth.

```
Browser → Caddy (forward_auth subrequest) → FastAPI /api/auth/verify
                                              ↓ 200 + X-User header
       ← Caddy copies X-User → X-Forwarded-User → Bot container / Frontend
```

**User store:** `bots/.users.json` — persists via existing volume mount. Users have `username`, `password_hash` (bcrypt), `role` (admin/user), and `bots` (list of bot names or `["*"]` for all).

**Sessions:** In-memory dict keyed by `secrets.token_urlsafe(32)`. Lost on restart (users must re-login). `_get_session()` re-reads `users.json` every call for always-current RBAC.

**RBAC:** Admin role → all bots. `bots: ["*"]` → all bots. Otherwise check specific bot name list. Per-bot port access uses `_PORT_TO_BOT` cache (rebuilt by `_sync_caddy_config`, zero Docker API calls per auth check).

**Caddy integration:** When `AUTH_DISABLED` is false, Caddy routes are split into public (login, verify, assets) and protected (everything else). Protected routes use forward_auth with subrequest to `/api/auth/verify`. Bot port routes include `X-Original-Port` header for per-bot RBAC.

**Cookie:** `cfm_session`, HttpOnly + Secure + SameSite=Lax, 24h TTL.

**First run:** If no users exist, `_bootstrap_admin()` creates an admin user from `ADMIN_USER`/`ADMIN_PASSWORD` env vars. If `ADMIN_PASSWORD` is unset, a random password is generated and printed to stdout.

**Auth disabled mode:** Set `AUTH_DISABLED=1` to skip all auth. Caddy uses hardcoded `X-Forwarded-User: dev`.

## Important Files

| File | Purpose |
|------|---------|
| `dashboard/app.py` | **All backend logic** — bot CRUD, backup, rollback, metrics, auth, Docker orchestration, API routes |
| `frontend/src/lib/api.ts` | Frontend API client — all backend calls |
| `frontend/src/lib/types.ts` | TypeScript interfaces (Bot, BotDetail, BotStats, Backup, User, etc.) |
| `frontend/src/app/page.tsx` | Dashboard home page |
| `frontend/src/app/bots/[name]/page.tsx` | Bot detail page |
| `frontend/src/app/login/page.tsx` | Login page |
| `frontend/src/app/users/page.tsx` | Admin user management page |
| `frontend/src/hooks/use-auth.ts` | SWR hook for auth state |
| `frontend/next.config.ts` | API proxy rewrite rules |
| `bot-template/config.template.json` | Base config for new bots |

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `LLM_BASE_URL` | Yes | vLLM endpoint (e.g., `http://10.88.100.186:8000/v1`) |
| `LLM_MODEL` | Yes | Model name (e.g., `Qwen3.5-122B-A10B`) |
| `LLM_API_KEY` | Yes | API key for the LLM server |
| `LLM_HOST` | For isolation | LLM server IP (used by iptables rules) |
| `LLM_PORT` | For isolation | LLM server port (used by iptables rules) |
| `BOT_PORT_START` | No | Start of port range (default: 3001) |
| `BOT_PORT_END` | No | End of port range (default: 3100) |
| `HOST_BOTS_DIR` | Docker Compose | **Host-side** path to `bots/` dir — required when dashboard runs in a container (see workaround above) |
| `DASHBOARD_PORT` | No | Frontend port (default: 3000) |
| `CADDY_PORT` | No | Caddy HTTPS port (default: 8443) |
| `CADDY_ADMIN_URL` | Docker Compose | Caddy admin API URL (default: `http://caddy:2019`) |
| `PORTAL_URL` | Recommended | External base URL (e.g., `https://10.88.142.100` or `https://fleet.example.com`) — used for HTTP redirects and bot UI links |
| `BRAVE_API_KEY` | No | Brave Search API key for agent web search |
| `DOCKER_GID` | Docker Compose | GID of the `docker` group on the host (default: 988). Detect with `stat -c '%g' /var/run/docker.sock` |
| `OPENCLAW_IMAGE` | No | Bot container image (default: `ghcr.io/openclaw/openclaw:latest`) |
| `BACKUP_DIR` | No | External backup directory. Empty = store in bot's `.backups/` dir |
| `BACKUP_INTERVAL_SECONDS` | No | How often to run scheduled backups (default: 3600, 0 = disabled) |
| `BACKUP_KEEP` | No | Max scheduled backups to retain per bot (default: 24) |
| `ADMIN_USER` | No | Default admin username (default: `admin`) |
| `ADMIN_PASSWORD` | No | Admin password (empty = auto-generated, printed to stdout) |
| `SESSION_TTL` | No | Session lifetime in seconds (default: 86400 = 24h) |
| `AUTH_DISABLED` | No | Set to `1` or `true` to disable auth entirely |

## Common Tasks

### Adding a new API endpoint
1. Add the function in `dashboard/app.py` (pure logic section)
2. Add the FastAPI route in the routes section at the bottom
3. Add the client method in `frontend/src/lib/api.ts`
4. Add TypeScript types if needed in `frontend/src/lib/types.ts`
5. Add tests in `dashboard/tests/test_fleet.py`

### Adding a new UI component
Frontend uses shadcn/ui components in `frontend/src/components/ui/`. Custom components go in `frontend/src/components/`. Data fetching uses SWR hooks in `frontend/src/hooks/`.
