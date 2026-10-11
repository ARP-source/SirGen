#!/usr/bin/env bash
# Start the local vision service (instrument detector, anatomy detector, CVS classifier) on 127.0.0.1:8004.
# Uses the training venv (torch + ultralytics); the app talks to it over HTTP and never waits on it.
#   SENTINEL_MODELS   model folder (default ~/sentinel-train/models)
#   VISION_VENV       venv with torch + ultralytics (default ~/sentinel-train/.venv)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="${VISION_VENV:-$HOME/sentinel-train/.venv}"
exec "$VENV/bin/python" "$ROOT/vision/server.py"
