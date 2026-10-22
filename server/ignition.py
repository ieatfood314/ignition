#!/usr/bin/env python3
"""Ignition: a tiny to-do / want-to-do / focus-timer server. Stdlib only."""

import argparse
import json
import mimetypes
import os
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
LISTS = ("need", "want")
EDITABLE = ("title", "estimate_min", "due", "priority", "notes", "list", "done")
EARLY_STOP_MIN_MINUTES = 60
MAX_MINUTES = 12 * 60

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
"""


class Store:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.lock = threading.Lock()

    def q(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def x(self, sql, args=()):
        with self.lock:
            return self.db.execute(sql, args).lastrowid


def now():
    return int(time.time())


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def clean_item_fields(body, partial):
    out = {}
    for key in EDITABLE:
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
        elif key == "done":
            key, val = "done_at", (now() if val else None)
        out[key] = val
    if not partial and "title" not in out:
        raise ApiError(400, "title is required")
    return out


class App:
    def __init__(self, store):
        self.s = store

    def items(self):
        return self.s.q("SELECT * FROM items ORDER BY id")

    def create_item(self, body):
        f = clean_item_fields(body, partial=False)
        f.setdefault("list", "need")
        f["created_at"] = now()
        if f.get("list") == "want":
            f.pop("due", None)
        cols = ", ".join(f)
        marks = ", ".join("?" for _ in f)
        rid = self.s.x(f"INSERT INTO items ({cols}) VALUES ({marks})", list(f.values()))
        return self.s.q("SELECT * FROM items WHERE id=?", (rid,))[0]

    def update_item(self, rid, body):
        f = clean_item_fields(body, partial=True)
        if f.get("list") == "want":
            f["due"] = None
        if f:
            sets = ", ".join(f"{k}=?" for k in f)
            self.s.x(f"UPDATE items SET {sets} WHERE id=?", [*f.values(), rid])
        rows = self.s.q("SELECT * FROM items WHERE id=?", (rid,))
        if not rows:
            raise ApiError(404, "no such item")
        return rows[0]

    def delete_item(self, rid):
        self.s.x("DELETE FROM items WHERE id=?", (rid,))
        return {"ok": True}

    def current_session(self):
        rows = self.s.q(
            "SELECT * FROM sessions WHERE stopped_at IS NULL AND ends_at > ? "
            "ORDER BY id DESC LIMIT 1",
            (now(),),
        )
        return rows[0] if rows else None

    def ignite_state(self):
        sess = self.current_session()
        t = now()
        enforcers = [
            {**e, "status": json.loads(e["status"]), "age": t - e["last_seen"]}
            for e in self.s.q("SELECT * FROM enforcers ORDER BY host")
        ]
        state = {"now": t, "active": bool(sess), "enforcers": enforcers}
        if sess:
            state.update(
                session_id=sess["id"],
                minutes=sess["minutes"],
                started_at=sess["started_at"],
                ends_at=sess["ends_at"],
                can_stop=sess["minutes"] > EARLY_STOP_MIN_MINUTES,
            )
        return state

    def ignite(self, body):
        if self.current_session():
            raise ApiError(409, "already ignited")
        try:
            minutes = int(body.get("minutes"))
        except (TypeError, ValueError):
            raise ApiError(400, "minutes must be a number")
        if not 1 <= minutes <= MAX_MINUTES:
            raise ApiError(400, f"minutes must be 1-{MAX_MINUTES}")
        t = now()
        self.s.x(
            "INSERT INTO sessions (minutes, started_at, ends_at) VALUES (?, ?, ?)",
            (minutes, t, t + minutes * 60),
        )
        return self.ignite_state()

    def stop(self):
        sess = self.current_session()
        if not sess:
            return self.ignite_state()
        if sess["minutes"] <= EARLY_STOP_MIN_MINUTES:
            raise ApiError(403, "sessions of an hour or less can't be ended early")
        self.s.x("UPDATE sessions SET stopped_at=? WHERE id=?", (now(), sess["id"]))
        return self.ignite_state()

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
        return self.ignite_state()


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
            path = urlparse(self.path).path.rstrip("/") or "/"
            parts = path.strip("/").split("/")
            if parts[0] != "api":
                if method != "GET":
                    raise ApiError(405, "method not allowed")
                return self.static(path)
            r = parts[1:]
            if r == ["items"] and method == "GET":
                return app.items()
            if r == ["items"] and method == "POST":
                return app.create_item(self.body())
            if len(r) == 2 and r[0] == "items" and r[1].isdigit():
                rid = int(r[1])
                if method == "PATCH":
                    return app.update_item(rid, self.body())
                if method == "DELETE":
                    return app.delete_item(rid)
            if r == ["ignite"] and method == "GET":
                return app.ignite_state()
            if r == ["ignite"] and method == "POST":
                return app.ignite(self.body())
            if r == ["ignite", "stop"] and method == "POST":
                return app.stop()
            if r == ["enforcer", "heartbeat"] and method == "POST":
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

        def do_PATCH(self):
            self.handle_method("PATCH")

        def do_DELETE(self):
            self.handle_method("DELETE")

    return Handler


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
    srv = ThreadingHTTPServer((a.host, a.port), make_handler(app))
    print(f"ignition on http://{a.host}:{a.port} db={a.db}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
