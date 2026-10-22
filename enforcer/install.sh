#!/bin/sh
set -eu

usage() {
    cat <<USAGE
usage: sudo $0 --server-url URL --t3-user USER [--host NAME] [--t3-ports 3773[,13773]] [--block lutris[,other]] [--notify]

  --server-url   where the ignition server is reachable from this machine
  --t3-user      user whose T3 Code server should keep running (its cgroups stay allowed)
  --host         name shown on the timer page (default: hostname)
  --t3-ports     loopback ports to cut off during a session (default: 3773)
  --block        programs to kill if started during a session
  --notify       send a desktop notification to --t3-user when something is blocked
USAGE
    exit 1
}

url= user= host=$(hostname -s) ports=3773 block= notify=
while [ $# -gt 0 ]; do
    case $1 in
        --server-url) url=$2; shift 2 ;;
        --t3-user) user=$2; shift 2 ;;
        --host) host=$2; shift 2 ;;
        --t3-ports) ports=$2; shift 2 ;;
        --block) block=$2; shift 2 ;;
        --notify) notify=1; shift ;;
        *) usage ;;
    esac
done
[ -n "$url" ] && [ -n "$user" ] || usage
[ "$(id -u)" = 0 ] || { echo "run with sudo" >&2; exit 1; }
command -v nft >/dev/null || { echo "nft not found; install nftables" >&2; exit 1; }

here=$(cd "$(dirname "$0")" && pwd)
uid=$(id -u "$user")
app="user.slice/user-$uid.slice/user@$uid.service/app.slice"

install -D -m 0755 "$here/ignition-enforcer.py" /usr/local/lib/ignition/ignition-enforcer.py
install -D -m 0644 "$here/ignition-enforcer.service" /etc/systemd/system/ignition-enforcer.service
mkdir -p /etc/ignition

python3 -I - "$url" "$host" "$ports" "$block" "$app" "${notify:+$user}" >/etc/ignition/enforcer.json <<'PY'
import json, sys
url, host, ports, block, app, notify = sys.argv[1:]
cfg = {
    "host": host,
    "server_url": url,
    "poll_seconds": 5,
    "t3": {
        "ports": [int(p) for p in ports.split(",") if p],
        "allow_cgroups": [f"{app}/t3code.service", f"{app}/app-t3code.slice"],
    },
    "block_processes": [{"name": n} for n in block.split(",") if n],
}
if notify:
    cfg["notify_user"] = notify
print(json.dumps(cfg, indent=2))
PY

systemctl daemon-reload
systemctl enable ignition-enforcer.service
systemctl restart ignition-enforcer.service
echo "installed; config:"
cat /etc/ignition/enforcer.json
