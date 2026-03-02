#!/bin/sh
# Restore per-bot network isolation rules on startup.
# Runs as an init container with CAP_NET_ADMIN + network_mode: host.
# Reads bot metadata from /data/bots and applies iptables chains for each
# bot that has network_isolation enabled (default: true).
set -e

BOTS_DIR="${BOTS_DIR:-/data/bots}"
LLM_HOST="${LLM_HOST:-}"
LLM_PORT="${LLM_PORT:-}"

restored=0
skipped=0
failed=0

for meta_file in "$BOTS_DIR"/*/.meta.json; do
    [ -f "$meta_file" ] || continue

    bot_dir="$(dirname "$meta_file")"
    bot_name="$(basename "$bot_dir")"

    # Check if network_isolation is explicitly false (default is true)
    isolation=$(jq -r '.network_isolation // true' "$meta_file" 2>/dev/null)
    if [ "$isolation" = "false" ]; then
        skipped=$((skipped + 1))
        continue
    fi

    network_name="openclaw-net-${bot_name}"

    # Look up Docker network ID via inspect
    network_id=$(docker network inspect "$network_name" --format '{{.Id}}' 2>/dev/null | head -c 12) || true
    if [ -z "$network_id" ]; then
        # Network doesn't exist (bot was deleted but dir remains, or not yet started)
        skipped=$((skipped + 1))
        continue
    fi

    chain="CF-${bot_name}"
    # Truncate chain name to 28 chars (iptables limit is 30, CF- prefix is 3)
    chain=$(echo "$chain" | head -c 28)

    # Build LLM allow rule
    llm_rule=""
    if [ -n "$LLM_HOST" ] && [ -n "$LLM_PORT" ]; then
        llm_rule="iptables -A $chain -d $LLM_HOST -p tcp --dport $LLM_PORT -j RETURN"
    fi

    # Apply rules
    if iptables -N "$chain" 2>/dev/null || iptables -F "$chain"; then
        iptables -A "$chain" -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
        [ -n "$llm_rule" ] && eval "$llm_rule"
        iptables -A "$chain" -d 10.0.0.0/8 -j DROP
        iptables -A "$chain" -d 172.16.0.0/12 -j DROP
        iptables -A "$chain" -d 192.168.0.0/16 -j DROP
        iptables -A "$chain" -j RETURN
        iptables -C DOCKER-USER -i "br-${network_id}" -j "$chain" 2>/dev/null || \
            iptables -I DOCKER-USER -i "br-${network_id}" -j "$chain"
        restored=$((restored + 1))
    else
        echo "WARN: Failed to create chain $chain for $bot_name"
        failed=$((failed + 1))
    fi
done

echo "Network isolation restored: ${restored} applied, ${skipped} skipped, ${failed} failed"
