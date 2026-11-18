#!/bin/sh
# Installs (or updates) the screen-time tracker on a KDE Plasma desktop: a user service that records which
# window is in front and sends it to the ignition server. Takes over from lightcurve's tracker, keeping this
# computer's id and anything it hadn't sent yet.
#
#   tracker/install.sh [--server URL]    by default, the server this computer's blocker or app uses
set -eu

here=$(cd "$(dirname "$0")/.." && pwd)
die() { printf 'ignition tracker: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" != 0 ] || die "run this as yourself, not with sudo."
python3 -c 'import dbus, gi' 2>/dev/null || die "needs python-dbus and python-gobject (on Arch: sudo pacman -S python-dbus python-gobject)."

server=
if [ "${1:-}" = --server ] && [ -n "${2:-}" ]; then
    server=${2%/}
elif [ -f "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/ignition.service" ]; then
    port=$(sed -n 's/^ExecStart=.* --port \([0-9]*\).*/\1/p' "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/ignition.service")
    server="http://127.0.0.1:${port:-4580}"
elif [ -r /etc/ignition/enforcer.json ]; then
    server=$(python3 -I -c 'import json, sys; print(json.load(open(sys.argv[1]))["server_url"])' /etc/ignition/enforcer.json 2>/dev/null) || server=
fi

# The settings: the server, and the name lightcurve's tracker went by if there was one.
python3 -I - "$server" <<'PY'
import json, os, sys
conf = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
path = os.path.join(conf, "ignition", "tracker.json")
def read(p):
    try:
        with open(p) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}
cfg = read(path) or {k: v for k, v in read(os.path.join(conf, "lightcurve", "config.json")).items() if k in ("hub", "name")}
if sys.argv[1]:
    cfg["hub"] = sys.argv[1]
os.makedirs(os.path.dirname(path), exist_ok=True)
with open(path + ".tmp", "w") as f:
    json.dump(cfg, f, indent=2)
    f.write("\n")
os.replace(path + ".tmp", path)
PY

unit="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/ignition-tracker.service"
mkdir -p "$(dirname "$unit")"
cat >"$unit" <<UNIT
[Unit]
Description=ignition screen-time tracker
PartOf=graphical-session.target
After=graphical-session.target

[Service]
ExecStart=$(command -v python3) -I $here/tracker/ignition-tracker
Restart=on-failure
RestartSec=5

[Install]
WantedBy=graphical-session.target
UNIT

# lightcurve's tracker would count this computer twice.
systemctl --user disable --now lightcurve-tracker.service 2>/dev/null || true
systemctl --user daemon-reload
systemctl --user enable ignition-tracker.service >/dev/null 2>&1
if systemctl --user is-active -q graphical-session.target; then
    systemctl --user restart ignition-tracker.service
fi
echo "${server:-no server}"
