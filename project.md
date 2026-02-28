# OpenClaw Fleet Manager — Implementation Spec

You are building a Docker-based fleet manager for OpenClaw bots. The system has a FastAPI web dashboard that provisions, manages, and isolates bot containers. The operator configures their LLM endpoint once; every bot inherits it automatically.

## Project Structure

```
openclaw-fleet/
├── README.md
├── .env.example
├── .gitignore
├── docker-compose.yml          # Dashboard service only
├── dashboard/
│   ├── Dockerfile              # Python 3.12 slim, FastAPI
│   ├── requirements.txt        # fastapi, uvicorn, docker, jinja2, pydantic
│   ├── app.py                  # Fleet manager API + bot lifecycle
│   ├── pytest.ini
│   ├── templates/
│   │   └── index.html          # Single-page dashboard UI
│   └── tests/
│       ├── __init__.py
│       └── test_fleet.py
├── bot-template/
│   ├── config.template.json    # Base bot config with LLM placeholders
│   └── SOUL.md                 # Default personality
├── network/
│   └── setup-isolation.sh      # iptables rules
└── scripts/
    └── init.sh                 # First-time setup
```

## Core Design Decisions

### Docker Client Must Be Lazy

Do NOT initialize `docker.from_env()` at module level. Use a lazy getter so pure functions (sanitization, config gen, deep merge) are importable and testable without a running Docker daemon.

```python
_client = None

def _get_client():
    global _client
    if _client is None:
        _client = docker.from_env()
    return _client
```

### Bot Lifecycle

Each bot gets:
- A sanitized container name: `openclaw-bot-{name}`
- Its own Docker bridge network: `openclaw-net-{name}`
- A host directory `./bots/{name}/` with generated `config.json` and `SOUL.md`
- An auto-allocated port from the configured range (default 3001–3100)
- Labels for discovery: `openclaw.bot=true`, `openclaw.port={port}`, `openclaw.name={name}`

Creation flow:
1. Sanitize name (lowercase, alphanum + hyphens, max 48 chars, reject empty)
2. Allocate next free port by scanning existing container labels
3. Generate config from template, injecting LLM_BASE_URL and LLM_MODEL
4. Write config.json and SOUL.md to `./bots/{name}/`
5. Create per-bot Docker bridge network
6. Start container with volume mount, port binding, labels, auto-restart

### Network Isolation Model

Bots must be able to reach the internet and the operator's local LLM server, but nothing else on the LAN, and not each other.

Each bot runs on its own bridge network (no shared networks between bots). The `setup-isolation.sh` script applies iptables rules on the `DOCKER-USER` chain:

1. ACCEPT established/related connections
2. ACCEPT traffic to `LLM_HOST:LLM_PORT` (the local inference server exception)
3. DROP all RFC1918 destinations (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16)
4. Implicit ACCEPT for everything else (internet)

The script must be idempotent — flush and rebuild the rules on each run. It requires `LLM_HOST` and `LLM_PORT` from `.env`.

### Config Generation

`bot-template/config.template.json` is the base config. At bot creation, the dashboard:
1. Loads the template
2. Sets `llm.provider = "openai-compatible"`, `llm.baseUrl = LLM_BASE_URL`, `llm.model = LLM_MODEL`
3. Deep-merges any `extra_config` the user provided (recursive dict merge, lists replace, doesn't mutate source)
4. Writes the result to `./bots/{name}/config.json`
5. Writes SOUL content (user-provided or default from `bot-template/SOUL.md`) to `./bots/{name}/SOUL.md`

The template should include sensible defaults: gateway on port 3000, compaction enabled (maxMessages: 50), memory enabled, empty channels array.

## Dashboard API

All endpoints on the FastAPI app:

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/` | Serve the HTML dashboard |
| GET | `/api/bots` | List all bots with status, port, name |
| POST | `/api/bots` | Create a bot. Body: `{name, soul?, extra_config?}` |
| POST | `/api/bots/{name}/start` | Start a stopped bot |
| POST | `/api/bots/{name}/stop` | Stop a running bot |
| POST | `/api/bots/{name}/restart` | Restart a bot |
| DELETE | `/api/bots/{name}` | Stop + remove container, network, and config dir |
| GET | `/api/bots/{name}/logs` | Return last 200 lines of container logs |

Bot info response shape: `{name, status, port, container_name}`

Status comes from `container.status` — typically "running", "exited", "created".

## Dashboard UI

Single HTML file with embedded CSS and vanilla JS (no frameworks). Dark theme, monospace font.

Features:
- Grid of bot cards showing name, colored status badge, port
- Each card has: "Open UI" link (to `http://{host}:{port}`), Start/Stop/Restart buttons, Logs button (opens modal), Delete button (with confirm)
- "New Bot" form at top: name input, SOUL textarea, Create button
- Toast notifications for action results
- Auto-refresh bot list on actions

## Environment Variables

```env
LLM_HOST=10.88.100.186
LLM_PORT=8000
LLM_BASE_URL=http://10.88.100.186:8000/v1
LLM_MODEL=qwen3.5-122b
BOT_PORT_START=3001
BOT_PORT_END=3100
DASHBOARD_PORT=8080
OPENCLAW_IMAGE=ghcr.io/openclaw/openclaw:latest
```

## docker-compose.yml

Single service: the dashboard. Mounts `/var/run/docker.sock` (required for container management), mounts `./bots` and `./bot-template` as volumes. Exposes `DASHBOARD_PORT`.

## init.sh

Sequential steps:
1. Check `.env` exists, source it
2. Pull `OPENCLAW_IMAGE`
3. Build the dashboard image
4. Run `network/setup-isolation.sh` (needs sudo)
5. `docker compose up -d`

## Test Strategy

Tests live in `dashboard/tests/test_fleet.py`. They validate pure logic without requiring Docker:

**Name Sanitization** — security-critical:
- Converts to lowercase, allows alphanum + hyphens
- Strips special characters, collapses consecutive hyphens
- Truncates to 48 chars
- Rejects path traversal attempts (`../etc/passwd` → sanitized)
- Raises on empty-after-sanitization

**Deep Merge:**
- Flat key override works
- Nested dicts merge recursively
- Override dict is not mutated

**Config Generation** (use tmp_path fixture):
- LLM fields populated from env vars
- SOUL.md written to workspace
- Default SOUL used when user provides blank
- extra_config merges into generated config
- Workspace directory created

**Port Allocation** (mock `_get_client`):
- Skips ports already claimed by existing containers
- Raises when port range exhausted

All tests must pass without a Docker daemon. Mock `_get_client` for anything that touches Docker. Use `monkeypatch` for env vars and config values. Use `tmp_path` for filesystem tests.

## README.md

Document the architecture, network isolation model, deploy flow, and env vars. No code in the README — the implementation is the source of truth. The README explains the what and why.

## Implementation Order

1. `.env.example`, `.gitignore`
2. `bot-template/config.template.json` and `SOUL.md`
3. `dashboard/requirements.txt`
4. `dashboard/app.py` — all API logic with lazy Docker client
5. `dashboard/templates/index.html`
6. `dashboard/Dockerfile`
7. `docker-compose.yml`
8. `network/setup-isolation.sh`
9. `scripts/init.sh`
10. `dashboard/tests/test_fleet.py` and `pytest.ini`
11. `README.md`
12. Run tests, verify 15/15 pass
