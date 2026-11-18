"""Screen time inside the ignition server: the record every device sends to, its reports, and the limits.

This was lightcurve's hub before the two apps became one. It keeps its own SQLite file next to ignition's,
and the API keeps lightcurve's paths, so a tracker or phone from before the merge only needs the address.
"""

import json
import re
import sqlite3
import threading
import time
from pathlib import Path

from . import core, limits, link

ENFORCE_TTL = 3  # seconds an answer to "what do computers block" is reused; every enforcer asks every 5


class Screen:
    def __init__(self, db_path, ignition):
        """`ignition` is the server's App: burns and the block list come straight from it."""
        self.path = str(db_path)
        self.db = core.open_db(self.path)
        self.lock = threading.RLock()
        self.cache = core.Cache()
        self.ig = ignition
        self._enforce = (0, None)

    # -- importing lightcurve's record --

    def import_lightcurve(self, src):
        """Bring in a lightcurve hub's record. Safe to run again: newer copies of the same rows win."""
        src = Path(src)
        if not src.is_file():
            return 0
        tmp = Path(self.path).with_name(".lightcurve-import.db")
        # sqlite's backup reads a consistent copy even while lightcurve is still writing to it.
        with sqlite3.connect(f"file:{src}?mode=ro", uri=True) as s, sqlite3.connect(tmp) as d:
            s.backup(d)
        n = 0
        with self.lock, self.db:
            self.db.execute("ATTACH DATABASE ? AS lc", (str(tmp),))
            try:
                have = {r[0] for r in self.db.execute("SELECT name FROM lc.sqlite_master WHERE type='table'")}
                for table in ("devices", "spans", "pickups", "names", "categories", "legacy", "limits", "kv"):
                    if table not in have:
                        continue
                    cols = [r[1] for r in self.db.execute(f"PRAGMA lc.table_info({table})")]
                    mine = {r[1] for r in self.db.execute(f"PRAGMA main.table_info({table})")}
                    cols = [c for c in cols if c in mine]
                    names = ", ".join(cols)
                    verb = "INSERT OR IGNORE" if table == "kv" else "INSERT OR REPLACE"
                    cur = self.db.execute(f"{verb} INTO main.{table} ({names}) SELECT {names} FROM lc.{table}")
                    n += max(cur.rowcount, 0)
                # lightcurve's link to ignition is the server itself now.
                self.db.execute("DELETE FROM kv WHERE key IN ('ignition_url', 'demo_ignition')")
            finally:
                self.db.commit()
                self.db.execute("DETACH DATABASE lc")
        tmp.unlink(missing_ok=True)
        self.cache.drop()
        return n

    # -- ignition, from the inside --

    def burns(self):
        return self.ig.burns()["burns"]

    def block(self):
        return self.ig.settings()[0]["block"]

    def live(self):
        st = self.ig.ignite_state()
        if st.get("phase") in ("burning", "countdown") and st.get("session"):
            s = st["session"]
            return {"phase": st["phase"], "starts_at": s.get("starts_at"), "ends_at": s.get("ends_at")}
        return None

    def status(self):
        return {"url": "", "connected": True, "error": None, "live": self.live(), "demo": False}

    def add_block(self, body):
        kind, value = body.get("list"), str(body.get("value") or "").strip()
        if kind not in ("sites", "apps", "programs"):
            raise ValueError("list must be sites, apps or programs")
        if not value:
            raise ValueError("Nothing to block.")
        settings = self.ig.settings()[0]
        current = settings["block"].setdefault(kind, [])
        if kind == "apps":
            if not any(a.get("id") == value for a in current):
                current.append({"id": value, "label": str(body.get("label") or value)[:80]})
        elif value not in current:
            current.append(value)
        saved = self.ig.put_settings(settings)["block"]
        kept = saved.get(kind, [])
        ok = any(a.get("id") == value for a in kept) if kind == "apps" else value in kept
        if not ok:
            raise ValueError(f"“{value}” doesn't look right. Sites need an address like example.com, "
                             "programs a command name.")
        return {"ok": True, "block": saved}

    # -- reads --

    @staticmethod
    def _date(q, key="date"):
        v = (q.get(key) or [""])[0]
        return v if re.match(r"^\d{4}-\d{2}-\d{2}$", v) else core.today()

    @staticmethod
    def _device(q):
        v = (q.get("device") or [""])[0]
        return v if re.match(r"^[A-Za-z0-9._:-]{3,64}$", v) else None

    @staticmethod
    def _days(q, default):
        try:
            return max(1, min(366, int((q.get("days") or [str(default)])[0])))
        except ValueError:
            return default

    def first_day(self):
        row = self.db.execute("SELECT MIN(start) FROM spans").fetchone()
        legacy = self.db.execute("SELECT MIN(date) FROM legacy").fetchone()
        first = core.day_of(row[0] / 1000) if row and row[0] else None
        return {"spans": first, "legacy": legacy[0] if legacy else None}

    def day(self, q):
        burns, test = self.burns(), link.blocked_test(self.block())
        with self.lock:
            r = core.day_report(self.db, self._date(q), self.cache, burns=burns, blocked=test, device=self._device(q))
            r["first_day"] = self.first_day()
        r["ignition"] = self.status()
        return r

    def range(self, q):
        burns, test = self.burns(), link.blocked_test(self.block())
        with self.lock:
            r = core.range_report(self.db, self._date(q, "end"), self._days(q, 28), self.cache, burns=burns,
                                  blocked=test, device=self._device(q))
        for e in r["candidates"]:
            for p in e["parts"]:
                p["list"], p["value"] = link.suggest(p)
        r["ignition"]["status"] = self.status()
        return r

    def devices(self):
        now = time.time()
        with self.lock:
            devs = core.devices(self.db)
            counts = dict(self.db.execute("SELECT device, COUNT(*) FROM spans GROUP BY device"))
            legacy = dict(self.db.execute("SELECT device, COUNT(DISTINCT date) FROM legacy GROUP BY device"))
        for d in devs:
            d["spans"] = counts.get(d["id"], 0)
            d["legacy_days"] = legacy.get(d["id"], 0)
            d["silent_for"] = round(now - d["last_seen"] / 1000)
        return {"devices": devs, "now": now, "version": core.VERSION, "ignition": self.status(),
                "demo": False, "day_start_hour": core.DAY_START_HOUR}

    def summary(self, q):
        """A small account of a day and the days before it, for the terminal and phone widgets."""
        date, burns = self._date(q), self.burns()
        with self.lock:
            day = core.day_report(self.db, date, self.cache, burns=burns)
            week = core.range_report(self.db, date, self._days(q, 7), self.cache, burns=burns)
        return {
            "date": date, "lit": day["lit"], "summed": day["summed"], "devices": day["devices"],
            "categories": day["categories"], "first": day["first"], "last": day["last"],
            "entities": [{k: e[k] for k in ("label", "cat", "seconds", "sessions", "devices")}
                         for e in day["entities"][:40]],
            "curve": [round(c["lit"], 2) if c else 0 for c in day["curve"]],
            "curve_cat": "".join(c["cat"][0] if c else "." for c in day["curve"]),
            "attention": {k: day["attention"][k] for k in ("switches", "pickups", "sittings")},
            "usual": {k: day["usual"].get(k) for k in ("lit", "lit_by_now", "enough", "days")},
            "burns": [{k: b[k] for k in ("start", "end", "minutes", "live")} for b in day["burns"]],
            "notes": day["notes"], "in_progress": day["in_progress"], "legacy": day["legacy"],
            "week": [{k: r[k] for k in ("date", "lit", "has_data", "legacy", "cats", "burn_seconds")}
                     for r in week["rows"]],
            "utc_offset": day["utc_offset"], "t0": day["t0"], "now": day["now"],
        }

    def things(self, q, kind):
        """Totals per app or per site over the last N days."""
        end, days = self._date(q, "end"), self._days(q, 7)
        with self.lock:
            r = core.range_report(self.db, end, days, self.cache)
        rows = [e for e in r["census"] if any(p["kind"] == kind for p in e["parts"])] if kind else r["census"]
        return {"days": days, "end": end, "lit": r["lit"], "rows": rows[:60]}

    def today(self):
        """Today's total, for the Ignite page."""
        with self.lock:
            day = core.DayData(self.db, core.today(), core.Namer(self.db))
            return {"date": day.date, "lit": day.lit_seconds}

    # -- writes --

    def ingest(self, body):
        with self.lock:
            result, touched = core.ingest(self.db, body)
            self.cache.drop(touched)
        self._enforce = (0, None)
        return result

    def set_category(self, body):
        key, cat = str(body.get("key") or ""), body.get("cat")
        if not re.match(r"^n:.{1,200}$", key):
            raise ValueError("key must look like n:<name>")
        with self.lock:
            out = limits.set_category(self.db, key, cat)
            self.cache.drop()
        self._enforce = (0, None)
        return out

    def patch_device(self, dev_id, body):
        with self.lock, self.db:
            if "name" in body:
                name = str(body["name"] or "").strip()[:60]
                if name:
                    self.db.execute("UPDATE devices SET name = ?, renamed = 1 WHERE id = ?", (name, dev_id))
            if body.get("kind") in ("desktop", "laptop", "phone", "tablet"):
                self.db.execute("UPDATE devices SET kind = ? WHERE id = ?", (body["kind"], dev_id))
            self.cache.drop()
        return self.devices()

    # -- limits --

    def limit_report(self, q):
        device = self._device(q)
        with self.lock:
            if device and (q.get("app") or [""])[0] == "ignition":
                with self.db:
                    core.kv_set(self.db, "ignition_app:" + device, int(time.time()))
            elif device and core.kv_get(self.db, "ignition_app:" + device):
                # The old lightcurve app on a phone that has ignition now: ignition keeps the limits there,
                # so the old one stands down (and lets itself be uninstalled).
                return {"limits": [], "settings": {**limits.settings(self.db), "guard": False},
                        "now": int(time.time() * 1000), "settings_pending": None, "settings_at": None,
                        "day_start_hour": core.DAY_START_HOUR}
            r = limits.report(self.db, device=device)
        r["burn"] = self.live()
        return r

    def limits_changed(self):
        self._enforce = (0, None)

    def enforce(self):
        """What computers block for the limits right now (cached for a few seconds)."""
        at, value = self._enforce
        if value is not None and time.monotonic() - at < ENFORCE_TTL:
            return value
        with self.lock:
            value = limits.enforce(self.db)
        self._enforce = (time.monotonic(), value)
        return value

    def route(self, method, r, q, body):
        """Answers the screen-time and limit endpoints, or returns None if `r` isn't one of them."""
        with_lock = self.lock
        with self.lock:
            if limits.settle(self.db):  # a waiting change of kind came due: the reports count it now
                self.cache.drop()
        if method == "GET":
            if r == ["day"]:
                return self.day(q)
            if r == ["range"]:
                return self.range(q)
            if r == ["devices"]:
                return self.devices()
            if r == ["summary"]:
                return self.summary(q)
            if r in (["apps"], ["sites"], ["things"]):
                return self.things(q, {"apps": "app", "sites": "site"}.get(r[0]))
            if r == ["screen", "today"]:
                return self.today()
            if r == ["limits"]:
                return self.limit_report(q)
            if r == ["limits", "enforce"]:
                return self.enforce()
            return None
        if method == "POST":
            if r == ["ingest"]:
                return self.ingest(body())
            if r == ["ignition", "block"]:
                return self.add_block(body())
            if len(r) == 3 and r[0] == "limits" and r[2] == "cancel":
                with with_lock:
                    out = limits.cancel(self.db, r[1])
                self.limits_changed()
                return out
            if len(r) == 3 and r[0] == "limits" and r[2] == "unlock":
                b = body()
                step = b.get("step")
                live = self.live()
                if step in ("start", "open") and live and live["phase"] == "burning":
                    raise ValueError("No unlocks during a burn.")
                with with_lock:
                    if step == "start":
                        out = limits.start_unlock(self.db, r[1], b.get("device") or "")
                    elif step == "open":
                        out = limits.open_unlock(self.db, str(b.get("id") or ""))
                    elif step == "cancel":
                        out = limits.cancel_unlock(self.db, str(b.get("id") or ""))
                    else:
                        raise ValueError("step must be start, open or cancel")
                self.limits_changed()
                return out
            return None
        if method == "PUT":
            if r == ["category"]:
                return self.set_category(body())
            if r == ["limits", "settings"]:
                b = body()
                with with_lock:
                    if b.get("cancel"):
                        out = limits.cancel_settings(self.db)
                    elif b.get("dry"):
                        out = limits.settings_would_wait(self.db, b)
                    else:
                        out = limits.save_settings(self.db, b)
                self.limits_changed()
                return out
            if r == ["limits"]:
                b = body()
                with with_lock:
                    out = limits.would_wait(self.db, b) if b.get("dry") else limits.save(self.db, b)
                self.limits_changed()
                return out
            return None
        if method == "DELETE":
            if len(r) == 2 and r[0] == "limits":
                with with_lock:
                    out = limits.delete(self.db, r[1])
                self.limits_changed()
                return out
            return None
        if method == "PATCH":
            if len(r) == 2 and r[0] == "devices":
                return self.patch_device(r[1], body())
        return None

    def checkpoint(self):
        """Fold the write-ahead log into the main file, so a plain copy of it is a whole backup."""
        with self.lock:
            self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def default_lightcurve_db():
    return Path(core.data_dir("lightcurve")) / "hub.db"

