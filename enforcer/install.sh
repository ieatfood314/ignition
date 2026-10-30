#!/bin/sh
set -eu

usage() {
    cat <<USAGE
usage: sudo $0 [options]

Installs the ignition blocker on this computer. During a burn it blocks the sites and programs on your
block list (set in the app: Settings > Blocking). Most people don't need any options: ../install.sh runs
this for you.

  --server-url URL    where this computer reaches the ignition app
                      (default: the app on this computer, http://127.0.0.1:4580)
  --user USER         your login, for desktop notifications (default: whoever ran sudo)
  --host NAME         name shown on the Ignite page (default: hostname)
  --no-notify         don't send a desktop notification when a blocked program is closed
  --block LIST        programs to kill if started during a burn, on this computer only (comma-separated)
  --block-ports LIST  local TCP ports to close to everyone during a burn (e.g. 8000 for a video server)
  --t3-ports LIST     T3 Code ports to cut off during a burn (e.g. 3773); T3 Code itself keeps running
  --hook-burn CMD     shell command to run when a burn starts (\$IGNITION_COLOR is the accent, e.g. e23a29)
  --hook-idle CMD     shell command to run when it ends
  --hook-countdown CMD  shell command to run when a T-minus starts (\$IGNITION_COLOR is amber, ff8000)
  --hook-steps N      re-run the burn and countdown hooks as each 1/N of them passes, with \$IGNITION_STEPS=N
                      and \$IGNITION_LEFT the steps to go (0 turns it off)

Re-running keeps settings you don't pass. It also turns off DNS-over-HTTPS in Firefox, Zen and Chrome
through their policy files, because DoH skips /etc/hosts, which is how sites get blocked.
USAGE
    exit 1
}

url= user=${SUDO_USER:-} host= ports= block= block_ports= hook_burn= hook_idle= hook_countdown= hook_steps= notify=
while [ $# -gt 0 ]; do
    case $1 in
        --server-url) url=$2; shift 2 ;;
        --user|--t3-user) user=$2; shift 2 ;;
        --host) host=$2; shift 2 ;;
        --t3-ports) ports=$2; shift 2 ;;
        --block) block=$2; shift 2 ;;
        --block-ports) block_ports=$2; shift 2 ;;
        --hook-burn) hook_burn=$2; shift 2 ;;
        --hook-idle) hook_idle=$2; shift 2 ;;
        --hook-countdown) hook_countdown=$2; shift 2 ;;
        --hook-steps) hook_steps=$2; shift 2 ;;
        --notify) notify=on; shift ;;
        --no-notify) notify=off; shift ;;
        *) usage ;;
    esac
done
[ "$(id -u)" = 0 ] || { echo "run with sudo" >&2; exit 1; }
[ -x /usr/bin/python3 ] || { echo "needs Python 3 at /usr/bin/python3" >&2; exit 1; }
if [ "$user" = root ]; then user=; fi
if [ -n "$user" ] && ! id -u "$user" >/dev/null 2>&1; then echo "no such user: $user" >&2; exit 1; fi

here=$(cd "$(dirname "$0")" && pwd)
app=
if [ -n "$user" ]; then
    uid=$(id -u "$user")
    app="user.slice/user-$uid.slice/user@$uid.service/app.slice"
fi
mkdir -p /etc/ignition

# Write the config first: it can still refuse (e.g. port blocks without nftables) before anything is installed.
python3 -I - "$url" "$host" "$(hostname -s)" "$ports" "$block" "$block_ports" "$hook_burn" "$hook_idle" \
    "$hook_countdown" "$hook_steps" "$app" "$user" "$notify" <<'PY'
import json, shutil, sys
from pathlib import Path

url, host, hostname, ports, block, block_ports, hook_burn, hook_idle, hook_countdown, hook_steps, app, user, notify = sys.argv[1:]
path = Path("/etc/ignition/enforcer.json")
try:
    cfg = json.loads(path.read_text())
except (OSError, ValueError):
    cfg = {}
new = not cfg
if url:
    cfg["server_url"] = url
cfg.setdefault("server_url", "http://127.0.0.1:4580")
if host:
    cfg["host"] = host
cfg.setdefault("host", hostname)
cfg.setdefault("poll_seconds", 5)
if ports:
    if ports != "none" and not app:
        sys.exit("--t3-ports needs --user (whose T3 Code server should keep running)")
    cfg["t3"] = {
        "ports": [] if ports == "none" else [int(p) for p in ports.split(",") if p],
        "allow_cgroups": [f"{app}/t3code.service", f"{app}/app-t3code.slice"] if app else [],
    }
cfg.setdefault("t3", {"ports": [], "allow_cgroups": []})
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
if hook_countdown:
    hooks["countdown"] = hook_countdown
if hook_steps:
    hooks["steps"] = int(hook_steps)
if hooks:
    cfg["hooks"] = hooks
# Notifications are on for new installs; re-runs keep whatever was chosen before.
if notify == "off":
    cfg.pop("notify_user", None)
elif user and (notify == "on" or new):
    cfg["notify_user"] = user

if (cfg["t3"].get("ports") or cfg.get("block_ports")) and not shutil.which("nft"):
    sys.exit("port blocking needs nftables: install it (apt/dnf/pacman install nftables) and run this again")
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

install -D -m 0755 "$here/ignition-enforcer.py" /usr/local/lib/ignition/ignition-enforcer.py
install -D -m 0755 "$here/openrgb-ram" /usr/local/lib/ignition/openrgb-ram
install -D -m 0644 "$here/ignition-enforcer.service" /etc/systemd/system/ignition-enforcer.service

systemctl daemon-reload
systemctl enable ignition-enforcer.service
systemctl restart ignition-enforcer.service
echo "installed; config:"
cat /etc/ignition/enforcer.json
