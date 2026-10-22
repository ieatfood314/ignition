#!/usr/bin/env python3
"""Ignition enforcer: runs as root on each machine and applies blocks while a session is active.

Every few seconds it heartbeats to the ignition server, which replies with the current session.
While a session is active it:
  * cuts off T3 Code clients: an nftables table rejects loopback TCP to the T3 ports from every
    process outside the T3 server's own cgroups (so running threads keep going, but Tailscale Serve,
    SSH tunnels and local desktop apps can't reach it), and existing client connections are killed;
  * kills blocked programs (e.g. lutris) that are started after the session began.
The last known session is cached on disk, so blocks hold until it ends even if the server is unreachable.
"""

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

NFT_TABLE = "ignition"
CACHE = Path("/var/lib/ignition/state.json")
CGROUP_ROOT = Path("/sys/fs/cgroup")


def log(*a):
    print(*a, flush=True)


def run(cmd, stdin=None):
    return subprocess.run(cmd, input=stdin, capture_output=True, text=True)


class Enforcer:
    def __init__(self, cfg):
        self.cfg = cfg
        self.host = cfg.get("host") or socket.gethostname()
        self.url = cfg["server_url"].rstrip("/")
        self.poll = cfg.get("poll_seconds", 5)
        self.t3 = cfg.get("t3") or {}
        self.block = cfg.get("block_processes") or []
        self.notify_user = cfg.get("notify_user")
        self.session = self.load_cache()
        self.armed = False
        self.error = None
        self.killed = 0
        self.last_notify = 0
        self.stop = threading.Event()

    # ---------- session state ----------

    def load_cache(self):
        try:
            return json.loads(CACHE.read_text())
        except (OSError, ValueError):
            return {"active": False}

    def save_cache(self, s):
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        tmp = CACHE.with_suffix(".tmp")
        tmp.write_text(json.dumps(s))
        tmp.replace(CACHE)

    def active(self):
        s = self.session
        return bool(s.get("active")) and time.time() < s.get("ends_at", 0)

    def sync(self):
        body = json.dumps({
            "host": self.host,
            "status": {"armed": self.armed, "error": self.error, "killed": self.killed},
        }).encode()
        req = urllib.request.Request(
            f"{self.url}/api/enforcer/heartbeat", data=body,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=8) as r:
                s = json.load(r)
        except Exception as e:  # noqa: BLE001 — offline: keep enforcing the cached session
            return f"server unreachable: {e}"
        new = {k: s.get(k) for k in ("active", "session_id", "started_at", "ends_at")}
        if new != self.session:
            self.session = new
            self.save_cache(new)
        return None

    # ---------- T3 lockout ----------

    def allowed_cgroups(self):
        return [p for p in self.t3.get("allow_cgroups", []) if (CGROUP_ROOT / p).is_dir()]

    def apply_nft(self):
        ports = ", ".join(str(p) for p in self.t3["ports"])
        rules = [
            f'oifname "lo" tcp dport {{ {ports} }} socket cgroupv2 level {p.count("/") + 1} "{p}" accept'
            for p in self.allowed_cgroups()
        ]
        rules.append(f'oifname "lo" tcp dport {{ {ports} }} reject with tcp reset')
        body = "\n".join(f"    {r}" for r in rules)
        script = (
            f"table inet {NFT_TABLE} {{}}\n"
            f"delete table inet {NFT_TABLE}\n"
            f"table inet {NFT_TABLE} {{\n"
            f"  chain output {{\n"
            f"    type filter hook output priority filter - 10; policy accept;\n"
            f"{body}\n"
            f"  }}\n"
            f"}}\n"
        )
        r = run(["nft", "-f", "-"], stdin=script)
        if r.returncode != 0:
            raise RuntimeError(f"nft: {r.stderr.strip()}")

    def clear_nft(self):
        run(["nft", "delete", "table", "inet", NFT_TABLE])

    def pid_allowed(self, pid):
        try:
            cg = Path(f"/proc/{pid}/cgroup").read_text().strip().split("::", 1)[-1].lstrip("/")
        except OSError:
            return False
        return any(cg == p or cg.startswith(p + "/") for p in self.allowed_cgroups())

    def kill_clients(self):
        ports = self.t3["ports"]
        flt = " or ".join(f"dport = :{p}" for p in ports)
        r = run(["ss", "-Htnp", "state", "established", f"( {flt} )"])
        for line in r.stdout.splitlines():
            cols = line.split()
            if len(cols) < 4:
                continue
            local, peer = cols[2], cols[3]
            pids = [int(x.split("=")[1]) for x in line.replace(",", " ").split() if x.startswith("pid=")]
            if pids and all(self.pid_allowed(p) for p in pids):
                continue
            lport, pport = local.rsplit(":", 1)[1], peer.rsplit(":", 1)[1]
            k = run(["ss", "-K", "-Htn", f"( sport = :{lport} and dport = :{pport} )"])
            if k.returncode == 0:
                self.killed += 1
                log(f"killed T3 client connection {local} -> {peer} pids={pids}")

    # ---------- process blocking ----------

    def boot_time(self):
        for line in Path("/proc/stat").read_text().splitlines():
            if line.startswith("btime "):
                return int(line.split()[1])
        return 0

    def blocked_name(self, argv):
        heads = [os.path.basename(a) for a in argv[:2]]
        for rule in self.block:
            if rule["name"] in heads:
                return rule["name"]
        return None

    def kill_blocked_processes(self):
        started = self.session.get("started_at") or 0
        btime, hz = self.boot_time(), os.sysconf("SC_CLK_TCK")
        hits = []
        for d in Path("/proc").iterdir():
            if not d.name.isdigit():
                continue
            try:
                argv = (d / "cmdline").read_bytes().split(b"\0")
                argv = [a.decode(errors="replace") for a in argv if a]
                name = self.blocked_name(argv)
                if not name:
                    continue
                stat = (d / "stat").read_text()
                start = btime + int(stat.rsplit(")", 1)[1].split()[19]) / hz
                uid = (d / "status").read_text().split("\nUid:")[1].split()[0]
            except (OSError, IndexError, ValueError):
                continue
            if start < started:
                continue
            try:
                os.kill(int(d.name), signal.SIGKILL)
                hits.append(name)
                log(f"killed {name} pid={d.name} uid={uid}")
            except OSError:
                pass
        if hits:
            self.notify(f"{hits[0].capitalize()} is blocked", self.remaining_text())

    def remaining_text(self):
        mins = max(0, int((self.session.get("ends_at", 0) - time.time()) // 60) + 1)
        return f"Ignition is burning: {mins} min left. Back to work."

    def notify(self, title, body):
        if not self.notify_user or time.time() - self.last_notify < 10:
            return
        self.last_notify = time.time()
        try:
            uid = int(run(["id", "-u", self.notify_user]).stdout.strip())
        except ValueError:
            return
        subprocess.Popen(
            ["runuser", "-u", self.notify_user, "--", "env",
             f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus",
             "notify-send", "-a", "ignition", "-u", "critical", title, body],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    # ---------- loops ----------

    def enforce(self):
        if not self.active():
            if self.armed:
                log("session over: lifting blocks")
            self.clear_nft()
            self.armed = False
            return
        if not self.armed:
            log(f"session active until {time.ctime(self.session['ends_at'])}: arming")
        if self.t3.get("ports"):
            self.apply_nft()
            self.kill_clients()
        self.armed = True

    def main_loop(self):
        while not self.stop.is_set():
            err = self.sync()
            try:
                self.enforce()
                self.error = err
            except Exception as e:  # noqa: BLE001
                self.error = str(e)
                log(f"enforce failed: {e}")
            self.stop.wait(self.poll)

    def process_loop(self):
        while not self.stop.is_set():
            if self.block and self.active():
                try:
                    self.kill_blocked_processes()
                except Exception as e:  # noqa: BLE001
                    log(f"process scan failed: {e}")
            self.stop.wait(1)

    def run(self):
        def shutdown(*_):
            self.stop.set()
        signal.signal(signal.SIGTERM, shutdown)
        signal.signal(signal.SIGINT, shutdown)
        log(f"ignition enforcer for {self.host} -> {self.url}")
        t = threading.Thread(target=self.process_loop, daemon=True)
        t.start()
        self.main_loop()
        self.clear_nft()
        log("stopped; blocks lifted")


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: ignition-enforcer.py CONFIG.json")
    Enforcer(json.loads(Path(sys.argv[1]).read_text())).run()


if __name__ == "__main__":
    main()
