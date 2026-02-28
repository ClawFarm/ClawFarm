# OpenClaw Fleet Manager

A Docker-based fleet manager for OpenClaw bots. Provides a FastAPI web dashboard to provision, manage, and network-isolate bot containers. Configure your LLM endpoint once — every bot inherits it automatically.

## Architecture

The fleet manager runs as a single dashboard container that communicates with the Docker daemon to create and manage bot containers. Each bot gets its own Docker bridge network, config directory, and auto-allocated port.

```
┌─────────────────────────────┐
│   Dashboard (FastAPI)       │  ← docker-compose service
│   - Web UI on :8080         │
│   - Manages bot lifecycle   │
│   - Mounts Docker socket    │
└─────────┬───────────────────┘
          │ Docker API
    ┌─────┼─────┬─────────┐
    ▼     ▼     ▼         ▼
  Bot A  Bot B  Bot C  ... Bot N
  :3001  :3002  :3003     :30xx
  (each on its own bridge network)
```

## Quick Start

1. Clone the repo and create your environment file:

```bash
cp .env.example .env
# Edit .env with your LLM server details
```

2. Run the init script:

```bash
bash scripts/init.sh
```

3. Open `http://localhost:8080` in your browser.

## Environment Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `LLM_HOST` | LLM server IP (for network isolation rules) | `10.88.100.186` |
| `LLM_PORT` | LLM server port (for network isolation rules) | `8000` |
| `LLM_BASE_URL` | Full LLM API base URL injected into bot configs | `http://10.88.100.186:8000/v1` |
| `LLM_MODEL` | Model name injected into bot configs | `qwen3.5-122b` |
| `BOT_PORT_START` | Start of bot port range | `3001` |
| `BOT_PORT_END` | End of bot port range | `3100` |
| `DASHBOARD_PORT` | Dashboard web UI port | `8080` |
| `OPENCLAW_IMAGE` | Docker image for bot containers | `ghcr.io/openclaw/openclaw:latest` |

## Network Isolation

Each bot runs on its own Docker bridge network. The `network/setup-isolation.sh` script applies iptables rules on the `DOCKER-USER` chain to enforce isolation:

1. **ACCEPT** established/related connections
2. **ACCEPT** traffic to the configured LLM server (`LLM_HOST:LLM_PORT`)
3. **DROP** all RFC1918 private network destinations (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16)
4. **RETURN** — allows internet access for everything else

This means bots can reach the internet and the LLM server, but cannot access the LAN or communicate with each other.

## API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/` | Serve the HTML dashboard |
| `GET` | `/api/bots` | List all bots with status, port, name |
| `POST` | `/api/bots` | Create a bot (`{name, soul?, extra_config?}`) |
| `POST` | `/api/bots/{name}/start` | Start a stopped bot |
| `POST` | `/api/bots/{name}/stop` | Stop a running bot |
| `POST` | `/api/bots/{name}/restart` | Restart a bot |
| `DELETE` | `/api/bots/{name}` | Remove bot container, network, and config |
| `GET` | `/api/bots/{name}/logs` | Last 200 lines of container logs |

## Running Tests

Tests validate pure logic (sanitization, config generation, deep merge, port allocation) without requiring a Docker daemon:

```bash
cd dashboard
pip install -r requirements.txt
pytest -v
```
