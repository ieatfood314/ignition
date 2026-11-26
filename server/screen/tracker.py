"""The tracker for KDE Plasma: records what is in front of you, and sends it to the hub.

A small KWin script tells us which window has focus. The lock screen, the
compositor's idle notice and gaps in our own heartbeat (suspend) tell us when
nobody is there. What comes out is spans, written to a local database first
and pushed to the hub when it can be reached, so nothing is lost offline.

Only the window's class is kept, plus, for browsers, the site named at the
end of the window title. Titles themselves are never stored.
"""

import glob
import json
import os
import re
import secrets
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

from . import core
from .wayland_idle import IdleWatch

BUS_NAME = "ca.heeney.ignition.Tracker"
OBJECT_PATH = "/ca/heeney/ignition/Tracker"
KWIN_SCRIPT_NAME = "ignition-tracker"
OLD_BUS_NAME = "org.lightcurve.Tracker"  # the tracker from before lightcurve was part of ignition

BEAT = 5  # seconds between heartbeats; an open span's end moves forward on each
GAP = 30  # a heartbeat this late means the machine was asleep, so the span ended at the last one
MIN_SPAN = 1000  # ms; focus that passes through a window faster than this isn't recorded
IDLE_AFTER = 180  # seconds without input before the compositor tells us
STILL_THERE = 300  # back within this long of your last input and you never left (you were reading)
WATCHING_LIMIT = 7200  # a playing video keeps time counting without input, but not for longer than this
INPUT_WATCH = 1  # a raw watch this short notices input after an agent disconnects
AGENT_INPUT_GRACE = 2  # input right as an agent disconnects still belongs to the agent
PUSH_EVERY = 60

KWIN_SCRIPT = """\
var lightcurveCurrent = null;

function lightcurveSend(win) {
    callDBus("%(bus)s", "%(path)s", "%(bus)s", "ReportActive",
             win ? String(win.resourceClass) : "",
             win ? String(win.caption) : "");
}

function lightcurveCaptionChanged() {
    lightcurveSend(lightcurveCurrent);
}

function lightcurveActivated(win) {
    if (lightcurveCurrent) {
        lightcurveCurrent.captionChanged.disconnect(lightcurveCaptionChanged);
    }
    lightcurveCurrent = win;
    if (win) {
        win.captionChanged.connect(lightcurveCaptionChanged);
    }
    lightcurveSend(win);
}

workspace.windowActivated.connect(lightcurveActivated);
lightcurveActivated(workspace.activeWindow);
""" % {"bus": BUS_NAME, "path": OBJECT_PATH}


def log(msg):
    print(msg, flush=True)


# ---- configuration ----

def config_path():
    return os.path.join(core.config_dir(), "tracker.json")


def load_config():
    try:
        with open(config_path()) as f:
            cfg = json.load(f)
        return cfg if isinstance(cfg, dict) else {}
    except (OSError, ValueError):
        return {}


def save_config(cfg):
    os.makedirs(core.config_dir(), exist_ok=True)
    tmp = config_path() + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")
    os.replace(tmp, config_path())


def local_db_path():
    return os.path.join(core.data_dir(), "tracker.db")


def adopt_lightcurve():
    """The first time on a machine that ran lightcurve's tracker, carry its database over (this machine's
    id, and anything not sent yet) and its server and name, so nothing is counted twice or lost."""
    new_db, old_db = local_db_path(), os.path.join(core.data_dir("lightcurve"), "tracker.db")
    if not os.path.exists(new_db) and os.path.exists(old_db):
        os.makedirs(os.path.dirname(new_db), exist_ok=True)
        src = sqlite3.connect(old_db, timeout=15)
        dst = sqlite3.connect(new_db + ".tmp")
        with dst:
            src.backup(dst)
        src.close()
        dst.close()
        os.replace(new_db + ".tmp", new_db)
        log(f"carried lightcurve's tracker over from {old_db}")
    if not os.path.exists(config_path()):
        try:
            with open(os.path.join(core.config_dir("lightcurve"), "config.json")) as f:
                old = json.load(f)
        except (OSError, ValueError):
            old = {}
        keep = {k: old[k] for k in ("hub", "name") if isinstance(old, dict) and old.get(k)}
        if keep:
            save_config(keep)


def device_kind():
    return "laptop" if glob.glob("/sys/class/power_supply/BAT*") else "desktop"


def device_identity(db):
    """This machine's id (made once, kept in the local database) and its name."""
    dev_id = core.kv_get(db, "device_id")
    host = re.sub(r"[^A-Za-z0-9-]", "-", socket.gethostname().split(".")[0])[:40] or "computer"
    if not dev_id:
        dev_id = f"{host}-{secrets.token_hex(4)}"
        with db:
            core.kv_set(db, "device_id", dev_id)
    return dev_id, host


# ---- turning events into spans ----

class Recorder:
    """Keeps the span that is open right now and writes it down as it grows.

    Knows nothing about D-Bus or Wayland: it is told what has focus, whether
    the screen is locked and whether anyone is there, and the time. That keeps
    the rules testable.
    """

    def __init__(self, db, device, lock=None):
        self.db, self.device = db, device
        self.lock = lock or threading.RLock()
        self.focus = None  # (app, site) of the window with focus
        self.locked = False
        self.idle = False
        self.agent = False
        self.open = None  # {"start", "end", "app", "site", "saved"}, times in ms
        self.parked = None  # the span that was open when idleness began, and when that was

    @property
    def active(self):
        return self.open is not None

    def _want(self):
        if self.locked or self.idle or self.agent or not self.focus or self.focus[0] in core.IGNORED_APPS:
            return None
        return self.focus

    def _save(self, span):
        if span["end"] - span["start"] < MIN_SPAN and not span["saved"]:
            return
        end = max(span["end"], span["start"] + MIN_SPAN)
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO spans (device, start, end, app, site) VALUES (?,?,?,?,?) "
                "ON CONFLICT(device, start) DO UPDATE SET end = excluded.end",
                (self.device, span["start"], end, span["app"], span["site"]))
            self.db.execute("INSERT OR IGNORE INTO outbox (start) VALUES (?)", (span["start"],))
        span["saved"] = True

    def _settle(self, now, end=None):
        """Make the open span match what should be recorded, closing and opening as needed."""
        want = self._want()
        if self.open and (self.open["app"], self.open["site"]) != want:
            self.open["end"] = max(self.open["start"], int((now if end is None else end) * 1000))
            self._save(self.open)
            self.open = None
        if want and not self.open:
            ms = int(now * 1000)
            self.open = {"start": ms, "end": ms, "app": want[0], "site": want[1], "saved": False}

    def set_focus(self, app, site, now):
        self.beat(now)
        self.focus = (app, site) if app else None
        self._settle(now)

    def set_locked(self, locked, now):
        self.beat(now)
        self.locked = bool(locked)
        if locked:
            self.parked = None
        self._settle(now)

    def set_agent(self, active, now):
        """Remote and agent-driven desktop use isn't this person's screen time."""
        self.beat(now)
        self.agent = bool(active)
        if active:
            self.parked = None
        self._settle(now)

    def set_idle(self, idle, now, since=None, stitch=True):
        """`since` back-dates the start of idleness to the last input; long idle is never stitched back."""
        if idle == self.idle:
            return
        self.beat(now)
        if idle:
            since = now if since is None else min(since, now)
            span = self.open
            self.idle = True
            self._settle(now, end=since)
            self.parked = (span, since) if span and stitch else None
            return
        self.idle = False
        parked, self.parked = self.parked, None
        if parked and now - parked[1] < STILL_THERE and self._want() == (parked[0]["app"], parked[0]["site"]):
            # Away from the keys for a few minutes and back in the same place: one unbroken stretch.
            self.open = parked[0]
            self.open["end"] = int(now * 1000)
            self._save(self.open)
            return
        self._settle(now)

    def beat(self, now):
        """Move the open span's end up to now, unless the clock shows we were asleep."""
        if not self.open:
            return
        ms = int(now * 1000)
        if ms - self.open["end"] > GAP * 1000 or ms < self.open["end"] - 5000:
            # Suspended (or the clock jumped): the span ended at its last heartbeat.
            self._save(self.open)
            self.open = None
            self.parked = None
            self._settle(now)
            return
        self.open["end"] = max(self.open["end"], ms)
        self._save(self.open)

    def stop(self, now):
        self.beat(now)
        if self.open:
            self._save(self.open)
            self.open = None


class IdleGate:
    """Combine the compositor's ordinary and input-only idle notices.

    An ordinary notice can say "resumed" when a screen inhibitor appears or disappears, without any input.
    Once the input-only notice says nobody is there, only input returning may start the clock again.
    """

    def __init__(self, recorder, policy_inhibited):
        self.recorder = recorder
        self.policy_inhibited = policy_inhibited
        self.normal_quiet = False
        self.raw_quiet = False

    def normal(self, quiet, now):
        self.normal_quiet = bool(quiet)
        self.apply(now, since=now - IDLE_AFTER if quiet else None)

    def raw(self, quiet, now):
        self.raw_quiet = bool(quiet)
        self.apply(now)

    def apply(self, now, since=None):
        if self.raw_quiet:
            self.recorder.set_idle(True, now, stitch=False)
        elif self.normal_quiet:
            if not self.policy_inhibited():
                self.recorder.set_idle(True, now, since=since)
        else:
            self.recorder.set_idle(False, now)


class AgentGate:
    """Keep agent-driven use out, including short sessions and the idle gaps between them.

    Once an agent has used the computer, fresh input after it has left is what gives the computer back to
    the person. Without this, an agent's synthetic input looks like local activity for the next idle timeout.
    """

    def __init__(self, recorder):
        self.recorder = recorder
        self.local = False
        self.remote = False
        self.latched = False
        self.last_active = 0.0

    def _apply(self, now):
        self.recorder.set_agent(self.local or self.remote or self.latched, now)

    def note_remote(self, now):
        # SessionNew is delivered even for a session that has ended before the next process poll.
        self.latched = True
        self.last_active = now
        self._apply(now)

    def update(self, local, remote, now):
        was_active = self.local or self.remote
        self.local, self.remote = bool(local), bool(remote)
        active = self.local or self.remote
        if active or was_active:
            self.last_active = now
        if active:
            self.latched = True
        self._apply(now)

    def input(self, now):
        current = self.local or self.remote
        if self.latched and not current and now - self.last_active > AGENT_INPUT_GRACE:
            self.latched = False
            self._apply(now)


# ---- names for window classes ----

_DESKTOP_HINTS = (
    ("Game", "play"), ("Development", "make"), ("IDE", "make"), ("TextEditor", "make"), ("TerminalEmulator", "make"),
    ("InstantMessaging", "talk"), ("Chat", "talk"), ("Email", "talk"), ("Telephony", "talk"),
    ("Player", "watch"), ("Video", "watch"), ("TV", "watch"),
    ("Office", "make"), ("Graphics", "make"), ("3DGraphics", "make"), ("Engineering", "make"),
)


class DesktopNames:
    """Looks a window class up in the installed .desktop files, for its proper name."""

    def __init__(self):
        self.index = None

    def _build(self):
        self.index = {}
        home = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
        dirs = [home] + (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":")
        dirs += [os.path.expanduser("~/.local/share/flatpak/exports/share"), "/var/lib/flatpak/exports/share"]
        for d in dirs:
            for path in glob.glob(os.path.join(d, "applications", "*.desktop")):
                try:
                    with open(path, errors="replace") as f:
                        text = f.read(20000)
                except OSError:
                    continue
                entry = text.split("[Desktop Entry]", 1)[-1].split("\n[", 1)[0]
                fields = dict(re.findall(r"^(Name|StartupWMClass|Categories|NoDisplay)=(.*)$", entry, re.M))
                name = fields.get("Name", "").strip()
                if not name:
                    continue
                cats = fields.get("Categories", "").split(";")
                hint = next((h for c, h in _DESKTOP_HINTS if c in cats), "")
                stem = os.path.basename(path)[:-len(".desktop")]
                for key in (stem, fields.get("StartupWMClass", "").strip()):
                    if key:
                        self.index.setdefault(key.lower(), (name[:80], hint))

    def lookup(self, app):
        if self.index is None:
            self._build()
        return self.index.get(app.lower())


# ---- bringing over waytime's history ----

def import_waytime(db, device, path=None):
    """Copy waytime's daily totals in as 'legacy' days. Runs once; returns the number of rows."""
    if core.kv_get(db, "waytime_imported"):
        return 0
    path = path or os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"),
                                "waytime", "waytime.db")
    rows = []
    if os.path.exists(path):
        try:
            old = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            rows = [(d, a, s) for d, a, s in old.execute("SELECT date, app, seconds FROM usage")
                    if a not in core.IGNORED_APPS]
            try:
                # Page titles that waytime took for site names stay behind: that time is still in the browser's total.
                rows += [(d, "site:" + s, sec) for d, s, sec in old.execute("SELECT date, site, seconds FROM sites")
                         if core.plausible_site(s)]
            except sqlite3.Error:
                pass
            old.close()
        except sqlite3.Error as e:
            log(f"couldn't read waytime's history: {e}")
            return 0
    with db:
        db.executemany("INSERT OR REPLACE INTO legacy (device, date, app, seconds) VALUES (?,?,?,?)",
                       [(device, d, a, s) for d, a, s in rows if s > 0])
        core.kv_set(db, "waytime_imported", "1")
        if rows:
            core.kv_set(db, "legacy_unsent", "1")
    return len(rows)


# ---- sending to the hub ----

class Uploader:
    def __init__(self, db, lock, device, name, hub):
        self.db, self.lock, self.device, self.name = db, lock, device, name
        self.hub = (hub or "").rstrip("/")
        self.names = DesktopNames()
        self.error = None
        self.last_ok = 0

    def _batch(self):
        with self.lock:
            spans = self.db.execute(
                "SELECT s.start, s.end, s.app, s.site FROM outbox o JOIN spans s ON s.start = o.start "
                "WHERE s.device = ? ORDER BY s.start LIMIT 5000", (self.device,)).fetchall()
            legacy = []
            if core.kv_get(self.db, "legacy_unsent"):
                legacy = self.db.execute("SELECT date, app, seconds FROM legacy WHERE device = ?",
                                         (self.device,)).fetchall()
        return spans, legacy

    def push(self, active, timeout=10):
        """Send whatever the hub hasn't had yet. True if it got there."""
        if not self.hub:
            return False
        spans, legacy = self._batch()
        names = {}
        for app in {s[2] for s in spans}:
            found = self.names.lookup(app)
            if found:
                names[app] = {"label": found[0], "hint": found[1]}
        body = {
            "device": {"id": self.device, "name": self.name, "kind": device_kind(), "platform": "Plasma",
                       "version": core.VERSION},
            "spans": [list(s) for s in spans], "legacy": [list(r) for r in legacy], "names": names,
            "complete": len(spans) < 5000, "active": bool(active),
        }
        req = urllib.request.Request(self.hub + "/api/ingest", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                json.loads(r.read().decode())
        except (OSError, ValueError, urllib.error.URLError) as e:
            reason = str(getattr(e, "reason", e))[:160]
            if reason != self.error:
                log(f"hub unreachable ({reason}); keeping spans here until it is")
            self.error = reason
            return False
        if self.error:
            log("hub reachable again")
        self.error, self.last_ok = None, time.time()
        with self.lock, self.db:
            # A span that grew while this was in flight stays in the outbox for the next push.
            self.db.executemany(
                "DELETE FROM outbox WHERE start = ? AND "
                "(SELECT end FROM spans WHERE device = ? AND start = ?) = ?",
                [(s[0], self.device, s[0], s[1]) for s in spans])
            if legacy:
                self.db.execute("DELETE FROM kv WHERE key = 'legacy_unsent'")
            core.kv_set(self.db, "last_push", int(self.last_ok))
        return True


# ---- agent-driven use ----

AGENT_COMMANDS = {"agy", "claude", "codex", "gemini", "kimi", "opencode"}


def t3_agent_running(proc_root="/proc"):
    """An agent is doing a turn on this machine's T3 server (the server itself may always be running)."""
    try:
        pids = [p for p in os.listdir(proc_root) if p.isdigit()]
    except OSError:
        return False
    for pid in pids:
        try:
            with open(os.path.join(proc_root, pid, "comm")) as f:
                if f.read().strip() not in AGENT_COMMANDS:
                    continue
            with open(os.path.join(proc_root, pid, "cgroup")) as f:
                if "/t3code.service" in f.read():
                    return True
        except OSError:
            continue  # it exited, or belongs to another user
    return False


def remote_agent_session(bus):
    """Tailscale SSH sessions are remote agents, not somebody sitting at this computer."""
    manager = bus.get_object("org.freedesktop.login1", "/org/freedesktop/login1")
    sessions = manager.ListSessions(dbus_interface="org.freedesktop.login1.Manager")
    for _, _, _, _, path in sessions:
        session = bus.get_object("org.freedesktop.login1", path)
        if session.Get("org.freedesktop.login1.Session", "Remote",
                       dbus_interface="org.freedesktop.DBus.Properties"):
            return True
    return False


# ---- the daemon ----

def run(hub=None):
    try:
        import dbus
        import dbus.mainloop.glib
        import dbus.service
        from gi.repository import GLib
    except ImportError as e:
        sys.exit(f"The tracker needs python-dbus and python-gobject ({e}). "
                 "On Arch: sudo pacman -S python-dbus python-gobject")

    adopt_lightcurve()
    cfg = load_config()
    hub = hub if hub is not None else cfg.get("hub", "")
    db = core.open_db(local_db_path())
    lock = threading.RLock()
    device, host = device_identity(db)
    name = cfg.get("name") or host
    imported = import_waytime(db, device)
    if imported:
        log(f"brought over {imported} daily totals from waytime")

    ms = int(time.time() * 1000)
    with db:  # so reports read straight from this database know the machine too
        db.execute(
            "INSERT INTO devices (id, name, kind, platform, version, first_seen, last_seen) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET name = excluded.name, kind = excluded.kind, version = excluded.version, "
            "last_seen = excluded.last_seen", (device, name, device_kind(), "Plasma", core.VERSION, ms, ms))

    recorder = Recorder(db, device, lock)
    uploader = Uploader(db, lock, device, name, hub)

    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SessionBus()
    system_bus = dbus.SystemBus()
    script_path = os.path.join(core.data_dir(), "kwin-tracker.js")

    def policy_inhibited():
        """Is something (a playing video, a presentation) asking for the screen to stay on?"""
        try:
            agent = bus.get_object("org.kde.Solid.PowerManagement", "/org/kde/Solid/PowerManagement/PolicyAgent")
            return bool(agent.HasInhibition(dbus.UInt32(4), dbus_interface="org.kde.Solid.PowerManagement.PolicyAgent",
                                            timeout=2))
        except dbus.DBusException:
            return False

    idle = IdleGate(recorder, policy_inhibited)
    agents = AgentGate(recorder)

    def on_quiet(quiet):
        idle.normal(quiet, time.time())

    def on_long_quiet(quiet):
        # No input at all for WATCHING_LIMIT, whatever is playing: nobody is watching any more.
        idle.raw(quiet, time.time())

    def on_input(quiet):
        if not quiet:
            agents.input(time.time())

    class Service(dbus.service.Object):
        @dbus.service.method(BUS_NAME, in_signature="ss")
        def ReportActive(self, app, caption):
            app = str(app).strip()
            site = core.site_from_caption(str(caption)) if app.lower() in core.BROWSERS else ""
            recorder.set_focus(app, site, time.time())

        @dbus.service.method(BUS_NAME, out_signature="s")
        def Status(self):
            return json.dumps(status(recorder, uploader, device, name))

        @dbus.service.method(BUS_NAME, out_signature="b")
        def Push(self):
            return bool(uploader.push(recorder.active))

    # lightcurve's own tracker would count this machine a second time, under the same id.
    if bus.name_has_owner(OLD_BUS_NAME):
        log("stopping lightcurve's tracker: this one replaces it")
        subprocess.run(["systemctl", "--user", "disable", "--now", "lightcurve-tracker.service"],
                       capture_output=True, timeout=30)
        for _ in range(20):
            if not bus.name_has_owner(OLD_BUS_NAME):
                break
            time.sleep(0.5)
        else:
            sys.exit("lightcurve's tracker is still running. Stop it first: systemctl --user disable --now lightcurve-tracker")
    try:
        bus_name = dbus.service.BusName(BUS_NAME, bus=bus, do_not_queue=True)
    except dbus.exceptions.NameExistsException:
        sys.exit("Another ignition tracker is already running in this session.")
    service = Service(bus_name, OBJECT_PATH)  # noqa: F841 (kept alive by the bus)

    def load_script():
        try:
            os.makedirs(os.path.dirname(script_path), exist_ok=True)
            with open(script_path, "w") as f:
                f.write(KWIN_SCRIPT)
            scripting = dbus.Interface(bus.get_object("org.kde.KWin", "/Scripting"), "org.kde.kwin.Scripting")
            if scripting.isScriptLoaded(KWIN_SCRIPT_NAME):
                scripting.unloadScript(KWIN_SCRIPT_NAME)
            scripting.loadScript(script_path, KWIN_SCRIPT_NAME, signature="ss")  # the (path, name) overload
            scripting.start()
            log("watching window focus through KWin")
        except dbus.DBusException as e:
            log(f"couldn't load the KWin script, trying again in 10 s: {e.get_dbus_message()}")
            GLib.timeout_add_seconds(10, load_script)
        return False

    def on_kwin(owner):
        if owner:
            GLib.timeout_add_seconds(2, load_script)  # give its scripting engine a moment after a restart
        else:
            recorder.set_focus("", "", time.time())

    def on_lock(active):
        recorder.set_locked(bool(active), time.time())
        log("screen locked" if active else "screen unlocked")

    try:
        saver = bus.get_object("org.freedesktop.ScreenSaver", "/ScreenSaver")
        recorder.set_locked(bool(saver.GetActive(dbus_interface="org.freedesktop.ScreenSaver")), time.time())
    except dbus.DBusException:
        pass
    bus.add_signal_receiver(on_lock, signal_name="ActiveChanged", dbus_interface="org.freedesktop.ScreenSaver",
                            path="/ScreenSaver")
    bus.watch_name_owner("org.kde.KWin", on_kwin)

    def check_agents(now):
        local_agent = t3_agent_running()
        try:
            remote_agent = remote_agent_session(system_bus)
        except dbus.DBusException:
            remote_agent = False
        agents.update(local_agent, remote_agent, now)

    def on_session_new(*_):
        now = time.time()
        agents.note_remote(now)
        check_agents(now)

    def on_session_removed(*_):
        check_agents(time.time())

    system_bus.add_signal_receiver(on_session_new, signal_name="SessionNew",
                                   dbus_interface="org.freedesktop.login1.Manager", path="/org/freedesktop/login1")
    system_bus.add_signal_receiver(on_session_removed, signal_name="SessionRemoved",
                                   dbus_interface="org.freedesktop.login1.Manager", path="/org/freedesktop/login1")

    def tick():
        now = time.time()
        check_agents(now)
        # A video may have stopped, or an ordinary notice may have resumed without any actual input.
        idle.apply(now)
        recorder.beat(now)
        return True

    tick()  # don't record the first five seconds of an agent session that predates this process
    GLib.timeout_add_seconds(BEAT, tick)

    IdleWatch([
        (IDLE_AFTER, False, lambda quiet: GLib.idle_add(on_quiet, quiet)),
        (WATCHING_LIMIT, True, lambda quiet: GLib.idle_add(on_long_quiet, quiet)),
        (INPUT_WATCH, True, lambda quiet: GLib.idle_add(on_input, quiet), True),
    ], log=log).start()

    stopping = threading.Event()

    def push_loop():
        while True:
            uploader.push(recorder.active)
            if stopping.wait(PUSH_EVERY if uploader.error is None else 20):
                return

    if uploader.hub:
        threading.Thread(target=push_loop, daemon=True, name="push").start()
        log(f"sending to {uploader.hub} as “{name}”")
    else:
        log(f"no server set: recording on this machine only (put {{\"hub\": \"http://server:4580\"}} in {config_path()})")

    loop = GLib.MainLoop()

    def stop(*_):
        stopping.set()
        recorder.stop(time.time())
        try:
            dbus.Interface(bus.get_object("org.kde.KWin", "/Scripting"),
                           "org.kde.kwin.Scripting").unloadScript(KWIN_SCRIPT_NAME)
        except dbus.DBusException:
            pass
        uploader.push(False, timeout=4)
        loop.quit()
        return False

    for sig in (signal.SIGTERM, signal.SIGINT):
        GLib.unix_signal_add(GLib.PRIORITY_HIGH, sig, stop)

    log(f"ignition tracker {core.VERSION} started")
    loop.run()


def status(recorder, uploader, device, name):
    with recorder.lock:
        waiting = recorder.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]
    return {
        "device": device, "name": name, "version": core.VERSION, "hub": uploader.hub,
        "hub_error": uploader.error, "last_push": uploader.last_ok, "waiting": waiting,
        "recording": recorder.active, "locked": recorder.locked, "idle": recorder.idle, "agent": recorder.agent,
        "now": {"app": recorder.open["app"], "site": recorder.open["site"]} if recorder.open else None,
    }
