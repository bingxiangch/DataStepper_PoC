#!/usr/bin/env bash
# Start the built Data Agent Debugger on a single origin.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="$(command -v python3)"
fi

if [[ ! -f "$ROOT/frontend/dist/index.html" ]]; then
  echo "The frontend bundle is missing." >&2
  echo "Build it first:  cd frontend && npm install && npm run build" >&2
  exit 1
fi

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
echo "Data Agent Debugger: http://$HOST:$PORT"
exec "$PYTHON" -m uvicorn backend.app.main:app --host "$HOST" --port "$PORT"
