#!/usr/bin/env bash
# Obole : mode développement (Ubuntu). Lance ensemble :
#   - le backend FastAPI (rechargement automatique du code Python), port 8000 ;
#   - le frontend Vite (rechargement instantané), port 5173, qui relaie /api vers le backend.
# Ouvrir http://localhost:5173 ; Ctrl+C arrête les deux.
#
# Usage : scripts/dev.sh [--backend-only]
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_PORT=8000

cd "$REPO_ROOT/backend"
uv sync --quiet

if [[ "${1:-}" == "--backend-only" ]]; then
  echo "Backend : http://127.0.0.1:${BACKEND_PORT}  (API : http://127.0.0.1:${BACKEND_PORT}/docs)"
  exec uv run uvicorn app.main:app --host 127.0.0.1 --port "$BACKEND_PORT" --reload --reload-dir app
fi

if [[ ! -d "$REPO_ROOT/frontend/node_modules" ]]; then
  (cd "$REPO_ROOT/frontend" && npm ci --no-audit --no-fund)
fi

uv run uvicorn app.main:app --host 127.0.0.1 --port "$BACKEND_PORT" --reload --reload-dir app &
BACKEND_PID=$!
trap 'kill "$BACKEND_PID" 2>/dev/null || true' EXIT INT TERM

cd "$REPO_ROOT/frontend"
echo "Appli : http://localhost:5173   API : http://127.0.0.1:${BACKEND_PORT}/docs"
OBOLE_BACKEND="http://127.0.0.1:${BACKEND_PORT}" npm run dev
