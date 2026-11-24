import datetime as dt
import json
import os
import tempfile
import unittest

from tests import _path  # noqa: F401
from screen import core, limits


def at(date, hh, mm=0, ss=0):
    d = dt.date.fromisoformat(date)
    return dt.datetime(d.year, d.month, d.day, hh, mm, ss).timestamp()


# 2026-03-09 is a Monday.
MON = "2026-03-09"
M = 60


def lim(*rules, unlock=limits.DEFAULT_UNLOCK, targets=("n:youtube",)):
    return limits.clean({"targets": list(targets), "rules": list(rules), "unlock": unlock})


class Rules(unittest.TestCase):
    def test_day_allowance(self):
        l = lim({"kind": "day", "minutes": 60})
        use = [[at(MON, 9), at(MON, 9, 40)]]
        s = limits.evaluate(l, use, at(MON, 12))
        self.assertFalse(s["blocked"])
        self.assertEqual(s["left"], 20 * M)
        use.append([at(MON, 13), at(MON, 13, 20)])
        s = limits.evaluate(l, use, at(MON, 13, 20))
        self.assertTrue(s["blocked"])
        self.assertEqual(s["until"], at("2026-03-10", 4))

    def test_day_counts_from_four(self):
        l = lim({"kind": "day", "minutes": 30})
        # 03:00 on Tuesday still belongs to Monday
        use = [[at(MON, 23), at("2026-03-10", 3)]]
        self.assertTrue(limits.evaluate(l, use, at("2026-03-10", 3))["blocked"])
        self.assertFalse(limits.evaluate(l, use, at("2026-03-10", 5))["blocked"])

    def test_day_only_on_its_days(self):
        l = lim({"kind": "day", "minutes": 0, "days": [5, 6]})
        self.assertFalse(limits.evaluate(l, [], at(MON, 12))["blocked"])
        self.assertTrue(limits.evaluate(l, [], at("2026-03-14", 12))["blocked"])

    def test_rolling_window(self):
        l = lim({"kind": "every", "minutes": 30, "hours": 4})
        use = [[at(MON, 8), at(MON, 8, 20)], [at(MON, 10), at(MON, 10, 10)]]
        s = limits.evaluate(l, use, at(MON, 10, 10))
        self.assertTrue(s["blocked"])
        # five minutes have to slide out: 08:05 + 4h
        self.assertEqual(s["until"], at(MON, 12, 5))
        self.assertFalse(limits.evaluate(l, use, at(MON, 12, 6))["blocked"])

    def test_closed_hours_across_midnight(self):
        l = lim({"kind": "block", "from": "22:00", "to": "07:00"})
        s = limits.evaluate(l, [], at(MON, 23))
        self.assertTrue(s["blocked"])
        self.assertEqual(s["until"], at("2026-03-10", 7))
        self.assertTrue(limits.evaluate(l, [], at("2026-03-10", 6, 59))["blocked"])
        s = limits.evaluate(l, [], at(MON, 21))
        self.assertFalse(s["blocked"])
        self.assertEqual(s["left"], 3600)

    def test_closed_belongs_to_the_day_it_starts(self):
        l = lim({"kind": "block", "from": "22:00", "to": "07:00", "days": [4]})  # Friday nights
        self.assertTrue(limits.evaluate(l, [], at("2026-03-14", 2))["blocked"])  # early Saturday
        self.assertFalse(limits.evaluate(l, [], at("2026-03-15", 2))["blocked"])  # early Sunday

    def test_tightest_rule_wins(self):
        l = lim({"kind": "day", "minutes": 120}, {"kind": "block", "from": "09:30", "to": "10:00"})
        use = [[at(MON, 9), at(MON, 9, 10)]]
        self.assertEqual(limits.evaluate(l, use, at(MON, 9, 10))["left"], 20 * M)

    def test_burn_rule_only_closes_during_a_burn(self):
        l = lim({"kind": "burn"})
        self.assertFalse(limits.evaluate(l, [], at(MON, 12))["blocked"])
        countdown = {"phase": "countdown", "starts_at": at(MON, 13), "ends_at": at(MON, 14)}
        self.assertFalse(limits.evaluate(l, [], at(MON, 12), countdown)["blocked"])
        burning = {"phase": "burning", "starts_at": at(MON, 11), "ends_at": at(MON, 14)}
        s = limits.evaluate(l, [], at(MON, 12), burning)
        self.assertTrue(s["blocked"])
        self.assertEqual(s["until"], at(MON, 14))

    def test_bad_input(self):
        for bad in ({"targets": [], "rules": [{"kind": "day", "minutes": 5}]},
                    {"targets": ["n:x"], "rules": []},
                    {"targets": ["n:x"], "rules": [{"kind": "block", "from": "25:00", "to": "07:00"}]},
                    {"targets": ["n:x"], "rules": [{"kind": "stretch", "minutes": 20, "rest": 30}]},
                    {"targets": ["c:nope"], "rules": [{"kind": "day", "minutes": 5}]}):
            with self.assertRaises(ValueError):
                limits.clean(bad)


class Loosening(unittest.TestCase):
    def test_tighter_is_not_loosening(self):
        old = lim({"kind": "day", "minutes": 60})
        new = dict(old, rules=[{"kind": "day", "minutes": 30, "days": limits.ALL_DAYS},
                               {"kind": "block", "from": "22:00", "to": "07:00", "days": limits.ALL_DAYS}])
        self.assertFalse(limits.loosens(old, new))

    def test_more_minutes_loosens(self):
        old = lim({"kind": "day", "minutes": 60})
        self.assertTrue(limits.loosens(old, dict(old, rules=[{"kind": "day", "minutes": 61, "days": limits.ALL_DAYS}])))

    def test_fewer_targets_or_easier_unlock_loosens(self):
        old = lim({"kind": "day", "minutes": 60}, targets=("n:youtube", "n:reddit"))
        self.assertTrue(limits.loosens(old, dict(old, targets=["n:youtube"])))
        self.assertTrue(limits.loosens(old, dict(old, unlock=dict(old["unlock"], wait=1))))
        strict = dict(old, unlock=None)
        self.assertTrue(limits.loosens(strict, old))
        self.assertFalse(limits.loosens(old, strict))

    def test_closed_hours_must_still_be_covered(self):
        old = lim({"kind": "block", "from": "22:00", "to": "07:00"})
        wider = dict(old, rules=[{"kind": "block", "from": "21:00", "to": "08:00", "days": limits.ALL_DAYS}])
        narrower = dict(old, rules=[{"kind": "block", "from": "23:00", "to": "07:00", "days": limits.ALL_DAYS}])
        self.assertFalse(limits.loosens(old, wider))
        self.assertTrue(limits.loosens(old, narrower))

    def test_identical_burn_rules_do_not_loosen(self):
        old = lim({"kind": "burn"})
        self.assertFalse(limits.loosens(old, lim({"kind": "burn"})))
        self.assertTrue(limits.loosens(old, lim({"kind": "block", "from": "22:00", "to": "07:00"})))

    def test_deleting_loosens(self):
        self.assertTrue(limits.loosens(lim({"kind": "day", "minutes": 60}), None))


class Storage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = core.open_db(os.path.join(self.tmp.name, "t.db"))

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_loosening_waits_and_tightening_does_not(self):
        t = at(MON, 9)
        made = limits.save(self.db, {"targets": ["n:youtube"], "rules": [{"kind": "day", "minutes": 60}]}, now=t)
        lid = made["limit"]["id"]
        later = t + 3600
        looser = limits.save(self.db, {"id": lid, "targets": ["n:youtube"], "rules": [{"kind": "day", "minutes": 90}]},
                             now=later)
        self.assertFalse(looser["applied"])
        self.assertEqual(looser["at"], int((later + 15 * 60) * 1000))
        row = limits.rows(self.db, later + 60)[0]
        self.assertEqual(row["limit"]["rules"][0]["minutes"], 60)
        self.assertEqual(row["pending"]["rules"][0]["minutes"], 90)
        row = limits.rows(self.db, later + 16 * 60)[0]
        self.assertEqual(row["limit"]["rules"][0]["minutes"], 90)
        self.assertIsNone(row["pending"])
        tighter = limits.save(self.db, {"id": lid, "targets": ["n:youtube"], "rules": [{"kind": "day", "minutes": 10}]},
                              now=later + 20 * 60)
        self.assertTrue(tighter["applied"])

    def test_a_new_limit_can_be_fixed_at_once(self):
        t = at(MON, 9)
        lid = limits.save(self.db, {"targets": ["n:x"], "rules": [{"kind": "day", "minutes": 5}]}, now=t)["limit"]["id"]
        self.assertTrue(limits.delete(self.db, lid, now=t + 60)["applied"])
        self.assertEqual(limits.rows(self.db, t + 61), [])

    def test_delete_waits_and_can_be_cancelled(self):
        t = at(MON, 9)
        lid = limits.save(self.db, {"targets": ["n:x"], "rules": [{"kind": "day", "minutes": 5}]}, now=t)["limit"]["id"]
        self.assertFalse(limits.delete(self.db, lid, now=t + 3600)["applied"])
        self.assertTrue(limits.rows(self.db, t + 3660)[0]["deleting"])
        limits.cancel(self.db, lid)
        self.assertEqual(len(limits.rows(self.db, t + 7200)), 1)

    def test_pending_burn_loosening_waits_for_the_burn_to_end(self):
        t = at(MON, 9)
        made = limits.save(self.db, {"targets": ["s:youtube.com", "s:reddit.com"], "rules": [{"kind": "burn"}]}, now=t)
        lid = made["limit"]["id"]
        later = t + 3600
        looser = limits.save(self.db, {"id": lid, "targets": ["s:youtube.com"], "rules": [{"kind": "burn"}]}, now=later)
        self.assertFalse(looser["applied"])
        live = {"phase": "burning", "starts_at": later, "ends_at": later + 30 * 60}
        row = limits.report(self.db, now=later + 20 * 60, live=live)["limits"][0]
        self.assertEqual(len(row["limit"]["targets"]), 2)
        self.assertEqual(row["pending_at"], int((later + 30 * 60) * 1000))
        # Even an unrelated dry run must not settle the deferred change without live burn state.
        limits.would_wait(self.db, {"targets": ["s:example.com"], "rules": [{"kind": "day", "minutes": 5}]},
                          now=later + 20 * 60, live=live)
        self.assertEqual(len(limits.rows(self.db, later + 20 * 60, live)[0]["limit"]["targets"]), 2)
        row = limits.report(self.db, now=later + 31 * 60)["limits"][0]
        self.assertEqual(row["limit"]["targets"], ["s:youtube.com"])

    def test_burn_builtin_category_change_without_spans_waits(self):
        t = at(MON, 9)
        core.kv_set(self.db, "limit_settings", json.dumps({"changes_wait": 0, "guard": True}))
        limits.save(self.db, {"targets": ["c:watch"], "rules": [{"kind": "burn"}]}, now=t)
        live = {"phase": "burning", "starts_at": t - 60, "ends_at": t + 3600, "locked": True}
        r = limits.set_category(self.db, "n:youtube", "make", now=t, live=live)
        self.assertFalse(r["applied"])
        self.assertEqual(r["at"], int((t + 3600) * 1000))

    def test_direct_usage_does_not_leak_between_platforms(self):
        now = at(MON, 12)
        body = lambda dev, kind: {"device": {"id": dev, "name": dev, "kind": kind}, "spans": [
            [int(at(MON, 9) * 1000), int(at(MON, 10) * 1000), "org.videolan.vlc", "example.com"]]}
        core.ingest(self.db, body("phone-1234", "phone"), now=now)
        limit = limits.save(self.db, {"targets": ["p:vlc"], "rules": [{"kind": "day", "minutes": 30}]}, now=now)
        row = limits.report(self.db, now=now)["limits"][0]
        self.assertEqual(row["state"]["rules"][0]["used"], 0)

    def test_settings_shorter_wait_waits(self):
        t = at(MON, 9)
        r = limits.save_settings(self.db, {"changes_wait": 5}, now=t)
        self.assertFalse(r["applied"])
        self.assertEqual(limits.settings(self.db)["changes_wait"], 15)
        limits.settle(self.db, t + 15 * 60)
        self.assertEqual(limits.settings(self.db)["changes_wait"], 5)
        self.assertTrue(limits.save_settings(self.db, {"changes_wait": 30}, now=t + 3600)["applied"])

    def test_report_counts_every_device_and_tells_a_phone_what_to_match(self):
        now = at(MON, 12)
        body = lambda dev, kind, spans, names=None: {
            "device": {"id": dev, "name": dev, "kind": kind}, "names": names or {},
            "spans": [[int(a * 1000), int(b * 1000), app, site] for a, b, app, site in spans]}
        core.ingest(self.db, body("pc-1234", "desktop", [(at(MON, 9), at(MON, 9, 30), "firefox", "YouTube")]), now=now)
        core.ingest(self.db, body("phone-1234", "phone", [
            (at(MON, 9, 20), at(MON, 9, 50), "com.google.android.youtube", ""),
            (at(MON, 10), at(MON, 10, 5), "org.mozilla.firefox", "m.youtube.com"),
        ], {"com.google.android.youtube": {"label": "YouTube"}}), now=now)
        limits.save(self.db, {"targets": ["n:youtube"], "rules": [{"kind": "day", "minutes": 60}]}, now=now)
        r = limits.report(self.db, now=now, device="phone-1234")["limits"][0]
        # 09:00 to 09:50 with two screens overlapping, plus 10:00 to 10:05
        self.assertEqual(r["state"]["rules"][0]["used"], 55 * M)
        self.assertEqual(r["match"]["apps"], ["com.google.android.youtube"])
        self.assertEqual(r["match"]["sites"], ["m.youtube.com"])
        self.assertEqual(r["others"], [[int(at(MON, 9) * 1000), int(at(MON, 9, 30) * 1000)]])
        json.dumps(r)


class AllDay(unittest.TestCase):
    def test_same_time_both_ends_is_the_whole_day(self):
        l = lim({"kind": "block", "from": "00:00", "to": "00:00", "days": [0]})
        s = limits.evaluate(l, [], at(MON, 12))
        self.assertTrue(s["blocked"])
        self.assertEqual(s["until"], at("2026-03-10", 0))
        self.assertFalse(limits.evaluate(l, [], at("2026-03-10", 12))["blocked"])

    def test_days_in_a_row_are_one_closure(self):
        l = lim({"kind": "block", "from": "06:00", "to": "06:00", "days": [0, 1]})
        self.assertEqual(limits.evaluate(l, [], at(MON, 12))["until"], at("2026-03-11", 6))

    def test_every_day_all_day_never_opens(self):
        s = limits.evaluate(lim({"kind": "block", "from": "00:00", "to": "00:00"}), [], at(MON, 12))
        self.assertTrue(s["blocked"])
        self.assertIsNone(s["until"])

    def test_all_day_covers_any_closed_hours(self):
        old = lim({"kind": "block", "from": "01:00", "to": "23:00"})
        new = lim({"kind": "block", "from": "00:00", "to": "00:00"})
        self.assertFalse(limits.loosens(old, new))
        self.assertTrue(limits.loosens(new, old))


class Unlocks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = core.open_db(os.path.join(self.tmp.name, "t.db"))
        self.t = at(MON, 9)
        self.lid = limits.save(self.db, {"targets": ["c:play"], "rules": [{"kind": "block", "from": "00:00", "to": "00:00"}],
                                         "unlock": {"hold": 5, "wait": 2, "grant": 10, "per_day": 2}}, now=self.t)["limit"]["id"]

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def item(self, now):
        return limits.report(self.db, now=now)["limits"][0]

    def test_a_computer_has_to_wait_it_out(self):
        u = limits.start_unlock(self.db, self.lid, "pc", now=self.t)
        # The hold was done before asking; the server times the wait.
        self.assertEqual(u["ready"], int((self.t + 120) * 1000))
        with self.assertRaises(ValueError):
            limits.open_unlock(self.db, u["id"], now=self.t + 60)
        got = limits.open_unlock(self.db, u["id"], now=self.t + 125)
        self.assertEqual(got["until"], int((self.t + 125 + 600) * 1000))
        st = self.item(self.t + 200)["unlock"]
        self.assertEqual((st["used"], st["left"]), (1, 1))
        self.assertEqual(st["open_until"], got["until"])
        self.assertEqual(limits.enforce(self.db, now=self.t + 200)["limits"], [])
        self.assertTrue(limits.enforce(self.db, now=self.t + 800)["games"])

    def test_unlocks_are_counted_across_devices(self):
        for dev, t in (("phone", self.t), ("pc", self.t + 700)):
            u = limits.start_unlock(self.db, self.lid, dev, now=t)
            limits.open_unlock(self.db, u["id"], now=t + 120)
        self.assertEqual(self.item(self.t + 1000)["unlock"]["left"], 0)
        with self.assertRaises(ValueError):
            limits.start_unlock(self.db, self.lid, "pc", now=self.t + 2000)
        # the next day starts at 04:00
        self.assertEqual(self.item(at("2026-03-10", 5))["unlock"]["left"], 2)

    def test_burn_limits_never_unlock(self):
        lid = limits.save(self.db, {"targets": ["n:x"], "rules": [{"kind": "burn"}],
                                    "unlock": {"hold": 1, "wait": 0, "grant": 10}}, now=self.t)["limit"]["id"]
        row = next(r for r in limits.rows(self.db, self.t) if r["limit"]["id"] == lid)
        self.assertIsNone(row["limit"]["unlock"])
        with self.assertRaisesRegex(ValueError, "can't be unlocked"):
            limits.start_unlock(self.db, lid, "pc", now=self.t)

    def test_no_unlocking_means_none(self):
        lid = limits.save(self.db, {"targets": ["n:x"], "rules": [{"kind": "day", "minutes": 0}], "unlock": None},
                          now=self.t)["limit"]["id"]
        with self.assertRaises(ValueError):
            limits.start_unlock(self.db, lid, now=self.t)


class Enforce(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = core.open_db(os.path.join(self.tmp.name, "t.db"))

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def send(self, dev, kind, spans, now):
        core.ingest(self.db, {"device": {"id": dev, "name": dev, "kind": kind},
                              "spans": [[int(a * 1000), int(b * 1000), app, site] for a, b, app, site in spans]}, now=now)

    def test_closed_limits_become_sites_and_programs(self):
        now = at(MON, 12)
        self.send("pc-1234", "desktop", [(at(MON, 9), at(MON, 9, 30), "steam_app_367520", ""),
                                         (at(MON, 9, 30), at(MON, 10), "net.lutris.Lutris", ""),
                                         (at(MON, 10), at(MON, 10, 30), "zen", "YouTube"),
                                         (at(MON, 10, 30), at(MON, 11), "kitty", "")], now)
        self.send("phone-1234", "phone", [(at(MON, 9), at(MON, 9, 1), "org.mozilla.firefox", "m.reddit.com")], now)
        limits.save(self.db, {"targets": ["c:play"], "rules": [{"kind": "block", "from": "00:00", "to": "00:00"}]}, now=now)
        limits.save(self.db, {"targets": ["n:youtube", "n:reddit"], "rules": [{"kind": "day", "minutes": 20}]}, now=now)
        out = limits.enforce(self.db, now=now)
        self.assertTrue(out["games"])
        self.assertIn("steam", out["programs"])
        self.assertIn("lutris", out["programs"])
        self.assertNotIn("kitty", out["programs"])
        self.assertNotIn("zen", out["programs"])
        self.assertIn("youtube.com", out["sites"])
        self.assertIn("reddit.com", out["sites"])
        self.assertEqual(len(out["limits"]), 2)

    def test_direct_burn_targets_enforce_exactly(self):
        now = at(MON, 12)
        self.send("pc-1234", "desktop", [(at(MON, 9), at(MON, 10), "com.termux", "")], now)
        self.send("phone-1234", "phone", [(at(MON, 9), at(MON, 10), "org.videolan.vlc", "")], now)
        limits.save(self.db, {"targets": ["s:youtube.com", "a:com.termux", "p:vlc", "g:phone"],
                              "rules": [{"kind": "burn"}]}, now=now)
        live = {"phase": "burning", "starts_at": at(MON, 11), "ends_at": at(MON, 14)}
        out = limits.enforce(self.db, now=now, live=live)
        self.assertEqual(out["sites"], ["youtube.com"])
        self.assertEqual(out["programs"], ["vlc"])
        self.assertFalse(out["games"])
        self.assertTrue(out["limits"][0]["burn"])
        countdown = {"phase": "countdown", "starts_at": at(MON, 13), "ends_at": at(MON, 14)}
        cached = limits.enforce(self.db, now=now, live=countdown)["limits"][0]
        self.assertTrue(cached["burn"])
        self.assertEqual(cached["starts_at"], at(MON, 13))
        self.assertEqual(cached["until"], at(MON, 14))
        report = limits.report(self.db, now=now, device="phone-1234", live=live)["limits"][0]
        self.assertEqual(report["match"]["apps"], ["com.termux"])
        self.assertEqual(report["match"]["sites"], ["youtube.com"])
        self.assertEqual(limits.enforce(self.db, now=now), {"sites": [], "programs": [], "games": False, "limits": []})
        limits.save(self.db, {"targets": ["g:computer"], "rules": [{"kind": "burn"}]}, now=now)
        self.assertTrue(limits.enforce(self.db, now=now, live=live)["games"])

    def test_direct_sites_cover_subdomains_and_ports(self):
        l = lim({"kind": "burn"}, targets=("s:example.com", "s:nas.local:8000"))
        namer = core.Namer(self.db)
        self.assertTrue(limits.matches(l, namer, "", "m.example.com"))
        self.assertFalse(limits.matches(l, namer, "", "notexample.com"))
        self.assertTrue(limits.matches(l, namer, "", "nas.local:8000"))
        self.assertFalse(limits.matches(l, namer, "", "nas.local:9000"))

    def test_mixed_burn_and_timed_rules_keep_their_own_closures(self):
        now = at(MON, 12)
        limits.save(self.db, {"targets": ["s:example.com"],
                              "rules": [{"kind": "burn"}, {"kind": "block", "from": "00:00", "to": "00:00"}]}, now=now)
        ordinary = limits.enforce(self.db, now=now)["limits"]
        self.assertEqual(len(ordinary), 1)
        self.assertFalse(ordinary[0]["burn"])
        countdown = {"phase": "countdown", "starts_at": at(MON, 13), "ends_at": at(MON, 14)}
        both = limits.enforce(self.db, now=now, live=countdown)["limits"]
        self.assertEqual(len(both), 2)
        self.assertEqual({x["burn"] for x in both}, {False, True})

    def test_nothing_closed_blocks_nothing(self):
        now = at(MON, 12)
        limits.save(self.db, {"targets": ["n:youtube"], "rules": [{"kind": "day", "minutes": 20}]}, now=now)
        self.assertEqual(limits.enforce(self.db, now=now), {"sites": [], "programs": [], "games": False, "limits": []})


if __name__ == "__main__":
    unittest.main()


class Kinds(unittest.TestCase):
    """Moving a thing out of a kind a limit covers is a loosening, so it waits."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = core.open_db(os.path.join(self.tmp.name, "t.db"))
        self.t = at(MON, 12)
        core.ingest(self.db, {"device": {"id": "pc-1234", "name": "pc", "kind": "desktop", "platform": "Plasma"},
                              "spans": [[int(at(MON, 9) * 1000), int(at(MON, 11) * 1000), "zen", "youtube.com"]]}, now=self.t)
        limits.save(self.db, {"targets": ["c:watch"], "rules": [{"kind": "day", "minutes": 60}]}, now=self.t)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def closed(self, now):
        return limits.report(self.db, now=now)["limits"][0]["state"]["blocked"]

    def test_burn_category_change_waits_even_with_zero_wait(self):
        core.kv_set(self.db, "limit_settings", json.dumps({"changes_wait": 0, "guard": True}))
        limits.save(self.db, {"targets": ["c:watch"], "rules": [{"kind": "burn"}]}, now=self.t)
        live = {"phase": "burning", "starts_at": self.t - 60, "ends_at": self.t + 3600, "locked": True}
        r = limits.set_category(self.db, "n:youtube", "make", now=self.t, live=live)
        self.assertFalse(r["applied"])
        self.assertEqual(r["at"], int((self.t + 3600) * 1000))
        self.assertEqual(core.Namer(self.db).category("zen", "youtube.com"), "watch")

    def test_out_of_a_limited_kind_waits(self):
        self.assertTrue(self.closed(self.t))
        r = limits.set_category(self.db, "n:youtube", "make", now=self.t)
        self.assertFalse(r["applied"])
        self.assertTrue(self.closed(self.t + 60))
        self.assertIn("youtube.com", limits.enforce(self.db, now=self.t + 60)["sites"])
        self.assertTrue(limits.settle(self.db, now=self.t + 15 * 60 + 1))
        self.assertFalse(self.closed(self.t + 15 * 60 + 2))

    def test_default_category_used_by_a_limit_cannot_be_deleted(self):
        rows = core.category_list(self.db)
        with self.assertRaises(ValueError):
            core.save_categories(self.db, [c for c in rows if c["id"] != "watch"])

    def test_into_a_limited_kind_is_at_once(self):
        limits.save(self.db, {"targets": ["c:play"], "rules": [{"kind": "day", "minutes": 0}]}, now=self.t)
        self.assertTrue(limits.set_category(self.db, "n:youtube", "play", now=self.t)["applied"] is False)
        r = limits.set_category(self.db, "n:kitty", "play", now=self.t)
        self.assertTrue(r["applied"])

    def test_custom_category_can_be_limited_but_not_deleted_from_a_limit(self):
        defs = core.save_categories(self.db, [*core.category_list(self.db),
                                              {"id": "", "name": "Drift", "color": "#ff0000"}])
        custom = next(c for c in defs if c["name"] == "Drift")
        self.assertTrue(limits.set_category(self.db, "n:kitty", custom["id"], now=self.t)["applied"])
        saved = limits.save(self.db, {"targets": ["c:" + custom["id"]],
                                      "rules": [{"kind": "day", "minutes": 0}]}, now=self.t)
        self.assertEqual(saved["limit"]["targets"], ["c:" + custom["id"]])
        report = limits.report(self.db, now=self.t)
        item = next(i for i in report["limits"] if i["limit"]["id"] == saved["limit"]["id"])
        self.assertTrue(item["state"]["blocked"])
        self.assertEqual(item["label"], "Drift")
        with self.assertRaises(ValueError):
            core.save_categories(self.db, [c for c in defs if c["id"] != custom["id"]])
        self.assertTrue(limits.delete(self.db, saved["limit"]["id"], now=self.t)["applied"])
        core.save_categories(self.db, [c for c in defs if c["id"] != custom["id"]])
        self.assertEqual(core.Namer(self.db).category("kitty", ""), "make")

    def test_calling_a_computer_a_phone_keeps_its_programs(self):
        core.ingest(self.db, {"device": {"id": "pc-1234", "name": "pc", "kind": "desktop", "platform": "Plasma"},
                              "spans": [[int(at(MON, 11) * 1000), int(at(MON, 11, 30) * 1000), "steam", ""]]}, now=self.t)
        limits.save(self.db, {"targets": ["n:steam"], "rules": [{"kind": "block", "from": "00:00", "to": "00:00"}]}, now=self.t)
        self.db.execute("UPDATE devices SET kind = 'phone' WHERE id = 'pc-1234'")
        self.assertIn("steam", limits.enforce(self.db, now=self.t)["programs"])
