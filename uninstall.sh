#!/bin/sh
# Remove ignition from this computer: the app, the blocker and the browser settings it changed.
# Your lists and burn history stay in ~/.local/share/ignition; delete that folder to remove them too.
set -eu

port=${IGNITION_PORT:-4580}
tailnet_port=${IGNITION_TAILNET_PORT:-8445}
data="${XDG_DATA_HOME:-$HOME/.local/share}/ignition"
unit="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/ignition.service"

die() { printf 'ignition: %s\n' "$*" >&2; exit 1; }
[ "$(id -u)" != 0 ] || die "run this as yourself, not with sudo. It asks for your password when it needs it."

# Uninstalling is not a way out of a burn.
python3 -I - "http://127.0.0.1:$port" <<'PY' || die "a burn is running. Uninstall after it ends."
import json, sys, time, urllib.request
try:
    with urllib.request.urlopen(sys.argv[1] + "/api/ignite", timeout=3) as r:
        burning = json.load(r).get("phase") == "burning"
except Exception:
    try:
        with open("/var/lib/ignition/state.json") as f:
            s = json.load(f)
        burning = bool(s.get("active")) and time.time() < s.get("ends_at", 0)
    except Exception:
        burning = False
sys.exit(1 if burning else 0)
PY

if [ -f "$unit" ]; then
    echo "Removing the app…"
    systemctl --user disable --now ignition.service 2>/dev/null || true
    rm -f "$unit"
    systemctl --user daemon-reload
    if command -v tailscale >/dev/null; then
        tailscale serve --http="$tailnet_port" off 2>/dev/null || sudo tailscale serve --http="$tailnet_port" off 2>/dev/null || true
    fi
fi
[ ! -d "$data/app" ] || rm -rf "$data/app"
rm -rf "${XDG_STATE_HOME:-$HOME/.local/state}/ignition"

if [ -f /etc/systemd/system/ignition-enforcer.service ]; then
    echo "Removing the blocker (it needs your password)…"
    sudo systemctl disable --now ignition-enforcer.service
    sudo rm -f /etc/systemd/system/ignition-enforcer.service
    sudo systemctl daemon-reload
    sudo rm -rf /usr/local/lib/ignition /etc/ignition /var/lib/ignition
    sudo rm -f /etc/sudoers.d/ignition-update
    sudo rm -f /etc/opt/chrome/policies/managed/ignition.json /etc/chromium/policies/managed/ignition.json \
        /etc/brave/policies/managed/ignition.json
    # Undo the DNS-over-HTTPS lock the blocker put on Firefox-family browsers, leaving other policies alone.
    sudo python3 -I - <<'PY'
import json
from pathlib import Path

files = [Path("/etc/firefox/policies/policies.json"),
         *Path("/usr/lib").glob("*/distribution/policies.json"), *Path("/opt").glob("*/distribution/policies.json")]
for p in files:
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        continue
    pol = data.get("policies", {})
    if pol.get("DNSOverHTTPS") != {"Enabled": False, "Locked": True}:
        continue
    del pol["DNSOverHTTPS"]
    if pol or len(data) > 1:
        p.write_text(json.dumps(data, indent=2) + "\n")
    else:
        p.unlink()
PY
fi

if command -v kpackagetool6 >/dev/null; then
    for w in ca.heeney.ignition ca.heeney.ignition.next ca.heeney.ignition.goals; do
        kpackagetool6 -t Plasma/Applet -r "$w" >/dev/null 2>&1 || true
    done
    rm -f "${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps/ca.heeney.ignition.svg"
fi

echo "ignition is uninstalled. Your lists are still in $data (delete it to remove them)."
