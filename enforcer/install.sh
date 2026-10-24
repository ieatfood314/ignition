#!/bin/sh
set -eu

usage() {
    cat <<USAGE
usage: sudo $0 --server-url URL --t3-user USER [options]

  --server-url    where the ignition server is reachable from this machine
  --t3-user       user whose T3 Code server should keep running (its cgroups stay allowed)
  --host          name shown on the timer page (default: hostname)
  --t3-ports      loopback ports to cut off during a burn (default: 3773, "none" to skip)
  --block         programs to kill if started during a burn, on this machine only
                  (the shared list is set in the app: Settings > Blocking)
  --block-ports   local TCP ports to close to everyone during a burn (e.g. 8000 for a video server)
  --hook-burn     shell command to run when a burn starts (\$IGNITION_COLOR is the accent, e.g. e23a29)
  --hook-idle     shell command to run when it ends
  --notify        send a desktop notification to --t3-user when something is blocked

Re-running keeps settings you don't pass. It also turns off DNS-over-HTTPS in Firefox, Zen and Chrome
through their policy files, because DoH skips /etc/hosts, which is how sites get blocked.
USAGE
    exit 1
}

url= user= host=$(hostname -s) ports= block= block_ports= hook_burn= hook_idle= notify=
while [ $# -gt 0 ]; do
    case $1 in
        --server-url) url=$2; shift 2 ;;
        --t3-user) user=$2; shift 2 ;;
        --host) host=$2; shift 2 ;;
        --t3-ports) ports=$2; shift 2 ;;
        --block) block=$2; shift 2 ;;
        --block-ports) block_ports=$2; shift 2 ;;
        --hook-burn) hook_burn=$2; shift 2 ;;
        --hook-idle) hook_idle=$2; shift 2 ;;
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
install -D -m 0755 "$here/openrgb-ram" /usr/local/lib/ignition/openrgb-ram
install -D -m 0644 "$here/ignition-enforcer.service" /etc/systemd/system/ignition-enforcer.service
mkdir -p /etc/ignition

python3 -I - "$url" "$host" "$ports" "$block" "$block_ports" "$hook_burn" "$hook_idle" "$app" "${notify:+$user}" <<'PY'
import json, sys
from pathlib import Path

url, host, ports, block, block_ports, hook_burn, hook_idle, app, notify = sys.argv[1:]
path = Path("/etc/ignition/enforcer.json")
try:
    cfg = json.loads(path.read_text())
except (OSError, ValueError):
    cfg = {}
cfg.update(host=host, server_url=url)
cfg.setdefault("poll_seconds", 5)
if ports or "t3" not in cfg:
    cfg["t3"] = {
        "ports": [] if ports == "none" else [int(p) for p in (ports or "3773").split(",") if p],
        "allow_cgroups": [f"{app}/t3code.service", f"{app}/app-t3code.slice"],
    }
if block:
    cfg["block_processes"] = [{"name": n} for n in block.split(",") if n]
cfg.setdefault("block_processes", [])
if block_ports:
    cfg["block_ports"] = [] if block_ports == "none" else [int(p) for p in block_ports.split(",") if p]
hooks = cfg.get("hooks") or {}
if hook_burn:
    hooks["burn"] = hook_burn
if hook_idle:
    hooks["idle"] = hook_idle
if hooks:
    cfg["hooks"] = hooks
if notify:
    cfg["notify_user"] = notify
path.write_text(json.dumps(cfg, indent=2) + "\n")

# Browsers that do DNS-over-HTTPS never look at /etc/hosts, so turn it off for good.
def merge(policy_file, key, value):
    p = Path(policy_file)
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        data = {}
    if key == "DNSOverHTTPS":
        data.setdefault("policies", {})[key] = value
    else:
        data[key] = value
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2) + "\n")
    print(f"DoH off: {p}")

gecko = [Path("/etc/firefox/policies/policies.json")]
for ini in [*Path("/usr/lib").glob("*/application.ini"), *Path("/opt").glob("*/application.ini")]:
    if "Gecko" in ini.read_text(errors="replace"):
        gecko.append(ini.parent / "distribution" / "policies.json")
for f in gecko:
    merge(f, "DNSOverHTTPS", {"Enabled": False, "Locked": True})
for d, binary in (("/etc/opt/chrome", "/opt/google/chrome"), ("/etc/chromium", "/usr/lib/chromium"),
                  ("/etc/brave", "/opt/brave.com")):
    if Path(binary).exists():
        merge(f"{d}/policies/managed/ignition.json", "DnsOverHttpsMode", "off")
PY

systemctl daemon-reload
systemctl enable ignition-enforcer.service
systemctl restart ignition-enforcer.service
echo "installed; config:"
cat /etc/ignition/enforcer.json
