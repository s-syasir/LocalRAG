#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="${VENV_PATH:-$HOME/.venvs/localrag}"

# Read the model from .env rather than hardcoding it here: a second copy of the name
# silently drifts, and the failure is this script pulling one model while app.py asks
# Ollama for another.
cd "$SCRIPT_DIR"
[ -f .env ] && { set -a; . ./.env; set +a; }
MODEL="${OLLAMA_MODEL:-qwen2.5:7b-instruct}"

echo "==> Checking Ollama container..."
if ! docker ps --filter name=ollama --filter status=running --format '{{.Names}}' | grep -q '^ollama$'; then
    echo "    Starting Ollama..."
    bash -ic "startOllama"
    echo "    Waiting for Ollama to be ready..."
    sleep 5
else
    echo "    Ollama already running."
fi

echo "==> Checking model $MODEL..."
if ! docker exec ollama ollama list | grep -q "^$MODEL"; then
    echo "    Pulling $MODEL (this may take a while)..."
    docker exec ollama ollama pull "$MODEL"
else
    echo "    Model already present."
fi

echo "==> Ingesting Joplin notes..."
source "$VENV/bin/activate"
python ingest.py

echo "==> Starting app..."
nohup python app.py > /tmp/localrag_app.log 2>&1 & disown $!
echo "    App PID: $!"
echo "    Logs:    tail -f /tmp/localrag_app.log"
echo ""
echo "Open http://localhost:7860"
