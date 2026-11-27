#!/usr/bin/env bash
# Obole : lancer le backend en mode développement (rechargement automatique du code).
# Usage : scripts/dev.sh [port]
# Le frontend (Vite) sera ajouté ici en Phase 2.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${1:-8000}"

cd "$REPO_ROOT/backend"
uv sync --quiet
echo "Backend : http://127.0.0.1:${PORT}  (documentation de l'API : http://127.0.0.1:${PORT}/docs)"
exec uv run uvicorn app.main:app --host 127.0.0.1 --port "$PORT" --reload --reload-dir app
