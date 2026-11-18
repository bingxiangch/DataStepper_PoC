#!/usr/bin/env bash
# Run the FastAPI controller and Vite dev server together.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="$(command -v python3)"
fi

if [[ ! -d "$ROOT/frontend/node_modules" ]]; then
  echo "frontend/node_modules is missing. Run: cd frontend && npm install" >&2
  exit 1
fi

cleanup() {
  trap - INT TERM EXIT
  kill 0 2>/dev/null || true
}
trap cleanup INT TERM EXIT

echo "API:  http://127.0.0.1:8000"
echo "App:  http://127.0.0.1:5173  (the /api proxy targets port 8000)"
echo

"$PYTHON" -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --reload &
(
  cd frontend
  npm run dev -- --host 127.0.0.1
) &
wait
