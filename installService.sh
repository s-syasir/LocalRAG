#!/usr/bin/env bash
# Install LocalRAG as systemd *user* services, so it survives logout and reboot.
#
# User units rather than system units: everything runs as your user, out of your home
# directory and your venv, and needs no root beyond enabling linger.
#
# By default ONLY the API is installed. The Gradio UI has no authentication, so running it
# as a boot service exposes every indexed note to anyone who can reach the port. Serve the
# API and put a client with real accounts in front of it; launch the UI on demand with
# startApp.sh when you actually want it.
#
#   bash installService.sh              # the OpenAI-compatible API (default)
#   bash installService.sh --with-app   # also install the Gradio UI as a boot service
#   bash installService.sh --app-only   # only the Gradio UI
#   bash installService.sh --uninstall  # stop, disable and remove both units
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="${VENV_PATH:-$HOME/.venvs/localrag}"
UNIT_DIR="$HOME/.config/systemd/user"
WANT_APP=0   # opt-in: see the note above about the UI having no auth
WANT_API=1
UNINSTALL=0

for arg in "$@"; do
    case "$arg" in
        --api-only)  WANT_APP=0 ;;
        --with-app)  WANT_APP=1 ;;
        --app-only)  WANT_APP=1; WANT_API=0 ;;
        --uninstall) UNINSTALL=1 ;;
        -h|--help)   sed -n '2,15p' "$0"; exit 0 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

if [ "$UNINSTALL" = 1 ]; then
    for u in localrag.service localrag-api.service; do
        systemctl --user disable --now "$u" 2>/dev/null || true
        rm -f "$UNIT_DIR/$u"
    done
    systemctl --user daemon-reload
    echo "Removed. Linger left enabled; 'loginctl disable-linger $USER' if you want it off too."
    exit 0
fi

[ -x "$VENV/bin/python" ] || {
    echo "!! no python at $VENV/bin/python"
    echo "   Create the venv first, or set VENV_PATH. See the README."
    exit 1
}
[ -f "$SCRIPT_DIR/.env" ] || echo "!! warning: no .env in $SCRIPT_DIR (copy .env.example first)"

mkdir -p "$UNIT_DIR"

# If a headless Joplin client runs as its own unit, order after it: it owns the database
# this reads. Detected rather than assumed, so a desktop install does not gain a dependency
# on a unit that will never exist.
JOPLIN_DEP=""
if systemctl --user list-unit-files joplin-headless.service >/dev/null 2>&1 && \
   [ -n "$(systemctl --user list-unit-files --no-legend joplin-headless.service 2>/dev/null)" ]; then
    JOPLIN_DEP=" joplin-headless.service"
    echo "==> Found joplin-headless.service, ordering after it"
fi

write_unit() {
    local name="$1" desc="$2" script="$3"
    # Never silently replace an existing unit -- it may have been hand-tuned.
    if [ -f "$UNIT_DIR/$name" ]; then
        cp "$UNIT_DIR/$name" "$UNIT_DIR/$name.bak"
        echo "  backed up existing $name -> $name.bak"
    fi
    cat > "$UNIT_DIR/$name" <<EOF
[Unit]
Description=$desc
# Ollama is usually a container, so it cannot be an After= target. Restart=always covers
# it not being ready yet; the unit simply retries until it is.
After=network-online.target$JOPLIN_DEP
${JOPLIN_DEP:+Wants=${JOPLIN_DEP# }}

[Service]
WorkingDirectory=$SCRIPT_DIR
ExecStart=$VENV/bin/python $SCRIPT_DIR/$script
Restart=always
RestartSec=15

[Install]
WantedBy=default.target
EOF
    echo "  wrote $UNIT_DIR/$name"
}

echo "==> Writing units (repo=$SCRIPT_DIR, venv=$VENV)"
if [ "$WANT_APP" = 1 ]; then write_unit localrag.service     "LocalRAG - Gradio UI over Joplin notes" app.py; fi
if [ "$WANT_API" = 1 ]; then write_unit localrag-api.service "LocalRAG - OpenAI-compatible API"       openai_api.py; fi

# Without linger, user units start at LOGIN, not at boot -- so a headless box comes back
# from a reboot with nothing running and no error to explain why.
echo "==> Enabling linger for $USER"
if loginctl show-user "$USER" --property=Linger 2>/dev/null | grep -q "Linger=yes"; then
    echo "  already enabled."
elif loginctl enable-linger "$USER" 2>/dev/null; then
    echo "  enabled."
elif sudo -n loginctl enable-linger "$USER" 2>/dev/null; then
    echo "  enabled (via sudo)."
else
    echo "  !! could not enable. Run: sudo loginctl enable-linger $USER"
    echo "     Until then these services will NOT start at boot."
fi

echo "==> Enabling and starting"
systemctl --user daemon-reload
if [ "$WANT_APP" = 1 ]; then systemctl --user enable --now localrag.service; fi
if [ "$WANT_API" = 1 ]; then systemctl --user enable --now localrag-api.service; fi

echo
if [ "$WANT_APP" = 1 ]; then systemctl --user --no-pager --lines=0 status localrag.service     || true; fi
if [ "$WANT_API" = 1 ]; then systemctl --user --no-pager --lines=0 status localrag-api.service || true; fi

if [ "$WANT_APP" = 1 ]; then
    echo
    echo "!! localrag.service (Gradio UI) has NO authentication and is now enabled at boot."
    echo "   Firewall its port, or use --api-only and launch the UI with startApp.sh instead."
fi

cat <<EOF

Done. Useful commands:
  systemctl --user status  localrag.service localrag-api.service
  systemctl --user restart localrag-api.service
  journalctl --user -u localrag-api.service -f

The index is only rebuilt when ingest.py runs -- see "Re-index on a schedule" in the README.
Services load the index at startup, so restart them after a re-index (startHeadless.sh does).

The Gradio UI is not a boot service by default. Launch it when you want it:
  bash startApp.sh          /  bash startApp.sh --stop
EOF
