#!/usr/bin/env bash
# First-time setup for OpenClaw Fleet Manager
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${SCRIPT_DIR}/.."

cd "$PROJECT_DIR"

# 1. Check .env exists and source it
if [ ! -f .env ]; then
    echo "Error: .env file not found. Copy .env.example to .env and configure it."
    exit 1
fi

set -a
source .env
set +a

# 2. Pull the OpenClaw image
echo "Pulling ${OPENCLAW_IMAGE}..."
docker pull "$OPENCLAW_IMAGE"

# 3. Build the dashboard image
echo "Building dashboard..."
docker compose build

# 4. Apply network isolation rules
echo "Applying network isolation rules (requires sudo)..."
sudo bash network/setup-isolation.sh

# 5. Start the dashboard
echo "Starting dashboard..."
docker compose up -d

echo "OpenClaw Fleet Manager is running on port ${DASHBOARD_PORT:-8080}"
