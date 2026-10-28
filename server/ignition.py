#!/usr/bin/env python3
"""Ignition: need-to-do / want-to-do lists and a focus timer that locks out distractions. Stdlib only.

Data model (shared with the Android app, which syncs through POST /api/sync):
  items     uid, list, title, estimate_min, due, priority, notes, created_at, done_at
  sessions  uid, minutes, planned, created_at, starts_at, ends_at, stopped_at, source
  settings  one JSON document (goals, schedules)
Every row carries updated_at (ms, last-writer-wins), deleted (tombstone) and rev (server change counter).
Times are unix seconds except updated_at.
"""

import argparse
import hashlib
import json
import mimetypes
import os
import re
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
LISTS = ("need", "want")
EARLY_STOP_OVER_MINUTES = 60
SCRUB_LOCK = 3600  # seconds before liftoff after which a countdown can't be scrubbed...
SCRUB_GRACE = 60  # ...unless it was set less than this many seconds ago
MAX_MINUTES = 12 * 60
MAX_DELAY = 24 * 60
SCHEDULE_WINDOW = 120  # seconds after a scheduled time during which it may still fire

ITEM_FIELDS = ("list", "title", "estimate_min", "due", "priority", "notes", "created_at", "done_at")
SESSION_FIELDS = ("minutes", "planned", "created_at", "starts_at", "ends_at", "stopped_at", "source")
DEFAULT_LAUNCH_WINDOW = {
    "enabled": False, "days": [1, 2, 3, 4, 5], "start": "15:00", "end": "17:30",
    "trigger": "home", "after": 20, "minutes": 50, "tminus": 10,
}
DEFAULT_SETTINGS = {
    "goals": {"daily": 0, "weekly": 0},
    "schedules": [],
    "block": {"sites": [], "apps": [], "games": False, "programs": []},
    "launch_window": DEFAULT_LAUNCH_WINDOW,
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY,
    list TEXT NOT NULL,
    title TEXT NOT NULL,
    estimate_min INTEGER,
    due TEXT,
    priority INTEGER NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    done_at INTEGER
);
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY,
    minutes INTEGER NOT NULL,
    started_at INTEGER NOT NULL,
    ends_at INTEGER NOT NULL,
    stopped_at INTEGER
);
CREATE TABLE IF NOT EXISTS enforcers (
    host TEXT PRIMARY KEY,
    last_seen INTEGER NOT NULL,
    status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at INTEGER NOT NULL DEFAULT 0,
    rev INTEGER NOT NULL DEFAULT 0
);
"""

MIGRATIONS = {
    "items": [
        ("uid", "TEXT"), ("updated_at", "INTEGER NOT NULL DEFAULT 0"),
        ("deleted", "INTEGER NOT NULL DEFAULT 0"), ("rev", "INTEGER NOT NULL DEFAULT 0"),
    ],
    "sessions": [
        ("uid", "TEXT"), ("planned", "INTEGER"), ("created_at", "INTEGER"), ("source", "TEXT NOT NULL DEFAULT 'manual'"),
        ("updated_at", "INTEGER NOT NULL DEFAULT 0"), ("deleted", "INTEGER NOT NULL DEFAULT 0"),
        ("rev", "INTEGER NOT NULL DEFAULT 0"),
    ],
}


def web_build():
    """Changes whenever a file under web/ does, so open pages know to reload after a deploy."""
    h = hashlib.sha1()
    for f in sorted(WEB_DIR.iterdir()):
        st = f.stat()
        h.update(f"{f.name}:{st.st_mtime_ns}:{st.st_size};".encode())
    return h.hexdigest()[:12]


def now():
    return int(time.time())


def now_ms():
    return int(time.time() * 1000)


def new_uid():
    return secrets.token_hex(8)


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class Store:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.lock = threading.RLock()
        self.migrate()

    def migrate(self):
        for table, cols in MIGRATIONS.items():
            have = {r["name"] for r in self.db.execute(f"PRAGMA table_info({table})")}
            for name, decl in cols:
                if name not in have:
                    self.db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
        for table in ("items", "sessions"):
            for r in self.db.execute(f"SELECT id FROM {table} WHERE uid IS NULL").fetchall():
                self.db.execute(f"UPDATE {table} SET uid=?, updated_at=?, rev=? WHERE id=?",
                                (new_uid(), now_ms(), self.next_rev(), r["id"]))
        self.db.execute("UPDATE sessions SET planned=minutes WHERE planned IS NULL")
        self.db.execute("UPDATE sessions SET created_at=started_at WHERE created_at IS NULL")
        self.db.execute("CREATE UNIQUE INDEX IF NOT EXISTS items_uid ON items(uid)")
        self.db.execute("CREATE UNIQUE INDEX IF NOT EXISTS sessions_uid ON sessions(uid)")

    def next_rev(self):
        with self.lock:
            row = self.db.execute("SELECT value FROM kv WHERE key='_rev'").fetchone()
            rev = (int(row["value"]) if row else 0) + 1
            self.db.execute("INSERT INTO kv (key, value) VALUES ('_rev', ?) "
                            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(rev),))
            return rev

    def rev(self):
        row = self.db.execute("SELECT value FROM kv WHERE key='_rev'").fetchone()
        return int(row["value"]) if row else 0

    def q(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def x(self, sql, args=()):
        with self.lock:
            return self.db.execute(sql, args).lastrowid


# ---------------------------------------------------------------- validation


def clean_item(body, partial):
    out = {}
    for key in ITEM_FIELDS + ("done",):
        if key not in body:
            continue
        val = body[key]
        if key == "title":
            val = str(val or "").strip()
            if not val:
                raise ApiError(400, "title is required")
            val = val[:500]
        elif key == "estimate_min":
            val = None if val in (None, "") else max(0, int(val))
        elif key == "due":
            val = None if not val else str(val)[:10]
        elif key == "priority":
            val = min(3, max(0, int(val or 0)))
        elif key == "notes":
            val = str(val or "")[:20000]
        elif key == "list":
            if val not in LISTS:
                raise ApiError(400, "bad list")
        elif key in ("created_at", "done_at"):
            val = None if val in (None, "") else int(val)
        elif key == "done":
            key, val = "done_at", (now() if val else None)
        out[key] = val
    if not partial and "title" not in out:
        raise ApiError(400, "title is required")
    return out


def clean_session(body):
    out = {}
    for key in SESSION_FIELDS:
        if key in body:
            val = body[key]
            out[key] = str(val or "manual")[:20] if key == "source" else (None if val is None else int(val))
    return out


def clean_time(val, default):
    try:
        hh, mm = (str(val or default) + ":00").split(":")[:2]
        return f"{min(23, max(0, int(hh))):02d}:{min(59, max(0, int(mm))):02d}"
    except ValueError:
        return default


def clean_days(val):
    days = sorted({int(d) for d in (val or []) if str(d).isdigit() and 1 <= int(d) <= 7})
    return days or [1, 2, 3, 4, 5, 6, 7]


def clamp_int(val, lo, hi, default):
    try:
        return max(lo, min(hi, int(val)))
    except (TypeError, ValueError):
        return default


SITE_RE = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+(:\d{1,5})?$")
PROGRAM_RE = re.compile(r"^[A-Za-z0-9._+-]{1,64}$")
PACKAGE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+$")


def clean_site(val):
    """'https://www.YouTube.com/watch?v=x' -> 'youtube.com'. Subdomains are always included when matching."""
    s = str(val or "").strip().lower()
    s = re.sub(r"^[a-z]+://", "", s).split("/")[0].split("?")[0].split("#")[0]
    s = s.split("@")[-1].strip(".")
    if s.startswith("www."):
        s = s[4:]
    return s if SITE_RE.match(s) else None


def clean_block(body):
    sites, programs, apps, seen = [], [], [], set()
    for v in (body.get("sites") or [])[:300]:
        site = clean_site(v)
        if site and site not in sites:
            sites.append(site)
    for v in (body.get("programs") or [])[:100]:
        name = str(v or "").strip()
        if PROGRAM_RE.match(name) and name not in programs:
            programs.append(name)
    for v in (body.get("apps") or [])[:300]:
        app = v if isinstance(v, dict) else {"id": v}
        pkg = str(app.get("id") or "").strip()
        if PACKAGE_RE.match(pkg) and pkg not in seen:
            seen.add(pkg)
            apps.append({"id": pkg[:200], "label": str(app.get("label") or pkg).strip()[:80]})
    return {"sites": sites, "apps": apps, "games": bool(body.get("games")), "programs": programs}


def clean_launch_window(body):
    d = DEFAULT_LAUNCH_WINDOW
    return {
        "enabled": bool(body.get("enabled", d["enabled"])),
        "days": clean_days(body.get("days") or d["days"]),
        "start": clean_time(body.get("start"), d["start"]),
        "end": clean_time(body.get("end"), d["end"]),
        "trigger": body.get("trigger") if body.get("trigger") in ("home", "time") else d["trigger"],
        "after": clamp_int(body.get("after"), 0, 240, d["after"]),
        "minutes": clamp_int(body.get("minutes"), 1, MAX_MINUTES, d["minutes"]),
        "tminus": clamp_int(body.get("tminus"), 0, 120, d["tminus"]),
    }


def clean_settings(body):
    goals = body.get("goals") or {}
    s = {"goals": {k: clamp_int(goals.get(k) or 0, 0, 24 * 60 * 7, 0) for k in ("daily", "weekly")}}
    scheds = []
    for sc in (body.get("schedules") or [])[:20]:
        scheds.append({
            "id": str(sc.get("id") or new_uid())[:32],
            "time": clean_time(sc.get("time"), "09:00"),
            "days": clean_days(sc.get("days")),
            "minutes": clamp_int(sc.get("minutes") or 50, 1, MAX_MINUTES, 50),
            "lead": clamp_int(sc.get("lead") if sc.get("lead") is not None else 5, 0, 60, 5),
            "enabled": bool(sc.get("enabled", True)),
        })
    s["schedules"] = scheds
    s["block"] = clean_block(body.get("block") or {})
    s["launch_window"] = clean_launch_window(body.get("launch_window") or {})
    return s


# ---------------------------------------------------------------- time helpers


def local_midnight(ts):
    d = datetime.fromtimestamp(ts)
    return int(datetime(d.year, d.month, d.day).timestamp())


def schedule_occurrences(sched, start_ts, days_ahead):
    """Yield (unix time, YYYYMMDD) for each occurrence of a schedule from start_ts's day onwards."""
    hh, mm = map(int, sched["time"].split(":"))
    base = datetime.fromtimestamp(start_ts).replace(hour=0, minute=0, second=0, microsecond=0)
    for n in range(days_ahead + 1):
        day = base + timedelta(days=n)
        if day.isoweekday() in sched["days"]:
            at = day.replace(hour=hh, minute=mm)
            yield int(at.timestamp()), at.strftime("%Y%m%d")


def schedule_uid(sched_id, ymd):
    return f"sched-{sched_id}-{ymd}"


# ---------------------------------------------------------------- app


class App:
    def __init__(self, store):
        self.s = store

    # ---- serialisation

    @staticmethod
    def item_out(r):
        return {
            "id": r["uid"], "list": r["list"], "title": r["title"], "estimate_min": r["estimate_min"],
            "due": r["due"], "priority": r["priority"], "notes": r["notes"], "created_at": r["created_at"],
            "done_at": r["done_at"], "updated_at": r["updated_at"], "deleted": bool(r["deleted"]),
        }

    @staticmethod
    def session_out(r):
        return {
            "id": r["uid"], "minutes": r["minutes"], "planned": r["planned"], "created_at": r["created_at"],
            "starts_at": r["started_at"], "ends_at": r["ends_at"], "stopped_at": r["stopped_at"],
            "source": r["source"], "updated_at": r["updated_at"], "deleted": bool(r["deleted"]),
        }

    # ---- items

    def items(self):
        return [self.item_out(r) for r in self.s.q("SELECT * FROM items WHERE deleted=0 ORDER BY id")]

    def get_item(self, uid):
        rows = self.s.q("SELECT * FROM items WHERE uid=?", (uid,))
        if not rows:
            raise ApiError(404, "no such item")
        return rows[0]

    def create_item(self, body):
        f = clean_item(body, partial=False)
        f.setdefault("list", "need")
        f.setdefault("created_at", now())
        if f["list"] == "want":
            f["due"] = None
        uid = str(body.get("id") or new_uid())[:40]
        with self.s.lock:
            if self.s.q("SELECT 1 FROM items WHERE uid=?", (uid,)):
                raise ApiError(409, "item exists")
            f.update(uid=uid, updated_at=now_ms(), rev=self.s.next_rev())
            self.s.x(f"INSERT INTO items ({', '.join(f)}) VALUES ({', '.join('?' for _ in f)})", list(f.values()))
            return self.item_out(self.get_item(uid))

    def update_item(self, uid, body):
        f = clean_item(body, partial=True)
        if f.get("list") == "want":
            f["due"] = None
        with self.s.lock:
            self.get_item(uid)
            f.update(updated_at=now_ms(), rev=self.s.next_rev())
            self.s.x(f"UPDATE items SET {', '.join(f'{k}=?' for k in f)} WHERE uid=?", [*f.values(), uid])
            return self.item_out(self.get_item(uid))

    def delete_item(self, uid):
        with self.s.lock:
            self.s.x("UPDATE items SET deleted=1, updated_at=?, rev=? WHERE uid=?", (now_ms(), self.s.next_rev(), uid))
        return {"ok": True}

    # ---- settings

    def settings(self):
        row = self.s.q("SELECT * FROM kv WHERE key='settings'")
        if not row:
            return clean_settings({}), 0
        return clean_settings(json.loads(row[0]["value"])), row[0]["updated_at"]

    def put_settings(self, body, updated_at=None):
        value = clean_settings(body)
        with self.s.lock:
            self.s.x(
                "INSERT INTO kv (key, value, updated_at, rev) VALUES ('settings', ?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at, rev=excluded.rev",
                (json.dumps(value), updated_at or now_ms(), self.s.next_rev()),
            )
        return value

    # ---- sessions

    def sessions_live(self, t):
        return self.s.q(
            "SELECT * FROM sessions WHERE deleted=0 AND stopped_at IS NULL AND ends_at > ? ORDER BY started_at",
            (t,),
        )

    def current(self, t=None):
        """(phase, row): the burning session if any, else the next countdown, else ('idle', None)."""
        t = t or now()
        live = self.sessions_live(t)
        burning = [r for r in live if r["started_at"] <= t]
        if burning:
            return "burning", burning[-1]
        if live:
            return "countdown", live[0]
        return "idle", None

    @staticmethod
    def scrub_until(row):
        """When scrubbing this countdown stops being allowed: an hour out, or a minute after setting it."""
        return max((row["created_at"] or 0) + SCRUB_GRACE, row["started_at"] - SCRUB_LOCK)

    def insert_session(self, uid, minutes, starts_at, source, planned=None):
        t = now()
        with self.s.lock:
            self.s.x(
                "INSERT INTO sessions (uid, minutes, planned, created_at, started_at, ends_at, source, updated_at, rev) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (uid, minutes, planned or minutes, t, starts_at, starts_at + minutes * 60, source, now_ms(), self.s.next_rev()),
            )

    def touch_session(self, row, **changes):
        with self.s.lock:
            changes.update(updated_at=now_ms(), rev=self.s.next_rev())
            self.s.x(f"UPDATE sessions SET {', '.join(f'{k}=?' for k in changes)} WHERE uid=?",
                     [*changes.values(), row["uid"]])

    def ignite(self, body):
        try:
            minutes = int(body.get("minutes"))
            delay = int(body.get("delay") or 0)
        except (TypeError, ValueError):
            raise ApiError(400, "minutes must be a number")
        if not 1 <= minutes <= MAX_MINUTES:
            raise ApiError(400, f"minutes must be 1-{MAX_MINUTES}")
        if not 0 <= delay <= MAX_DELAY:
            raise ApiError(400, f"T-minus must be 0-{MAX_DELAY} minutes")
        with self.s.lock:
            phase, _ = self.current()
            if phase == "burning":
                raise ApiError(409, "already burning")
            if phase == "countdown":
                raise ApiError(409, "a countdown is already running")
            self.insert_session(str(body.get("id") or new_uid())[:40], minutes, now() + delay * 60, "manual")
        return self.ignite_state()

    def stop(self):
        with self.s.lock:
            phase, row = self.current()
            if phase == "countdown":
                return self.scrub()
            if phase == "burning":
                if (row["planned"] or row["minutes"]) <= EARLY_STOP_OVER_MINUTES:
                    raise ApiError(403, "no way out")
                self.touch_session(row, stopped_at=now())
        return self.ignite_state()

    def scrub(self):
        with self.s.lock:
            phase, row = self.current()
            if phase != "countdown":
                raise ApiError(409, "nothing to scrub")
            if now() >= self.scrub_until(row):
                raise ApiError(403, "under an hour to liftoff: no scrubbing")
            self.touch_session(row, stopped_at=now())
        return self.ignite_state()

    def ignite_now(self):
        with self.s.lock:
            phase, row = self.current()
            if phase != "countdown":
                raise ApiError(409, "no countdown")
            t = now()
            self.touch_session(row, started_at=t, ends_at=t + row["minutes"] * 60)
        return self.ignite_state()

    def extend(self, body):
        try:
            minutes = int(body.get("minutes"))
        except (TypeError, ValueError):
            raise ApiError(400, "minutes must be a number")
        if not 1 <= minutes <= MAX_MINUTES:
            raise ApiError(400, "bad minutes")
        with self.s.lock:
            phase, row = self.current()
            if phase == "idle":
                raise ApiError(409, "nothing burning")
            if row["minutes"] + minutes > MAX_MINUTES:
                raise ApiError(400, "that's too long")
            self.touch_session(row, minutes=row["minutes"] + minutes, ends_at=row["ends_at"] + minutes * 60)
        return self.ignite_state()

    def skip(self, body):
        """Skip one occurrence of a schedule (writes a tombstone with the deterministic uid)."""
        sid, ymd = str(body.get("schedule_id") or ""), str(body.get("date") or "")
        if not sid or not ymd.isdigit():
            raise ApiError(400, "schedule_id and date (YYYYMMDD) required")
        uid = schedule_uid(sid, ymd)
        with self.s.lock:
            rows = self.s.q("SELECT * FROM sessions WHERE uid=?", (uid,))
            if rows:
                if rows[0]["started_at"] > now():
                    self.touch_session(rows[0], stopped_at=now())
            else:
                self.insert_session(uid, 1, now(), "schedule")
                self.touch_session({"uid": uid}, deleted=1)
        return self.ignite_state()

    # ---- schedules

    def run_schedules(self, t=None):
        t = t or now()
        settings, _ = self.settings()
        for sc in settings["schedules"]:
            if not sc["enabled"]:
                continue
            for at, ymd in schedule_occurrences(sc, t - SCHEDULE_WINDOW, 1):
                if not at <= t < at + SCHEDULE_WINDOW:
                    continue
                uid = schedule_uid(sc["id"], ymd)
                with self.s.lock:
                    if self.s.q("SELECT 1 FROM sessions WHERE uid=?", (uid,)):
                        continue
                    if self.current(t)[0] != "idle":
                        continue
                    self.insert_session(uid, sc["minutes"], at, "schedule")

    def next_schedule(self, t):
        settings, _ = self.settings()
        best = None
        for sc in settings["schedules"]:
            if not sc["enabled"]:
                continue
            for at, ymd in schedule_occurrences(sc, t, 7):
                if at <= t or self.s.q("SELECT 1 FROM sessions WHERE uid=?", (schedule_uid(sc["id"], ymd),)):
                    continue
                if best is None or at < best["at"]:
                    best = {"at": at, "date": ymd, "schedule_id": sc["id"], "minutes": sc["minutes"], "lead": sc["lead"]}
                break
        return best

    # ---- stats

    def stats(self, t=None):
        t = t or now()
        today = local_midnight(t)
        week = today - (datetime.fromtimestamp(t).isoweekday() - 1) * 86400
        week = local_midnight(week + 7200)  # tolerate DST shifts
        first = min(week, today - 6 * 86400)
        burns = []
        for r in self.s.q("SELECT * FROM sessions WHERE deleted=0 AND started_at < ? AND ends_at > ?", (t, first)):
            end = min(r["ends_at"], r["stopped_at"] or r["ends_at"], t)
            if end > r["started_at"]:
                burns.append((r["started_at"], end))

        def burned(a, b):
            return sum(max(0, min(e, b) - max(s, a)) for s, e in burns)

        days = []
        for n in range(6, -1, -1):
            d0 = local_midnight(today - n * 86400 + 7200)
            d1 = local_midnight(d0 + 30 * 3600)
            days.append({"date": datetime.fromtimestamp(d0).strftime("%Y-%m-%d"), "seconds": burned(d0, d1)})
        settings, _ = self.settings()
        return {
            "today": burned(today, t + 1), "week": burned(week, t + 1),
            "goals": settings["goals"], "days": days,
        }

    def burns(self, t=None):
        """Every stretch actually burned, oldest first, for the Stats page. The page buckets them itself,
        in the viewer's own timezone."""
        t = t or now()
        out = []
        for r in self.s.q("SELECT * FROM sessions WHERE deleted=0 AND started_at < ? ORDER BY started_at", (t,)):
            stopped = r["stopped_at"]
            end = min(r["ends_at"], stopped or r["ends_at"], t)
            if end <= r["started_at"]:
                continue  # scrubbed before liftoff
            out.append({
                "id": r["uid"], "start": r["started_at"], "end": end, "minutes": r["minutes"],
                "planned": r["planned"] or r["minutes"], "source": r["source"],
                "early": stopped is not None and stopped < r["ends_at"],
                "live": stopped is None and r["ends_at"] > t,
            })
        return {"now": t, "burns": out}

    # ---- state

    def ignite_state(self):
        t = now()
        phase, row = self.current(t)
        enforcers = [
            {**e, "status": json.loads(e["status"]), "age": t - e["last_seen"]}
            for e in self.s.q("SELECT * FROM enforcers ORDER BY host")
        ]
        state = {
            "now": t, "phase": phase, "active": phase == "burning", "enforcers": enforcers,
            "session": self.session_out(row) if row else None,
            "next_schedule": self.next_schedule(t), "stats": self.stats(t), "build": web_build(),
        }
        if row:
            planned = row["planned"] or row["minutes"]
            state.update(
                # Flat fields kept for the enforcers and older clients.
                session_id=row["uid"], minutes=row["minutes"], started_at=row["started_at"],
                ends_at=row["ends_at"], can_stop=planned > EARLY_STOP_OVER_MINUTES,
            )
            if phase == "countdown":
                state["scrub_until"] = self.scrub_until(row)
        return state

    def heartbeat(self, body):
        host = str(body.get("host") or "")[:64]
        if not host:
            raise ApiError(400, "host required")
        status = json.dumps(body.get("status") or {})[:4000]
        self.s.x(
            "INSERT INTO enforcers (host, last_seen, status) VALUES (?, ?, ?) "
            "ON CONFLICT(host) DO UPDATE SET last_seen=excluded.last_seen, status=excluded.status",
            (host, now(), status),
        )
        block = self.settings()[0]["block"]
        return {**self.ignite_state(), "block": {"sites": block["sites"], "programs": block["programs"]}}

    # ---- sync (Android app)

    def sync(self, body):
        try:
            since = int(body.get("since") or 0)
        except (TypeError, ValueError):
            raise ApiError(400, "bad since")
        with self.s.lock:
            for it in body.get("items") or []:
                self.merge_item(it)
            for se in body.get("sessions") or []:
                self.merge_session(se)
            st = body.get("settings")
            if st and isinstance(st.get("value"), dict):
                _, cur_at = self.settings()
                if int(st.get("updated_at") or 0) > cur_at:
                    self.put_settings(st["value"], int(st["updated_at"]))
            out = {
                "rev": self.s.rev(), "now": now(),
                "items": [self.item_out(r) for r in self.s.q("SELECT * FROM items WHERE rev > ?", (since,))],
                "sessions": [self.session_out(r) for r in self.s.q("SELECT * FROM sessions WHERE rev > ?", (since,))],
                "enforcers": [
                    {**e, "status": json.loads(e["status"]), "age": now() - e["last_seen"]}
                    for e in self.s.q("SELECT * FROM enforcers ORDER BY host")
                ],
            }
            row = self.s.q("SELECT * FROM kv WHERE key='settings' AND rev > ?", (since,))
            if row:
                out["settings"] = {"value": json.loads(row[0]["value"]), "updated_at": row[0]["updated_at"]}
        return out

    def merge_item(self, it):
        uid = str(it.get("id") or "")[:40]
        if not uid:
            return
        incoming_at = int(it.get("updated_at") or 0)
        cur = self.s.q("SELECT updated_at FROM items WHERE uid=?", (uid,))
        if cur and cur[0]["updated_at"] >= incoming_at:
            return
        try:
            f = clean_item(it, partial=bool(cur))
        except ApiError:
            return
        f.setdefault("created_at", now())
        f.update(updated_at=incoming_at, deleted=1 if it.get("deleted") else 0, rev=self.s.next_rev())
        if cur:
            self.s.x(f"UPDATE items SET {', '.join(f'{k}=?' for k in f)} WHERE uid=?", [*f.values(), uid])
        else:
            f.setdefault("list", "need")
            f["uid"] = uid
            self.s.x(f"INSERT INTO items ({', '.join(f)}) VALUES ({', '.join('?' for _ in f)})", list(f.values()))

    def merge_session(self, se):
        uid = str(se.get("id") or "")[:40]
        if not uid:
            return
        incoming_at = int(se.get("updated_at") or 0)
        cur = self.s.q("SELECT updated_at FROM sessions WHERE uid=?", (uid,))
        if cur and cur[0]["updated_at"] >= incoming_at:
            return
        f = clean_session(se)
        if "starts_at" in f:
            f["started_at"] = f.pop("starts_at")
        if not cur and not all(k in f for k in ("minutes", "started_at", "ends_at")):
            return
        f.update(updated_at=incoming_at, deleted=1 if se.get("deleted") else 0, rev=self.s.next_rev())
        if cur:
            self.s.x(f"UPDATE sessions SET {', '.join(f'{k}=?' for k in f)} WHERE uid=?", [*f.values(), uid])
        else:
            f["uid"] = uid
            f.setdefault("planned", f["minutes"])
            f.setdefault("created_at", now())
            self.s.x(f"INSERT INTO sessions ({', '.join(f)}) VALUES ({', '.join('?' for _ in f)})", list(f.values()))


# ---------------------------------------------------------------- http


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ignition"

        def log_message(self, fmt, *args):
            pass

        def send_json(self, status, obj):
            data = json.dumps(obj).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def body(self):
            n = int(self.headers.get("Content-Length") or 0)
            if not n:
                return {}
            try:
                return json.loads(self.rfile.read(n))
            except json.JSONDecodeError:
                raise ApiError(400, "bad json")

        def route(self, method):
            url = urlparse(self.path)
            path = url.path.rstrip("/") or "/"
            parts = path.strip("/").split("/")
            if parts[0] != "api":
                if method != "GET":
                    raise ApiError(405, "method not allowed")
                return self.static(path)
            r, m = parts[1:], method
            if r == ["items"]:
                if m == "GET":
                    return app.items()
                if m == "POST":
                    return app.create_item(self.body())
            if len(r) == 2 and r[0] == "items":
                if m == "PATCH":
                    return app.update_item(r[1], self.body())
                if m == "DELETE":
                    return app.delete_item(r[1])
            if r == ["ignite"]:
                if m == "GET":
                    return app.ignite_state()
                if m == "POST":
                    return app.ignite(self.body())
            if len(r) == 2 and r[0] == "ignite" and m == "POST":
                actions = {
                    "stop": lambda: app.stop(), "scrub": lambda: app.scrub(), "now": lambda: app.ignite_now(),
                    "extend": lambda: app.extend(self.body()), "skip": lambda: app.skip(self.body()),
                }
                if r[1] in actions:
                    return actions[r[1]]()
            if r == ["settings"]:
                if m == "GET":
                    return app.settings()[0]
                if m == "PUT":
                    return app.put_settings(self.body())
            if r == ["stats"] and m == "GET":
                return app.stats()
            if r == ["burns"] and m == "GET":
                return app.burns()
            if r == ["sync"] and m == "POST":
                return app.sync(self.body())
            if r == ["ping"] and m == "GET":
                return {"ignition": True, "now": now(), "rev": app.s.rev()}
            if r == ["enforcer", "heartbeat"] and m == "POST":
                return app.heartbeat(self.body())
            raise ApiError(404, "not found")

        def static(self, path):
            rel = "index.html" if path == "/" else path.lstrip("/")
            target = (WEB_DIR / rel).resolve()
            if WEB_DIR not in target.parents or not target.is_file():
                target = WEB_DIR / "index.html"
            data = target.read_bytes()
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if target.suffix == ".webmanifest":
                ctype = "application/manifest+json"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return None

        def handle_method(self, method):
            try:
                result = self.route(method)
                if result is not None:
                    self.send_json(200, result)
            except ApiError as e:
                self.send_json(e.status, {"error": str(e)})
            except Exception as e:  # noqa: BLE001
                self.send_json(500, {"error": f"{type(e).__name__}: {e}"})

        def do_GET(self):
            self.handle_method("GET")

        def do_POST(self):
            self.handle_method("POST")

        def do_PUT(self):
            self.handle_method("PUT")

        def do_PATCH(self):
            self.handle_method("PATCH")

        def do_DELETE(self):
            self.handle_method("DELETE")

    return Handler


def scheduler(app):
    while True:
        try:
            app.run_schedules()
        except Exception as e:  # noqa: BLE001
            print(f"schedule check failed: {e}", flush=True)
        time.sleep(15)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=4580)
    p.add_argument(
        "--db",
        type=Path,
        default=Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "ignition/ignition.db",
    )
    a = p.parse_args()
    app = App(Store(a.db))
    threading.Thread(target=scheduler, args=(app,), daemon=True).start()
    srv = ThreadingHTTPServer((a.host, a.port), make_handler(app))
    print(f"ignition on http://{a.host}:{a.port} db={a.db}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
