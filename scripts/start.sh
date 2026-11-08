#!/usr/bin/env bash
# Start the service. ./scripts/start.sh            -> local uvicorn in the background (dotenvx decrypts .env)
#                    ./scripts/start.sh --docker   -> docker compose (image has the encrypted .env, key passed at runtime)
set -euo pipefail
cd "$(dirname "$0")/.."
export PORT="${PORT:-8080}"

if [ "${1:-}" = "--docker" ]; then
  [ -f .env.keys ] && export DOTENV_PRIVATE_KEY="$(sed -n 's/^DOTENV_PRIVATE_KEY=//p' .env.keys | head -1 | tr -d '"')"
  docker compose up -d --build --no-deps app   # firestore emulator is not used by the app yet
else
  mkdir -p data
  if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1 && ! { [ -f data/service.pid ] && kill -0 "$(cat data/service.pid)" 2>/dev/null; }; then
    echo "port $PORT is already in use by another process; pick one: PORT=8081 $0" >&2; exit 1
  fi
  if [ -f data/service.pid ] && kill -0 "$(cat data/service.pid)" 2>/dev/null; then echo "already running (pid $(cat data/service.pid))"; exit 0; fi
  nohup npx -y @dotenvx/dotenvx run -q -- uv run uvicorn app.web:app --host 127.0.0.1 --port "$PORT" > data/service.log 2>&1 &
  echo $! > data/service.pid
fi
for _ in $(seq 1 40); do
  curl -sf "http://127.0.0.1:$PORT/healthz" >/dev/null && { echo "up: http://127.0.0.1:$PORT"; exit 0; }
  sleep 1
done
echo "did not become healthy; see data/service.log (or: docker compose logs app)" >&2; exit 1
