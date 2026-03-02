#!/usr/bin/env bash
# NOTE: Per-bot network isolation is now applied automatically by the dashboard.
# This script is retained for manual use or environments where automatic isolation
# is unavailable (e.g., Docker Desktop). It applies GLOBAL rules that affect ALL
# Docker containers — use with care on shared hosts.
#
# Idempotent iptables rules for bot network isolation.
# Bots can reach the internet and the LLM server, but not the LAN or each other.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/../.env"

if [ -f "$ENV_FILE" ]; then
    set -a
    source "$ENV_FILE"
    set +a
fi

: "${LLM_HOST:?LLM_HOST must be set}"
: "${LLM_PORT:?LLM_PORT must be set}"

# Flush existing DOCKER-USER rules and rebuild
iptables -F DOCKER-USER

# 1. Accept established / related connections
iptables -A DOCKER-USER -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT

# 2. Accept traffic to the local LLM server
iptables -A DOCKER-USER -d "$LLM_HOST" -p tcp --dport "$LLM_PORT" -j ACCEPT

# 3. Accept incoming connections to container services (LAN access)
#    After DNAT, destination ports are the container-internal ports:
#    - 8443:       Caddy HTTPS reverse proxy (dashboard/frontend)
#    - 80:         Caddy HTTP→HTTPS redirect
#    - 8080:       dashboard API
#    - 3000:       frontend
#    - 3001-3100:  Caddy HTTPS for bot Control UIs
BOT_PORT_START="${BOT_PORT_START:-3001}"
BOT_PORT_END="${BOT_PORT_END:-3100}"
iptables -A DOCKER-USER -p tcp --dport 8443 -j ACCEPT
iptables -A DOCKER-USER -p tcp --dport 80 -j ACCEPT
iptables -A DOCKER-USER -p tcp --dport 8080 -j ACCEPT
iptables -A DOCKER-USER -p tcp --dport 3000 -j ACCEPT
iptables -A DOCKER-USER -p tcp --dport "$BOT_PORT_START":"$BOT_PORT_END" -j ACCEPT

# 5. Drop all RFC1918 (private network) destinations — blocks bot-to-LAN and bot-to-bot
iptables -A DOCKER-USER -d 10.0.0.0/8 -j DROP
iptables -A DOCKER-USER -d 172.16.0.0/12 -j DROP
iptables -A DOCKER-USER -d 192.168.0.0/16 -j DROP

# 6. Return (implicit accept for internet traffic)
iptables -A DOCKER-USER -j RETURN

echo "Network isolation rules applied (LLM allowed: ${LLM_HOST}:${LLM_PORT})"
