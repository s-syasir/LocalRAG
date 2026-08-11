#!/usr/bin/env bash
# Server counterpart to start.sh, for a headless host with no desktop session.
#
# Differences from start.sh, all of which come from the same fact -- there is nobody
# logged in to notice a failure:
#   * the model name is read from .env instead of being hardcoded, so the model this
#     pulls is by construction the model app.py will ask for
#   * Ollama is reached over HTTP, not `docker exec`, so it may live in a container,
#     on another host, or as a bare service
#   * the app is handed to systemd instead of nohup, so it survives logout and reboot
#   * a failed ingest aborts instead of leaving the app serving a stale index
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="${VENV_PATH:-$HOME/.venvs/localrag}"
UNIT="${LOCALRAG_UNIT:-localrag.service}"

cd "$SCRIPT_DIR"
[ -f .env ] || { echo "!! no .env -- copy .env.example and edit it"; exit 1; }
# shellcheck disable=SC1091
set -a; . ./.env; set +a

OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://localhost:11434}"
OLLAMA_MODEL="${OLLAMA_MODEL:-qwen2.5:7b-instruct}"

echo "==> Ollama at $OLLAMA_BASE_URL"
if ! curl -fsS --max-time 5 "$OLLAMA_BASE_URL/api/tags" >/dev/null 2>&1; then
    echo "!! unreachable. Ollama commonly binds a specific interface rather than"
    echo "   loopback, so check what it is actually listening on before assuming it is down."
    exit 1
fi

echo "==> Model $OLLAMA_MODEL"
if curl -fsS "$OLLAMA_BASE_URL/api/tags" | grep -q "\"$OLLAMA_MODEL\""; then
    echo "    present."
else
    echo "    pulling (this may take a while)..."
    curl -fsS "$OLLAMA_BASE_URL/api/pull" -d "{\"model\":\"$OLLAMA_MODEL\"}" >/dev/null
fi

echo "==> Indexing notes..."
"$VENV/bin/python" ingest.py

# The Chroma collection is opened once at import, so a re-index is invisible to a
# process that is already running. Restarting is what makes new notes reachable.
if systemctl --user list-unit-files "$UNIT" >/dev/null 2>&1 && \
   [ -n "$(systemctl --user list-unit-files --no-legend "$UNIT" 2>/dev/null)" ]; then
    echo "==> Restarting $UNIT"
    systemctl --user restart "$UNIT"
    systemctl --user --no-pager --lines=0 status "$UNIT" || true
else
    echo "!! $UNIT not installed -- see the headless deployment section of the README."
    echo "   Falling back to a foreground run; Ctrl-C stops it."
    exec "$VENV/bin/python" app.py
fi
