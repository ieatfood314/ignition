#!/usr/bin/env python3
"""Ignition enforcer: runs as root on each machine and applies blocks while a session is active.

Every few seconds it heartbeats to the ignition server, which replies with the current session and the
shared block list. While a session is active it:
  * cuts off T3 Code clients: an nftables table rejects loopback TCP to the T3 ports from every
    process outside the T3 server's own cgroups (so running threads keep going, but Tailscale Serve,
    SSH tunnels and local desktop apps can't reach it), and existing client connections are killed;
  * blocks the T3 server's cgroups from reaching Cloudflare's tunnel port (t3.egress_ports, default
    7844): T3 Connect's cloudflared runs inside the server's cgroup, so it would otherwise pass the rule
    above and carry remote clients straight in;
  * cuts off other local services completely (block_ports), e.g. a self-hosted video server;
  * points blocked sites at 0.0.0.0 in /etc/hosts;
  * kills blocked programs (e.g. topgrade, lutris), and anything they spawned, if started after the
    session began. Ones already open at liftoff are frozen until it ends instead (and their windows
    minimized), so a game resumes where it was; a T-minus warns a minute before liftoff;
  * runs the optional burn/idle hooks (e.g. to recolour RGB lighting).
With hooks.steps set, the burn hook re-runs each time another 1/steps of the burn is used up, and a
countdown hook does the same through a T-minus, so the lighting can count down (see openrgb-ram).
The last known session and block list are cached on disk, so blocks hold until the session ends even if
the server is unreachable.
"""

import json
import math
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

NFT_TABLE = "ignition"
CACHE = Path("/var/lib/ignition/state.json")
FROZEN = Path("/var/lib/ignition/frozen.json")
WARN_SECONDS = 60  # heads-up before a T-minus liftoff pauses open programs
CGROUP_ROOT = Path("/sys/fs/cgroup")
HOSTS = Path("/etc/hosts")
HOSTS_BEGIN = "# >>> ignition: blocked while burning >>>"
HOSTS_END = "# <<< ignition <<<"
ACCENT = "e23a29"
COUNTDOWN_COLOR = "ff8000"

# Other domains that belong to a site; subdomains are covered separately.
ALIASES = {
    "youtube.com": ["youtu.be", "youtube-nocookie.com"],
    "reddit.com": ["redd.it", "i.redd.it", "v.redd.it", "preview.redd.it", "external-preview.redd.it"],
    "facebook.com": ["fb.com", "fb.me", "fb.watch", "messenger.com"],
}
# /etc/hosts has no wildcards, so a site also blocks these common subdomains.
SUBDOMAINS = {
    "": ["www", "m"],
    "youtube.com": ["music", "tv", "studio", "accounts"],
    "reddit.com": ["old", "new", "np", "sh", "gql", "oauth", "gateway", "chat"],
    "facebook.com": ["web", "mbasic", "touch", "business", "l", "lm", "edge-chat", "upload"],
    "onshape.com": ["cad", "learn", "forum"],
    "printables.com": ["media", "api"],
    "makerworld.com": ["public-cdn", "api"],
    "monkeytype.com": ["api"],
}


def hosts_for(sites):
    """Host names for /etc/hosts. Port-qualified sites are skipped: hosts has no ports, so blocking the
    bare host would take out everything else on it (block_ports handles those instead)."""
    out = []
    for site in sites:
        if ":" in site:
            continue
        for base in [site, *ALIASES.get(site, [])]:
            for sub in ["", *SUBDOMAINS[""], *SUBDOMAINS.get(base, [])]:
                name = f"{sub}.{base}" if sub else base
                if name not in out:
                    out.append(name)
    return out


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
        self.local_block = [r["name"] for r in cfg.get("block_processes") or []]
        self.block_ports = [int(p) for p in cfg.get("block_ports") or []]
        self.hooks = cfg.get("hooks") or {}
        self.steps = int(self.hooks.get("steps") or 0)
        self.shown = ("idle", 0)  # what the hooks last showed: (hook, steps left)
        self.hook_next = None  # the hook run waiting for the hook thread; only the latest is kept
        self.hook_busy = False
        self.hook_cv = threading.Condition()
        self.show_lock = threading.Lock()
        self.notify_user = cfg.get("notify_user")
        self.session = self.load_cache()
        self.remote = self.session.pop("block", None) or {"sites": [], "programs": []}
        self.armed = False
        self.error = None
        self.killed = 0
        self.last_notify = 0
        self.stop = threading.Event()
        self.freeze_lock = threading.Lock()
        self.frozen = self.load_frozen()  # pid -> start time (clock ticks) of what's paused
        self.warned = None  # the T-minus whose one-minute warning was shown

    # ---------- session state ----------

    def load_cache(self):
        try:
            return json.loads(CACHE.read_text())
        except (OSError, ValueError):
            return {"active": False}

    def save_cache(self):
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        tmp = CACHE.with_suffix(".tmp")
        tmp.write_text(json.dumps({**self.session, "block": self.remote}))
        tmp.replace(CACHE)

    @property
    def block(self):
        return sorted(set(self.local_block) | set(self.remote.get("programs") or []))

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
        new = {k: s.get(k) for k in ("active", "phase", "session_id", "started_at", "ends_at")}
        new["created_at"] = (s.get("session") or {}).get("created_at")
        remote = s.get("block") or self.remote
        if new != self.session or remote != self.remote:
            self.session, self.remote = new, remote
            self.save_cache()
        return None

    # ---------- T3 lockout ----------

    def allowed_cgroups(self):
        return [p for p in self.t3.get("allow_cgroups", []) if (CGROUP_ROOT / p).is_dir()]

    def apply_nft(self):
        rules = []
        if self.block_ports:
            # Nobody gets these, local or remote. Loopback covers Tailscale Serve and local clients; the
            # prerouting rule runs before Docker's DNAT, so published container ports are covered too.
            closed = ", ".join(str(p) for p in self.block_ports)
            rules.append(f'oifname "lo" tcp dport {{ {closed} }} reject with tcp reset')
        egress = self.t3.get("egress_ports", [7844]) if self.t3.get("ports") else []
        if egress:
            # Allowed cgroups include whatever the T3 server spawns, e.g. T3 Connect's cloudflared tunnel.
            # Cut the tunnel off at its edge instead of letting it into the port.
            out = ", ".join(str(p) for p in egress)
            rules += [
                f'socket cgroupv2 level {p.count("/") + 1} "{p}" meta l4proto {{ tcp, udp }} th dport {{ {out} }} reject'
                for p in self.allowed_cgroups()
            ]
        if self.t3.get("ports"):
            ports = ", ".join(str(p) for p in self.t3["ports"])
            rules += [
                f'oifname "lo" tcp dport {{ {ports} }} socket cgroupv2 level {p.count("/") + 1} "{p}" accept'
                for p in self.allowed_cgroups()
            ]
            rules.append(f'oifname "lo" tcp dport {{ {ports} }} reject with tcp reset')
        body = "\n".join(f"    {r}" for r in rules)
        pre = ""
        if self.block_ports:
            closed = ", ".join(str(p) for p in self.block_ports)
            pre = (
                f"  chain prerouting {{\n"
                f"    type filter hook prerouting priority dstnat - 10; policy accept;\n"
                f'    iifname != "lo" fib daddr type local tcp dport {{ {closed} }} drop\n'
                f"  }}\n"
            )
        script = (
            f"table inet {NFT_TABLE} {{}}\n"
            f"delete table inet {NFT_TABLE}\n"
            f"table inet {NFT_TABLE} {{\n"
            f"  chain output {{\n"
            f"    type filter hook output priority filter - 10; policy accept;\n"
            f"{body}\n"
            f"  }}\n"
            f"{pre}"
            f"}}\n"
        )
        r = run(["nft", "-f", "-"], stdin=script)
        if r.returncode != 0:
            raise RuntimeError(f"nft: {r.stderr.strip()}")

    def clear_nft(self):
        if shutil.which("nft"):  # only port blocks need nftables, so it may not be installed
            run(["nft", "delete", "table", "inet", NFT_TABLE])

    def pid_allowed(self, pid):
        try:
            cg = Path(f"/proc/{pid}/cgroup").read_text().strip().split("::", 1)[-1].lstrip("/")
        except OSError:
            return False
        return any(cg == p or cg.startswith(p + "/") for p in self.allowed_cgroups())

    def kill_clients(self):
        ports = self.t3.get("ports") or []
        if not ports:
            return
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

    def kill_port_connections(self):
        if not self.block_ports:
            return
        flt = " or ".join(f"sport = :{p} or dport = :{p}" for p in self.block_ports)
        r = run(["ss", "-K", "-Htn", "state", "established", f"( {flt} )"])
        n = len(r.stdout.splitlines())
        if n:
            self.killed += n
            log(f"killed {n} connection(s) to blocked ports {self.block_ports}")

    # ---------- blocked sites ----------

    def apply_hosts(self, sites):
        """Rewrite our section of /etc/hosts (only if it changed). An empty list removes the section."""
        try:
            text = HOSTS.read_text()
        except OSError:
            return
        kept, inside = [], False
        for line in text.splitlines():
            if line.strip() == HOSTS_BEGIN:
                inside = True
            elif line.strip() == HOSTS_END:
                inside = False
            elif not inside:
                kept.append(line)
        while kept and not kept[-1].strip():
            kept.pop()
        names = hosts_for(sites)
        if names:
            kept += ["", HOSTS_BEGIN, *(f"{ip} {n}" for n in names for ip in ("0.0.0.0", "::")), HOSTS_END]
        new = "\n".join(kept) + "\n"
        if new == text:
            return
        tmp = HOSTS.with_name(".hosts.ignition")
        tmp.write_text(new)
        os.chmod(tmp, 0o644)
        tmp.replace(HOSTS)
        log(f"/etc/hosts: {'blocking ' + str(len(names)) + ' names' if names else 'cleared'}")

    # ---------- hooks ----------

    def display(self):
        """(hook, steps left) the hooks should be showing: the burn or the T-minus, with how many of
        self.steps parts of it are still to go (0 without steps), or ("idle", 0)."""
        s, t = self.session, time.time()
        # A T-minus whose liftoff has passed is a burn, even before the next sync says so.
        lifted = s.get("phase") == "countdown" and (s.get("started_at") or t) <= t < (s.get("ends_at") or 0)
        if self.active() or lifted:
            name, a, b = "burn", s.get("started_at") or t, s["ends_at"]
        elif s.get("phase") == "countdown" and t < (s.get("started_at") or 0):
            name, a, b = "countdown", s.get("created_at") or t, s["started_at"]
        else:
            return "idle", 0
        if not self.hooks.get(name):
            return "idle", 0
        if not self.steps:
            return name, 0
        return name, max(1, min(self.steps, math.ceil((b - t) / max(1, b - a) * self.steps)))

    def show(self, again=False):
        """Run the hook for whatever should be showing, if that changed (or again, e.g. after a resume)."""
        with self.show_lock:
            want = self.display()
            if want != self.shown or (again and want[0] != "idle"):
                self.shown = want
                self.run_hook(*want)

    def run_hook(self, name, left=0):
        with self.hook_cv:
            self.hook_next = (name, left)
            self.hook_cv.notify_all()

    def hook_loop(self):
        # One hook at a time, so two OpenRGB runs never fight over the bus; a run that's overtaken while
        # waiting is skipped, because every run paints the whole state.
        while True:
            with self.hook_cv:
                while not self.hook_next:
                    self.hook_cv.wait()
                (name, left), self.hook_next, self.hook_busy = self.hook_next, None, True
            try:
                self.call_hook(name, left)
            finally:
                with self.hook_cv:
                    self.hook_busy = False
                    self.hook_cv.notify_all()

    def call_hook(self, name, left):
        cmd = self.hooks.get(name)
        if not cmd:
            return
        burn = self.hooks.get("color") or ACCENT
        color = (self.hooks.get("countdown_color") or COUNTDOWN_COLOR) if name == "countdown" else burn
        env = {**os.environ, "IGNITION_COLOR": color, "IGNITION_BURN_COLOR": burn, "IGNITION_PHASE": name}
        if self.steps and name != "idle":
            env.update(IGNITION_STEPS=str(self.steps), IGNITION_LEFT=str(left))
        try:
            r = subprocess.run(["sh", "-c", cmd], env=env, capture_output=True, text=True, timeout=90)
            if r.returncode:
                log(f"{name} hook exited {r.returncode}: {(r.stderr or r.stdout).strip()[:300]}")
        except Exception as e:  # noqa: BLE001
            log(f"{name} hook failed: {e}")

    def wait_hooks(self, timeout):
        with self.hook_cv:
            self.hook_cv.wait_for(lambda: not self.hook_next and not self.hook_busy, timeout)

    # ---------- process blocking ----------

    def boot_time(self):
        for line in Path("/proc/stat").read_text().splitlines():
            if line.startswith("btime "):
                return int(line.split()[1])
        return 0

    @staticmethod
    def blocked_name(argv, names):
        heads = [os.path.basename(a) for a in argv[:2]]
        for name in names:
            if name in heads:
                return name
        return None

    def scan(self):
        """Running blocked programs as [(pid, name, started, uid)], plus every process's children."""
        btime, hz = self.boot_time(), os.sysconf("SC_CLK_TCK")
        names = self.block
        found, children = [], {}
        for d in Path("/proc").iterdir():
            if not d.name.isdigit():
                continue
            try:
                stat = (d / "stat").read_text()
                fields = stat.rsplit(")", 1)[1].split()
                children.setdefault(int(fields[1]), []).append(int(d.name))
                argv = (d / "cmdline").read_bytes().split(b"\0")
                argv = [a.decode(errors="replace") for a in argv if a]
                name = self.blocked_name(argv, names)
                if not name:
                    continue
                start = btime + int(fields[19]) / hz
                uid = (d / "status").read_text().split("\nUid:")[1].split()[0]
            except (OSError, IndexError, ValueError):
                continue
            found.append((int(d.name), name, start, uid))
        return found, children

    @staticmethod
    def tree(pid, children):
        tree, i = [pid], 0
        while i < len(tree):
            tree += children.get(tree[i], [])
            i += 1
        return tree

    def kill_blocked_processes(self):
        """Kill blocked programs started during the burn; pause the ones that were already open."""
        started = self.session.get("started_at") or 0
        found, children = self.scan()
        hits, paused = [], []
        for pid, name, start, uid in found:
            tree = self.tree(pid, children)
            if start < started:
                if self.freeze(tree):
                    paused.append(name)
                    log(f"paused {name} pid={pid} uid={uid} ({len(tree)} process(es))")
                continue
            # Take what it spawned down with it (topgrade -> sudo pacman, ...): those all started later.
            for p in tree:
                try:
                    os.kill(p, signal.SIGKILL)
                except OSError:
                    pass
            hits.append(name)
            log(f"killed {name} pid={pid} uid={uid}" + (f" and {len(tree) - 1} child(ren)" if len(tree) > 1 else ""))
        if paused:
            ends = time.strftime("%H:%M", time.localtime(self.session.get("ends_at", 0)))
            self.notify(f"{paused[0].capitalize()} is paused", f"Ignition is burning until {ends}. It picks up "
                        "right where you left it after.")
        elif hits:
            self.notify(f"{hits[0].capitalize()} is blocked", self.remaining_text())

    # A blocked program that's already open at liftoff is frozen (SIGSTOP) instead of killed, so a game
    # comes back exactly where it was when the burn ends. Sending it SIGCONT yourself doesn't help: it's
    # frozen again within a second.

    @staticmethod
    def proc_start(pid):
        """(state, start time in clock ticks) of a process, or None if it's gone."""
        try:
            fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            return fields[0], int(fields[19])
        except (OSError, IndexError, ValueError):
            return None

    @staticmethod
    def serves_fuse(pid):
        # A frozen FUSE server (a game's squashed filesystem, ...) would hang anything that touches its
        # mount, like a file manager or an indexer, so it's left running; with the game frozen it's idle.
        try:
            return any(os.readlink(f) == "/dev/fuse" for f in Path(f"/proc/{pid}/fd").iterdir())
        except OSError:
            return False

    def load_frozen(self):
        try:
            return {int(k): v for k, v in json.loads(FROZEN.read_text()).items()}
        except (OSError, ValueError):
            return {}

    def save_frozen(self):
        FROZEN.parent.mkdir(parents=True, exist_ok=True)
        tmp = FROZEN.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.frozen))
        tmp.replace(FROZEN)

    def freeze(self, tree):
        """Freeze a process tree. True if any of it was newly frozen (not just re-frozen)."""
        with self.freeze_lock:
            if self.stop.is_set():
                return False
            todo = []
            for p in tree:
                st = self.proc_start(p)
                if not st or st[0] in "Tt" or (p not in self.frozen and self.serves_fuse(p)):
                    continue
                todo.append((p, st[1]))
            if not todo:
                return False
            fresh = [p for p, _ in todo if p not in self.frozen]
            if fresh:
                self.minimize(fresh)
            for p, ticks in todo:
                try:
                    os.kill(p, signal.SIGSTOP)
                    self.frozen[p] = ticks
                except OSError:
                    pass
            self.save_frozen()
            if not fresh:
                log(f"froze {len(todo)} process(es) again")
            return bool(fresh)

    def thaw(self):
        with self.freeze_lock:
            if not self.frozen:
                return
            for p, ticks in reversed(list(self.frozen.items())):
                st = self.proc_start(p)
                if st and st[1] == ticks:  # not a new process that reused the pid
                    try:
                        os.kill(p, signal.SIGCONT)
                    except OSError:
                        pass
            log(f"resumed {len(self.frozen)} paused process(es)")
            self.frozen = {}
            self.save_frozen()
        self.notify("Burn complete", "Everything ignition paused is back where you left it.")

    def minimize(self, pids):
        """Minimize these processes' windows (KDE Plasma), so a frozen full-screen game isn't in the way."""
        script = (
            f"const pids = {json.dumps(pids)};\n"
            "(workspace.windowList ? workspace.windowList() : workspace.clientList()).forEach(w => {\n"
            "    if (pids.includes(w.pid)) w.minimized = true;\n"
            "});\n"
        )
        with tempfile.NamedTemporaryFile("w", suffix=".js", prefix="ignition-") as f:
            f.write(script)
            f.flush()
            os.chmod(f.name, 0o644)
            call = ["dbus-send", "--session", "--print-reply", "--dest=org.kde.KWin"]
            unload = [*call, "/Scripting", "org.kde.kwin.Scripting.unloadScript", "string:ignition-minimize"]
            self.as_user(*unload)
            out = self.as_user(*call, "/Scripting", "org.kde.kwin.Scripting.loadScript",
                               f"string:{f.name}", "string:ignition-minimize")
            sid = out.split()[-1] if out and "int32" in out else None
            if sid and sid != "-1":
                self.as_user(*call, f"/Scripting/Script{sid}", "org.kde.kwin.Script.run")
            self.as_user(*unload)

    def warn_liftoff(self):
        """A minute before a T-minus lifts off, say what it'll pause, so there's time to get to a checkpoint."""
        s = self.session
        left = (s.get("started_at") or 0) - time.time()
        if not self.block or not s.get("session_id") or self.warned == s["session_id"] or not 0 < left <= WARN_SECONDS:
            return
        self.warned = s["session_id"]
        names = sorted({name for _, name, _, _ in self.scan()[0]})
        if names:
            what = ", ".join(n.capitalize() for n in names)
            self.notify(f"Liftoff in {round(left)} seconds",
                        f"{what} will be paused for the burn, and picks up where you left off after.")

    def remaining_text(self):
        mins = max(0, int((self.session.get("ends_at", 0) - time.time()) // 60) + 1)
        return f"Ignition is burning: {mins} min left. Back to work."

    def as_user(self, *cmd):
        """Run a command on the desktop user's session bus; its output, or None."""
        if not self.notify_user:
            return None
        try:
            uid = int(run(["id", "-u", self.notify_user]).stdout.strip())
            return subprocess.run(
                ["runuser", "-u", self.notify_user, "--", "env",
                 f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus", *cmd],
                capture_output=True, text=True, timeout=10,
            ).stdout
        except (ValueError, OSError, subprocess.TimeoutExpired):
            return None

    def notify(self, title, body):
        if not self.notify_user or time.time() - self.last_notify < 10:
            return
        self.last_notify = time.time()
        threading.Thread(
            target=self.as_user, args=("notify-send", "-a", "ignition", "-u", "critical", title, body), daemon=True,
        ).start()

    # ---------- loops ----------

    def enforce(self, resumed=False):
        self.show(again=resumed)
        if not self.active():
            if self.armed:
                log("session over: lifting blocks")
            self.clear_nft()
            self.apply_hosts([])
            self.armed = False
            return
        if not self.armed:
            log(f"session active until {time.ctime(self.session['ends_at'])}: arming")
        self.apply_hosts(self.remote.get("sites") or [])
        if self.t3.get("ports") or self.block_ports:
            self.apply_nft()
            self.kill_clients()
            self.kill_port_connections()
        self.armed = True

    def main_loop(self):
        last = time.time() - time.monotonic()
        while not self.stop.is_set():
            # The monotonic clock stops while suspended and the wall clock doesn't, so a jump between them
            # means the machine slept. RGB set in Direct mode is lost over a sleep, so the hooks repaint.
            offset = time.time() - time.monotonic()
            resumed, last = offset - last > 15, offset
            err = self.sync()
            try:
                self.enforce(resumed)
                self.error = err
            except Exception as e:  # noqa: BLE001
                self.error = str(e)
                log(f"enforce failed: {e}")
            self.stop.wait(self.poll)

    def process_loop(self):
        while not self.stop.is_set():
            try:
                self.show()  # the next step of the RAM countdown, on the second rather than at the next sync
                if not self.active():
                    self.thaw()
                    self.warn_liftoff()
                elif self.block:
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
        threading.Thread(target=self.process_loop, daemon=True).start()
        threading.Thread(target=self.hook_loop, daemon=True).start()
        self.main_loop()
        self.thaw()
        self.clear_nft()
        self.apply_hosts([])
        if self.shown[0] != "idle":
            self.run_hook("idle")
            self.wait_hooks(60)
        log("stopped; blocks lifted")


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: ignition-enforcer.py CONFIG.json")
    Enforcer(json.loads(Path(sys.argv[1]).read_text())).run()


if __name__ == "__main__":
    main()
