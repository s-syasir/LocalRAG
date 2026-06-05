#!/usr/bin/env bash

echo "==> Stopping LocalRAG app..."
if pkill -f "python app.py" 2>/dev/null; then
    echo "    App stopped."
else
    echo "    App was not running."
fi

echo "==> Stopping Ollama container..."
if docker ps --filter name=ollama --filter status=running --format '{{.Names}}' | grep -q '^ollama$'; then
    bash -ic "stopOllama"
else
    echo "    Ollama was not running."
fi

echo "Done."
