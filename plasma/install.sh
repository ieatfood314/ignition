#!/bin/sh
# Install (or update) ignition's KDE Plasma widgets for this user: Ignition, Next up and Burn goals.
# install.sh runs this for you when it finds Plasma 6.
#
#   plasma/install.sh [--server URL]     the server the widgets talk to; by default the app on this
#                                        computer, else the one this computer's blocker follows
set -eu

here=$(cd "$(dirname "$0")" && pwd)
widgets="ignition next goals"
icons="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"

die() { printf 'ignition: %s\n' "$*" >&2; exit 1; }
command -v kpackagetool6 >/dev/null || die "the widgets need KDE Plasma 6 (kpackagetool6 not found)."

server=
if [ "${1:-}" = --server ] && [ -n "${2:-}" ]; then
    server=${2%/}
elif [ -f "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/ignition.service" ]; then
    port=$(sed -n 's/^ExecStart=.* --port \([0-9]*\).*/\1/p' "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/ignition.service")
    server="http://127.0.0.1:${port:-4580}"
elif [ -r /etc/ignition/enforcer.json ]; then
    server=$(python3 -I -c 'import json, sys; print(json.load(open(sys.argv[1]))["server_url"])' /etc/ignition/enforcer.json 2>/dev/null) || server=
fi
[ -n "$server" ] || server="http://127.0.0.1:${IGNITION_PORT:-4580}"
version=$(git -C "$here" rev-list --count HEAD 2>/dev/null) || version=1

# Each widget gets its own copy of the shared pieces, and this computer's server as its default.
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
for w in $widgets; do
    cp -r "$here/$w" "$stage/$w"
    cp "$here"/shared/* "$stage/$w/contents/ui/"
    sed -i "s|@SERVER_URL@|$server|" "$stage/$w/contents/config/main.xml"
    sed -i "s|@VERSION@|$version|" "$stage/$w/metadata.json"
    kpackagetool6 -t Plasma/Applet -u "$stage/$w" >/dev/null 2>&1 ||
        kpackagetool6 -t Plasma/Applet -i "$stage/$w" >/dev/null ||
        die "couldn't install the $w widget"
done

mkdir -p "$icons"
cp "$here/flame.svg" "$icons/ca.heeney.ignition.svg"
echo "$server"
