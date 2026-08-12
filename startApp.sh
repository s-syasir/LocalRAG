#!/usr/bin/env bash
# Launch the Gradio UI on demand.
#
# The Gradio app has NO AUTHENTICATION: anyone who reaches its port can read every note in
# the index. That is fine on a desktop and not fine as a service left running on a server,
# so on a server it is deliberately not enabled at boot -- start it when you want it, stop
# it when you are done. For always-on access, serve openai_api.py and put a client that has
# real accounts in front of it instead.
#
#   bash startApp.sh            # start it
#   bash startApp.sh --stop     # stop it
#   bash startApp.sh --status   # is it up?
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="${VENV_PATH:-$HOME/.venvs/localrag}"
UNIT="${LOCALRAG_APP_UNIT:-localrag.service}"
PIDFILE="${TMPDIR:-/tmp}/localrag-app.pid"
LOGFILE="${TMPDIR:-/tmp}/localrag-app.log"

cd "$SCRIPT_DIR"
[ -f .env ] && { set -a; . ./.env; set +a; }
PORT="${APP_PORT:-7860}"

# Prefer the systemd unit when it exists: it keeps logs in the journal and restarts on crash.
# A *disabled* unit can still be started by hand, which is exactly the behaviour wanted here.
have_unit() { [ -n "$(systemctl --user list-unit-files --no-legend "$UNIT" 2>/dev/null)" ]; }

case "${1:-start}" in
  --status|status)
      if have_unit; then
          systemctl --user --no-pager --lines=0 status "$UNIT" || true
      elif [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
          echo "running (pid $(cat "$PIDFILE"), not under systemd)"
      else
          echo "not running"
      fi
      curl -s -o /dev/null -w "http://localhost:$PORT -> %{http_code}\n" "http://localhost:$PORT" || true
      exit 0 ;;

  --stop|stop)
      if have_unit; then
          echo "==> Stopping $UNIT"
          systemctl --user stop "$UNIT"
      fi
      if [ -f "$PIDFILE" ]; then
          kill "$(cat "$PIDFILE")" 2>/dev/null || true
          rm -f "$PIDFILE"
      fi
      echo "Stopped."
      exit 0 ;;

  start) ;;
  *) echo "usage: $0 [start|--stop|--status]" >&2; exit 2 ;;
esac

OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://localhost:11434}"
if ! curl -fsS --max-time 5 "$OLLAMA_BASE_URL/api/tags" >/dev/null 2>&1; then
    echo "!! Ollama unreachable at $OLLAMA_BASE_URL -- the UI will load but every answer will fail."
fi

echo "!! This UI has no authentication. Anyone who can reach port $PORT can read your notes."

if have_unit; then
    echo "==> Starting $UNIT"
    systemctl --user start "$UNIT"
    systemctl --user --no-pager --lines=0 status "$UNIT" || true
else
    [ -x "$VENV/bin/python" ] || { echo "!! no python at $VENV/bin/python"; exit 1; }
    echo "==> No $UNIT installed; running directly"
    nohup "$VENV/bin/python" app.py > "$LOGFILE" 2>&1 &
    echo $! > "$PIDFILE"
    echo "    pid $(cat "$PIDFILE")   logs: $LOGFILE"
fi

echo
echo "Stop it again with:  bash startApp.sh --stop"
