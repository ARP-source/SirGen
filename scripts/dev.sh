#!/usr/bin/env bash
# Run the backend (FastAPI, :7000) and the frontend (Vite, :5173) together. Ctrl-C stops both.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${PYTHON:-python3}"
[ -x "$ROOT/.venv/bin/python" ] && PY="$ROOT/.venv/bin/python"

"$PY" -m uvicorn backend.app:app --host 0.0.0.0 --port 7000 --timeout-graceful-shutdown 2 &
BACK=$!
(cd frontend && npm run dev) &
FRONT=$!
trap 'kill $BACK $FRONT 2>/dev/null; wait 2>/dev/null' INT TERM EXIT
echo "backend  http://localhost:7000/health"
echo "screens  http://localhost:5173/surgical  http://localhost:5173/agents  http://localhost:5173/dashboard  http://localhost:5173/control"
wait
