# ClawFleetManager

A Docker-based fleet manager for OpenClaw bots. Provides a Next.js dashboard to provision, manage, and network-isolate bot containers with per-bot metrics, backups, and rollback. Configure your LLM endpoint once — every bot inherits it automatically.

## Architecture

```
┌──────────────────────────────┐
│  Frontend (Next.js :3000)    │  ← public entry point
│  - Dashboard UI              │
│  - Bot detail + metrics      │
│  - Proxies /api/* to backend │
└──────────┬───────────────────┘
           │ HTTP proxy
┌──────────▼───────────────────┐
│  Backend (FastAPI :8080)     │  ← internal only
│  - Bot lifecycle API         │
│  - Metrics, backup, rollback │
│  - Mounts Docker socket      │
└──────────┬───────────────────┘
           │ Docker API
    ┌──────┼──────┬──────────┐
    ▼      ▼      ▼          ▼
  Bot A  Bot B  Bot C  ... Bot N
  :3001  :3002  :3003     :30xx
  (each on its own bridge network)
```

## Quick Start

1. Create your environment file:

```bash
cp .env.example .env
# Edit .env with your LLM server details
```

2. Run the init script:

```bash
bash scripts/init.sh
```

3. Open `http://localhost:3000` in your browser.

## Environment Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `LLM_HOST` | LLM server IP (for network isolation rules) | `10.88.100.186` |
| `LLM_PORT` | LLM server port (for network isolation rules) | `8000` |
| `LLM_BASE_URL` | Full LLM API base URL injected into bot configs | `http://10.88.100.186:8000/v1` |
| `LLM_MODEL` | Model name injected into bot configs | `qwen3.5-122b` |
| `BOT_PORT_START` | Start of bot port range | `3001` |
| `BOT_PORT_END` | End of bot port range | `3100` |
| `DASHBOARD_PORT` | Frontend port (default 3000) | `3000` |
| `OPENCLAW_IMAGE` | Docker image for bot containers | `ghcr.io/openclaw/openclaw:latest` |

## Bot Operations

| Operation | Description |
|-----------|-------------|
| **Create** | New bot from template with auto-allocated port |
| **Duplicate** | Copy a bot's actual config + soul to a new bot |
| **Fork** | Duplicate with lineage tracking (forked_from metadata) |
| **Backup** | Snapshot current config + soul to timestamped backup |
| **Rollback** | Restore from backup (auto-backs up current state first) |
| **Start/Stop/Restart** | Container lifecycle control |
| **Delete** | Remove container, network, and config directory |

## Network Isolation

Each bot runs on its own Docker bridge network. The `network/setup-isolation.sh` script applies iptables rules on the `DOCKER-USER` chain:

1. **ACCEPT** established/related connections
2. **ACCEPT** traffic to the configured LLM server (`LLM_HOST:LLM_PORT`)
3. **ACCEPT** incoming connections to container service ports (8080 API, 3000 bot UIs) — allows LAN access
4. **DROP** all RFC1918 private network destinations (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16)
5. **RETURN** — allows internet access for everything else

Bots can reach the internet and the LLM server, but cannot access the LAN or each other.

## API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/bots` | List all bots with status, port, metadata |
| `POST` | `/api/bots` | Create a bot (`{name, soul?, extra_config?}`) |
| `POST` | `/api/bots/{name}/start` | Start a stopped bot |
| `POST` | `/api/bots/{name}/stop` | Stop a running bot |
| `POST` | `/api/bots/{name}/restart` | Restart a bot |
| `DELETE` | `/api/bots/{name}` | Remove bot container, network, and config |
| `GET` | `/api/bots/{name}/logs` | Last 200 lines of container logs |
| `POST` | `/api/bots/{name}/duplicate` | Duplicate bot (`{new_name}`) |
| `POST` | `/api/bots/{name}/fork` | Fork with lineage (`{new_name}`) |
| `POST` | `/api/bots/{name}/backup` | Create backup snapshot |
| `GET` | `/api/bots/{name}/backups` | List backup history |
| `POST` | `/api/bots/{name}/rollback` | Rollback to backup (`{timestamp}`) |
| `GET` | `/api/bots/{name}/meta` | Get bot metadata |
| `GET` | `/api/bots/{name}/stats` | Live container metrics |
| `GET` | `/api/bots/{name}/detail` | Full detail (config, soul, meta, stats) |

## Running Tests

```bash
cd dashboard
uv run pytest -v
```
