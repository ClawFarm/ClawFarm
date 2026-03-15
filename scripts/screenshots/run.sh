#!/usr/bin/env bash
# Capture polished screenshots for README/website.
# Starts the frontend dev server, runs Playwright capture, then stops the server.
#
# Usage: ./scripts/screenshots/run.sh
# Output: assets/screenshots/*.png

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
FRONTEND_DIR="$PROJECT_ROOT/frontend"
BASE_URL="${BASE_URL:-http://localhost:3000}"

# ---------------------------------------------------------------------------
# 1. Install deps if needed
# ---------------------------------------------------------------------------
if [ ! -d "$SCRIPT_DIR/node_modules" ]; then
  echo "Installing screenshot tool dependencies..."
  (cd "$SCRIPT_DIR" && npm install --silent)
  npx playwright install chromium
fi

if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
  echo "Installing frontend dependencies..."
  (cd "$FRONTEND_DIR" && npm install --silent)
fi

# ---------------------------------------------------------------------------
# 2. Start frontend dev server in background
# ---------------------------------------------------------------------------
cleanup() {
  if [ -n "${FRONTEND_PID:-}" ]; then
    echo "Stopping frontend dev server (PID $FRONTEND_PID)..."
    kill "$FRONTEND_PID" 2>/dev/null || true
    wait "$FRONTEND_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

echo "Starting frontend dev server..."
(cd "$FRONTEND_DIR" && npm run dev -- --port 3000 > /dev/null 2>&1) &
FRONTEND_PID=$!

# Wait for the server to be ready
echo -n "Waiting for $BASE_URL"
for i in $(seq 1 30); do
  if curl -s -o /dev/null "$BASE_URL" 2>/dev/null; then
    echo " ready!"
    break
  fi
  if [ "$i" -eq 30 ]; then
    echo " timed out after 30s"
    exit 1
  fi
  echo -n "."
  sleep 1
done

# ---------------------------------------------------------------------------
# 3. Run screenshot capture
# ---------------------------------------------------------------------------
(cd "$SCRIPT_DIR" && npx tsx capture.ts)

# ---------------------------------------------------------------------------
# 4. Sync to website if it exists
# ---------------------------------------------------------------------------
WEBSITE_DIR="$PROJECT_ROOT/../clawfarm.dev/public/screenshots"
if [ -d "$WEBSITE_DIR" ]; then
  cp "$PROJECT_ROOT/assets/screenshots/"*.png "$WEBSITE_DIR/"
  echo "Synced to clawfarm.dev/public/screenshots/"
fi

# ---------------------------------------------------------------------------
# 5. Cleanup (handled by trap)
# ---------------------------------------------------------------------------
echo ""
echo "Screenshots saved to assets/screenshots/"
