#!/usr/bin/env bash
# Start the Election Night server (creates the venv on first run).
# Usage: ./run.sh            (PORT, HOST, ADMIN_TOKEN can be set via env)
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -x .venv/bin/uvicorn ]; then
  python3 -m venv .venv
  .venv/bin/pip install -r backend/requirements.txt
fi

cd backend
exec ../.venv/bin/uvicorn app.server:app --host "${HOST:-0.0.0.0}" --port "${PORT:-8000}"
