#!/bin/sh
# Install ignition on this Linux computer. Run it as yourself; it asks for your password when it needs it.
#
#   ./install.sh                 the app, plus the blocker for this computer
#   ./install.sh --no-blocker    just the app
#   ./install.sh --join URL      just the blocker, following the app on another computer
#
# Works straight from GitHub too, and running it again updates everything:
#   curl -fsSL https://raw.githubusercontent.com/ieatfood314/ignition/main/install.sh | sh
set -eu

repo=https://github.com/ieatfood314/ignition
port=${IGNITION_PORT:-4580}
tailnet_port=${IGNITION_TAILNET_PORT:-8445}

say() { printf '%s\n' "$*"; }
die() { printf 'ignition: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" != 0 ] || die "run this as yourself, not with sudo. It asks for your password when it needs it."

# Piped from curl there's no checkout around us: fetch one, then run the installer inside it.
here=$(cd "$(dirname "$0")" 2>/dev/null && pwd) || here=
if [ ! -f "$here/server/ignition.py" ]; then
    app="${XDG_DATA_HOME:-$HOME/.local/share}/ignition/app"
    say "Downloading ignition into $app"
    mkdir -p "$(dirname "$app")"
    if [ -d "$app/.git" ]; then
        git -C "$app" pull -q --ff-only
    elif [ ! -e "$app" ] && command -v git >/dev/null; then
        git clone -q --depth 1 "$repo.git" "$app"
    else
        tmp=$(mktemp -d)
        if command -v curl >/dev/null; then curl -fsSL "$repo/archive/refs/heads/main.tar.gz"
        else wget -qO- "$repo/archive/refs/heads/main.tar.gz"; fi | tar -xz -C "$tmp" --strip-components=1
        chmod 755 "$tmp"
        rm -rf "$app"
        mv "$tmp" "$app"
    fi
    exec sh "$app/install.sh" "$@"
fi

usage() {
    cat <<USAGE
usage: $0 [--no-blocker | --join URL]

  (nothing)       install the app, plus the blocker for this computer
  --no-blocker    just the app, nothing gets blocked on this computer
  --join URL      just the blocker, following the app on another computer, e.g. http://desk:8445

Advanced blocker options (programs, ports, hooks): sudo enforcer/install.sh --help
USAGE
    exit 1
}

blocker=1 join=
while [ $# -gt 0 ]; do
    case $1 in
        --no-blocker) blocker=; shift ;;
        --join) [ $# -ge 2 ] || usage; join=$2; shift 2 ;;
        *) usage ;;
    esac
done
[ -n "$blocker" ] || [ -z "$join" ] || usage
if [ -n "$join" ]; then
    case $join in *://*) ;; *) join="http://$join" ;; esac
    join=${join%/}
fi

[ "$(uname -s)" = Linux ] || die "the installer is for Linux. Elsewhere you can still run the app by hand:
  python3 server/ignition.py    (then open http://127.0.0.1:$port)"
command -v python3 >/dev/null || die "needs Python 3. Install it (e.g. sudo apt install python3) and run this again."
command -v systemctl >/dev/null || die "needs systemd to keep ignition running in the background."

reachable() { python3 -c 'import sys, urllib.request; urllib.request.urlopen(sys.argv[1] + "/api/ping", timeout=3)' "$1" 2>/dev/null; }

# ---- the app: a background service for your user, opened in a browser
if [ -z "$join" ]; then
    say "Starting the ignition app…"
    unit="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/ignition.service"
    mkdir -p "$(dirname "$unit")"
    cat >"$unit" <<UNIT
[Unit]
Description=Ignition productivity app
After=network-online.target

[Service]
ExecStart=$(command -v python3) -I $here/server/ignition.py --port $port
Restart=always
RestartSec=2

[Install]
WantedBy=default.target
UNIT
    systemctl --user daemon-reload
    systemctl --user enable --now ignition.service
    systemctl --user restart ignition.service
    i=0
    until reachable "http://127.0.0.1:$port"; do
        i=$((i + 1))
        [ $i -lt 20 ] || die "the app didn't start. See: journalctl --user -u ignition -n 30"
        sleep 0.5
    done
else
    reachable "$join" || die "can't reach the ignition app at $join. Check the address, and that both computers are on Tailscale."
fi

# ---- the blocker: a small root service that applies your block list during a burn
if [ -n "$blocker" ]; then
    say "Installing this computer's blocker (it needs your password)…"
    if [ -n "$join" ]; then set -- --server-url "$join"
    elif [ ! -f /etc/ignition/enforcer.json ]; then set -- --server-url "http://127.0.0.1:$port"
    else set --; fi
    sudo sh "$here/enforcer/install.sh" "$@" >/dev/null
fi

# ---- share the app with your other devices over Tailscale, if it's installed
remote=
if [ -z "$join" ] && command -v tailscale >/dev/null; then
    # Sharing needs Tailscale operator rights; fall back to sudo only if it won't ask for a password again.
    set -- serve --bg --http="$tailnet_port" "http://127.0.0.1:$port"
    if tailscale "$@" 2>/dev/null || { sudo -n true 2>/dev/null && sudo tailscale "$@"; }; then
        name=$(tailscale status --json 2>/dev/null |
            python3 -c 'import json, sys; print(json.load(sys.stdin)["Self"]["DNSName"].split(".")[0])' 2>/dev/null) ||
            name=$(hostname)
        remote="http://$name:$tailnet_port"
    else
        say "Couldn't share the app over Tailscale. To fix: sudo tailscale set --operator=$USER, then run this again."
    fi
fi

# ---- widgets for the KDE Plasma desktop, if this is one
widgets=
if command -v kpackagetool6 >/dev/null; then
    if sh "$here/plasma/install.sh" --server "${join:-http://127.0.0.1:$port}" >/dev/null; then widgets=1
    else say "Couldn't add the KDE Plasma widgets."; fi
fi

say ""
say "ignition is installed."
if [ -z "$join" ]; then
    say "  Open it:          http://127.0.0.1:$port"
    [ -z "$remote" ] || say "  Other devices:    $remote  (phone: Settings › Sync)"
else
    say "  This computer follows the app at $join"
fi
[ -z "$blocker" ] || say "  Blocking:         on for this computer. Choose what to block in the app: Settings › Blocking."
[ -z "$widgets" ] || say "  Plasma widgets:   right-click the desktop › Add Widgets, and search for ignition."
