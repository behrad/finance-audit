#!/usr/bin/env bash
# Stop the service. ./scripts/stop.sh [--docker]
set -euo pipefail
cd "$(dirname "$0")/.."
if [ "${1:-}" = "--docker" ]; then docker compose down; exit 0; fi
if [ -f data/service.pid ]; then
  pid="$(cat data/service.pid)"
  # npx/uv spawn children: kill the whole process group, then fall back to the pid
  kill -- "-$(ps -o pgid= "$pid" | tr -d ' ')" 2>/dev/null || kill "$pid" 2>/dev/null || true
  rm -f data/service.pid
fi
pkill -f "uvicorn app.web:app --host 127.0.0.1 --port ${PORT:-8080}" 2>/dev/null || true
echo stopped
