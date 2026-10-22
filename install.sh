#!/bin/sh
set -eu

port=${IGNITION_PORT:-4580}
tailnet_port=${IGNITION_TAILNET_PORT:-8445}
here=$(cd "$(dirname "$0")" && pwd)
unit="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/ignition.service"

mkdir -p "$(dirname "$unit")"
cat >"$unit" <<UNIT
[Unit]
Description=Ignition productivity app
After=network-online.target

[Service]
ExecStart=/usr/bin/python3 -I $here/server/ignition.py --port $port
Restart=always
RestartSec=2

[Install]
WantedBy=default.target
UNIT

systemctl --user daemon-reload
systemctl --user enable --now ignition.service
systemctl --user restart ignition.service

if command -v tailscale >/dev/null; then
    tailscale serve --bg --http="$tailnet_port" "http://127.0.0.1:$port"
fi
echo "ignition: http://127.0.0.1:$port (tailnet: http://<this machine>:$tailnet_port)"
