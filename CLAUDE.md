# ClawFarm

Docker-based fleet manager for OpenClaw bots. Single-page dashboard to create, duplicate, fork, backup/rollback, and monitor bot containers.

## Project Structure

```
botfarm/
├── dashboard/              # FastAPI backend (Python 3.12)
│   ├── app.py              # All backend logic: bot lifecycle, backup, metrics, API routes
│   ├── entrypoint.sh       # Docker entrypoint: auto-detects Docker socket GID
│   ├── Dockerfile
│   └── tests/test_fleet.py # ~100 unit + integration tests (pytest)
├── frontend/               # Next.js 16 dashboard UI (React 19, Tailwind 4, shadcn/ui)
│   ├── src/app/            # Pages: / (dashboard), /bots/[name] (detail)
│   ├── src/components/     # UI components (bot-card, bot-actions, logs-dialog, etc.)
│   ├── src/hooks/          # SWR data fetching hooks (use-bots, use-bot-detail)
│   ├── src/lib/            # api.ts (API client), types.ts, format.ts
│   ├── next.config.ts      # Proxies /api/* to backend via rewrites
│   └── Dockerfile
├── bot-template/           # Bot templates (one dir per provider)
│   ├── default/            # Anthropic Claude (recommended)
│   ├── openai/             # OpenAI GPT
│   ├── minimax/            # MiniMax (budget-friendly)
│   ├── qwen/               # Qwen via DashScope (budget-friendly)
│   ├── custom-endpoint/    # Self-hosted vLLM, Ollama, LM Studio
│   └── researcher/         # Research-focused (custom endpoint + web search)
├── bots/                   # Runtime: each bot gets a subdirectory (gitignored)
├── network/setup-isolation.sh  # iptables rules for bot network isolation
├── docker-compose.yml      # Production: pulls pre-built images from GHCR
├── docker-compose.dev.yml  # Development: builds from local source
├── Caddyfile               # Initial Caddy config (overwritten by admin API)
├── pyproject.toml          # Python deps, ruff + pytest config
├── .github/workflows/      # CI (lint + test + build) and release (Docker publish)
├── CONTRIBUTING.md         # Contributor guidelines
├── RELEASING.md            # Release process docs
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
uv pip install -e ".[dev]"
cd dashboard && uvicorn app:app --host 0.0.0.0 --port 8080 --reload

# 3. Frontend (separate terminal)
cd frontend && npm install && npm run dev

# 4. Open http://localhost:3000
```

## Docker Compose Deployment

**Production deployment is via Docker Compose.** All three services (dashboard, frontend, Caddy) run as containers. Caddy handles TLS termination (configurable via `TLS_MODE`) and is the only publicly exposed service. Docker socket GID is auto-detected at runtime — no manual `DOCKER_GID` configuration needed.

```bash
cp .env.example .env  # Edit with your LLM provider details
docker compose up -d  # Pulls pre-built images from GHCR
# Access at https://<server-ip>:8443 (default TLS_MODE=internal, self-signed cert)
# HTTP :80 redirects to HTTPS :8443

# For development (builds from source):
docker compose -f docker-compose.dev.yml up --build -d
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

## CI & Linting

**Ruff** enforces Python style (`pyproject.toml`): E/W (pycodestyle), F (pyflakes), I (isort), line-length 120, target py312.

**GitHub Actions** (`.github/workflows/ci.yml`) runs four parallel jobs on every push/PR to `master`:
1. `backend-lint` — ruff via `astral-sh/ruff-action`
2. `backend-test` — pytest
3. `frontend-lint` — ESLint
4. `frontend-build` — TypeScript check + Next.js build

**Release workflow** (`.github/workflows/release.yml`) triggers on `v*` tags: runs CI, builds multi-arch Docker images (amd64 + arm64), pushes to GHCR, creates GitHub Release. See `RELEASING.md`.

## Key Architecture Decisions

### Bot Templates
Templates live in `bot-template/`. Each template is a directory containing:
- `openclaw.template.json` — OpenClaw native config with `{{ENV_VAR}}` placeholder syntax
- `SOUL.md` — Bot personality
- `template.meta.json` — Display metadata (`description`, `env_hint`)

**Provider templates:**

| Template | Provider | Type | Required env vars |
|----------|----------|------|-------------------|
| `default` | Anthropic Claude | Built-in provider | `ANTHROPIC_API_KEY` |
| `openai` | OpenAI GPT | Built-in provider | `OPENAI_API_KEY` |
| `minimax` | MiniMax | OAI-compatible | `MINIMAX_API_KEY` |
| `qwen` | Qwen/DashScope | OAI-compatible | `QWEN_API_KEY` |
| `custom-endpoint` | Self-hosted (vLLM, Ollama) | OAI-compatible | `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` |
| `researcher` | Any (custom endpoint + web search) | OAI-compatible | `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`, `BRAVE_API_KEY` |

**Built-in provider templates** (Anthropic, OpenAI) are minimal — they don't define `models.providers` because OpenClaw auto-detects the provider from the API key env var. The API key is forwarded to bot containers via Docker `environment`.

**OAI-compatible templates** (MiniMax, Qwen, custom-endpoint) define `models.providers` with a base URL and use `{{ENV_VAR}}` placeholders for the API key. The key is baked into `openclaw.json` at bot creation time.

`{{VAR}}` placeholders are replaced with env var values at bot creation time. Unquoted placeholders (e.g., `{{LLM_CONTEXT_WINDOW}}`) become raw numbers after substitution.

ClawFarm injects gateway auth, proxy config, and tool settings on top of the resolved template — users don't touch those fields. Create new templates by copying an existing one and editing.

### OpenClaw API Mode
The `custom-endpoint` and related templates use `openai-completions` API mode (NOT `openai-responses`). This maps to `/v1/chat/completions` which is vLLM's core API with full tool calling support. The `openai-responses` mode uses `/v1/responses` and does NOT send tool definitions to local models. Built-in provider templates (Anthropic, OpenAI) use OpenClaw's native wire protocol for each provider (e.g., `anthropic-messages` for Anthropic).

### Bot Container Setup
Each bot gets:
- Its own Docker bridge network (isolation)
- Port allocated from internal range (used for container labels/identification)
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
Backups are compressed `tar.gz` archives containing the full agent state: the entire `.openclaw/` directory (`openclaw.json`, workspace with `SOUL.md` and memories, sessions, cron — excluding logs, `.bak` files, and temp files). Each backup records `size_bytes` in metadata.

**Storage:** By default, backups are stored in `bots/{name}/.backups/{timestamp}.tar.gz`. When `BACKUP_DIR` is set, backups go to `{BACKUP_DIR}/{bot_name}/{timestamp}.tar.gz` instead — allowing backups to survive bot deletion and be stored on a separate volume.

**Scheduled backups:** A background thread runs hourly (configurable via `BACKUP_INTERVAL_SECONDS`, 0 to disable) creating backups labeled `"scheduled"` for all bot containers.

**Retention:** After each scheduled backup, old scheduled backups beyond `BACKUP_KEEP` (default 24) are pruned. Manual backups are never auto-pruned.

**Rollback** restores everything but preserves the current gateway auth token so active UI connections aren't broken. A pre-rollback auto-backup is always created first. Rollback supports both new tar.gz backups and old directory-based backups (backward compatible).

### Docker-in-Docker Volume Mount Paths (HOST_BOTS_DIR Workaround)

**This is a critical gotcha for docker-compose deployments.**

The dashboard container creates bot containers via the Docker socket (`/var/run/docker.sock`). When it calls `docker.containers.run()` with volume mounts, the paths must be **host** paths — because the Docker daemon runs on the host, not inside the dashboard container.

The problem: inside the dashboard container, `bot_dir.resolve()` returns `/data/bots/captain-jack` (the container-internal mount point). But the Docker daemon needs the host path, e.g., `/path/to/botfarm/bots/captain-jack`.

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

The dashboard container runs as UID 1000 (matching OpenClaw's `node` user and the typical host user). This means all files created by the dashboard are naturally readable/writable by bot containers — no `chown`/`chmod` fixups needed. Docker socket access is auto-detected: the `entrypoint.sh` script reads the Docker socket's GID at runtime and adds the app user to that group — no `DOCKER_GID` env var needed.

### Caddy HTTPS Reverse Proxy

Single entry point for all services via Caddy on port 8443 (HTTPS). This is required because OpenClaw Control UI uses `crypto.subtle` which only works in a Secure Context (HTTPS or localhost).

**Architecture:** Path-based routing under the single `:8443` port. Each bot is accessible at `https://host:8443/claw/{name}/`. Caddy uses `strip_path_prefix` to strip `/claw/{name}` before proxying to the bot — OpenClaw serves at root (no `basePath` config needed). This eliminates exposing 100 ports — only `:8443` and `:80` are published.

**Route structure:**
- `:8443/` and `:8443/*` → Next.js frontend (`frontend:3000`)
- `:8443/api/*` → FastAPI dashboard (`dashboard:8080`)
- `:8443/claw/{name}/*` → strip prefix → reverse proxy to `openclaw-bot-{name}:18789`

**WebSocket routing:** OpenClaw Control UI connects WebSocket to `wss://{host}/` (root), ignoring the sub-path. Caddy sets a `cfm_bot={name}` cookie when serving the Control UI page. Root WebSocket upgrade requests are matched by a `header_regexp` on the `Cookie` header and routed to the correct bot. The `header_regexp` key must be the header name (e.g., `"Cookie": {"name": "cfm_bot", "pattern": "..."}`) — NOT `{"cfm_bot": {"name": "Cookie", ...}}`.

**Dynamic route sync:** `_sync_caddy_config()` in `app.py` pushes the full JSON config to Caddy's admin API (`POST http://caddy:2019/load`) on every bot lifecycle event (create, delete, start, stop) and on dashboard startup. Uses full-state reconciliation — rebuilds the entire config from current Docker container state. Caddy is connected to each bot's bridge network to reach containers directly.

**Startup migration:** On startup, existing bots' `openclaw.json` is checked and `basePath` is removed if present (Caddy handles sub-path routing externally).

**TLS modes** (`TLS_MODE` env var):

| Mode | Behavior | Default port | Use case |
|------|----------|-------------|----------|
| `internal` **(default)** | Caddy auto-generates self-signed cert | 8443 | LAN/IP — zero config |
| `acme` | Let's Encrypt via `DOMAIN` env var | 443 | Public domain |
| `custom` | Load `certs/cert.pem` + `certs/key.pem` | 8443 | Existing PKI |
| `off` | Plain HTTP, no TLS | 8080 | Behind upstream proxy |

`_build_tls_config()` returns `(tls_connection_policies, tls_app, scheme)` based on `TLS_MODE`. `_sync_caddy_config()` uses these to build the Caddy JSON config. In `off` mode, no HTTP→HTTPS redirect server is created. In `acme` mode, `PORTAL_URL` is auto-derived from `DOMAIN` if not set.

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

**RBAC:** Admin role → all bots. `bots: ["*"]` → all bots. Otherwise check specific bot name list. Per-bot path access uses `X-Original-Bot` header set by Caddy (zero Docker API calls per auth check).

**Caddy integration:** When `AUTH_DISABLED` is false, Caddy routes are split into public (login, verify, assets) and protected (everything else). Protected routes use forward_auth with subrequest to `/api/auth/verify`. Bot path routes include `X-Original-Bot` header for per-bot RBAC.

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
| `bot-template/default/openclaw.template.json` | Default OpenClaw config template with `{{ENV_VAR}}` placeholders |
| `bot-template/default/SOUL.md` | Default bot personality |

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | For Anthropic | Anthropic API key (default template) |
| `ANTHROPIC_MODEL` | No | Model name (default: `claude-sonnet-4-6`) |
| `OPENAI_API_KEY` | For OpenAI | OpenAI API key (openai template) |
| `OPENAI_MODEL` | No | Model name (default: `gpt-4o`) |
| `MINIMAX_API_KEY` | For MiniMax | MiniMax API key (minimax template) |
| `MINIMAX_MODEL` | No | Model name (default: `MiniMax-M1`) |
| `QWEN_API_KEY` | For Qwen | DashScope API key (qwen template) |
| `QWEN_MODEL` | No | Model name (default: `qwen-plus`) |
| `LLM_BASE_URL` | For custom | Custom endpoint URL (custom-endpoint template) |
| `LLM_MODEL` | For custom | Model name for custom endpoint |
| `LLM_API_KEY` | For custom | API key for custom endpoint |
| `LLM_CONTEXT_WINDOW` | No | Context window size in tokens (default: 128000) |
| `LLM_MAX_TOKENS` | No | Max output tokens per response (default: 8192) |
| `LLM_HOST` | For isolation | LLM server IP (used by iptables rules) |
| `LLM_PORT` | For isolation | LLM server port (used by iptables rules) |
| `TLS_MODE` | No | TLS mode: `internal` (default), `acme`, `custom`, `off` |
| `DOMAIN` | For `acme` | Public domain for Let's Encrypt (e.g., `farm.example.com`) |
| `ACME_EMAIL` | No | Email for Let's Encrypt notifications |
| `BOT_PORT_START` | No | Start of port range (default: 3001). Dev-mode only — in compose mode, bots use path-based routing under `:8443` |
| `BOT_PORT_END` | No | End of port range (default: 3100). Dev-mode only |
| `HOST_BOTS_DIR` | Docker Compose | **Host-side** path to `bots/` dir — required when dashboard runs in a container (see workaround above) |
| `CADDY_PORT` | No | Caddy listening port (default: 8443) |
| `CADDY_ADMIN_URL` | Docker Compose | Caddy admin API URL (default: `http://caddy:2019`) |
| `PORTAL_URL` | No | External base URL override (auto-derived in `acme` mode from `DOMAIN`, not needed in `internal`/`custom` mode) |
| `BRAVE_API_KEY` | No | Brave Search API key for agent web search |
| `OPENCLAW_IMAGE` | No | Bot container image (default: `ghcr.io/openclaw/openclaw:latest`) |
| `BACKUP_DIR` | No | External backup directory. Empty = store in bot's `.backups/` dir |
| `BACKUP_INTERVAL_SECONDS` | No | How often to run scheduled backups (default: 3600, 0 = disabled) |
| `BACKUP_KEEP` | No | Max scheduled backups to retain per bot (default: 24) |
| `ADMIN_USER` | No | Default admin username (default: `admin`) |
| `ADMIN_PASSWORD` | No | Admin password (empty = auto-generated, printed to stdout with prominent banner) |
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

## TODO

- **Remove cookie-based WebSocket workaround** — OpenClaw PR #30228 (merged) fixes Control UI to include `basePath` in WebSocket URLs. Once a new `ghcr.io/openclaw/openclaw` image ships with this fix, we can remove the `cfm_bot` cookie hack and use native `basePath`-based routing instead. This simplifies the Caddy config significantly. Changes needed:
  1. Restore `basePath` in `_prepare_openclaw_home()` gateway config
  2. Remove `set_cookie`, `strip_path_prefix`, and root WebSocket `header_regexp` matcher from `_sync_caddy_config()`
  3. Flip startup migration in `_lifespan()` from "remove basePath" to "ensure basePath is set"
  4. Pin `OPENCLAW_IMAGE` to the release that includes the fix
