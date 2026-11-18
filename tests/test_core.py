import datetime as dt
import os
import tempfile
import unittest

from tests import _path  # noqa: F401
from screen import core


def at(date, hh, mm=0, ss=0):
    """Unix seconds for a wall-clock time on a calendar date."""
    d = dt.date.fromisoformat(date)
    return dt.datetime(d.year, d.month, d.day, hh, mm, ss).timestamp()


def ms(t):
    return int(t * 1000)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = core.open_db(os.path.join(self.tmp.name, "t.db"))

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def send(self, device, spans, kind="desktop", now=None, **extra):
        body = {"device": {"id": device, "name": device, "kind": kind},
                "spans": [[ms(a), ms(b), app, site] for a, b, app, site in spans], **extra}
        return core.ingest(self.db, body, now=now)


class Intervals(unittest.TestCase):
    def test_merge_joins_overlaps_and_touching(self):
        self.assertEqual([tuple(i) for i in core.merge([(5, 8), (0, 2), (2, 3), (7, 10)])], [(0, 3), (5, 10)])

    def test_merge_with_gap(self):
        self.assertEqual([tuple(i) for i in core.merge([(0, 10), (14, 20), (40, 50)], gap=5)], [(0, 20), (40, 50)])

    def test_intersect(self):
        self.assertEqual([tuple(i) for i in core.intersect([(0, 10), (20, 30)], [(5, 25)])], [(5, 10), (20, 25)])
        self.assertEqual(core.intersect([(0, 10)], [(10, 20)]), [])

    def test_total_and_clip(self):
        self.assertEqual(core.total(core.clip([(0, 10), (20, 30)], 5, 22)), 7)


class Days(unittest.TestCase):
    def test_late_night_belongs_to_the_day_before(self):
        self.assertEqual(core.day_of(at("2026-03-10", 2, 30)), "2026-03-09")
        self.assertEqual(core.day_of(at("2026-03-10", 4, 0)), "2026-03-10")

    def test_bounds_run_four_to_four(self):
        a, b = core.day_bounds("2026-03-10")
        self.assertEqual(a, at("2026-03-10", 4))
        self.assertEqual(b, at("2026-03-11", 4))


class Titles(unittest.TestCase):
    def test_site_is_the_last_piece_before_the_browser(self):
        self.assertEqual(core.site_from_caption("Some video - YouTube — Zen Browser"), "YouTube")
        self.assertEqual(core.site_from_caption("r/linux - Reddit — Mozilla Firefox"), "Reddit")

    def test_counters_are_dropped(self):
        self.assertEqual(core.site_from_caption("(3) YouTube — Zen Browser"), "YouTube")

    def test_a_title_that_names_no_site_gives_none(self):
        self.assertEqual(core.site_from_caption("Zen Browser"), "")
        self.assertEqual(core.site_from_caption(
            "20/50/100pcs 6x6 PCB Momentary Mini Micro Tactile Push Button 4-Pin Switch — Zen Browser"), "")

    def test_a_known_site_wins_wherever_it_sits(self):
        self.assertEqual(core.site_from_caption("Reddit - The heart of the internet — Zen Browser"), "Reddit")
        self.assertEqual(core.site_from_caption("Inbox (3) - someone@example.com - Gmail — Mozilla Firefox"), "Gmail")

    def test_an_unknown_short_name_is_kept(self):
        self.assertEqual(core.site_from_caption("Best tents of the year | Outdoor Gear Lab — Zen Browser"),
                         "Outdoor Gear Lab")


class Domains(Base):
    """Phones read the address bar and send a domain; it has to land on the same thing as a window title's name."""

    def test_a_domain_goes_by_its_sites_name(self):
        self.assertEqual(core.site_from_host("m.youtube.com"), "YouTube")
        self.assertEqual(core.site_from_host("old.reddit.com"), "Reddit")
        self.assertEqual(core.site_from_host("en.m.wikipedia.org"), "Wikipedia")
        self.assertEqual(core.site_from_host("news.ycombinator.com"), "Hacker News")

    def test_the_most_specific_domain_wins(self):
        self.assertEqual(core.site_from_host("docs.google.com"), "Google Docs")
        self.assertEqual(core.site_from_host("www.google.com"), "Google")

    def test_an_unknown_domain_is_cut_to_what_youd_type(self):
        self.assertEqual(core.site_from_host("blog.example.com"), "example.com")
        self.assertEqual(core.site_from_host("news.example.co.uk"), "example.co.uk")
        self.assertEqual(core.site_from_host("someone.github.io"), "someone.github.io")
        self.assertEqual(core.site_from_host("lightcurve.tailnet-name.ts.net"), "lightcurve")
        self.assertEqual(core.site_from_host("not a domain"), "")

    def test_a_name_from_a_title_is_left_alone(self):
        self.assertEqual(core.site_name("Outdoor Gear Lab"), "Outdoor Gear Lab")
        self.assertEqual(core.site_name("r/linux"), "r/linux")

    def test_the_phones_domain_and_the_computers_title_are_one_thing(self):
        a = at("2026-03-10", 9)
        self.send("desk-1", [(a, a + 600, "zen", "YouTube")], now=a + 4000)
        self.send("phone-1", [(a + 1200, a + 1800, "org.mozilla.firefox", "m.youtube.com"),
                              (a + 1800, a + 2400, "org.mozilla.firefox", "monkeytype.com"),
                              (a + 2400, a + 3000, "org.mozilla.firefox", "")], kind="phone", now=a + 4000)
        r = core.day_report(self.db, "2026-03-10", now=a + 4000)
        things = {e["label"]: (e["seconds"], e["cat"]) for e in r["entities"]}
        self.assertEqual(things["YouTube"], (1200, "watch"))
        self.assertEqual(things["Monkeytype"], (600, "play"))
        self.assertEqual(things["Firefox"][1], "browse")


class Ingest(Base):
    def test_sending_twice_changes_nothing(self):
        a = at("2026-03-10", 9)
        spans = [(a, a + 600, "kitty", ""), (a + 600, a + 900, "zen", "YouTube")]
        self.send("desk-1", spans, now=a + 1000)
        self.send("desk-1", spans, now=a + 1000)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM spans").fetchone()[0], 2)
        r = core.day_report(self.db, "2026-03-10", now=a + 1000)
        self.assertEqual(r["lit"], 900)

    def test_an_open_span_grows_in_place(self):
        a = at("2026-03-10", 9)
        self.send("desk-1", [(a, a + 60, "kitty", "")], now=a + 60)
        self.send("desk-1", [(a, a + 300, "kitty", "")], now=a + 300)
        self.assertEqual(self.db.execute("SELECT start, end FROM spans").fetchall(), [(ms(a), ms(a + 300))])

    def test_rubbish_is_refused(self):
        a = at("2026-03-10", 9)
        result, _ = self.send("desk-1", [
            (a, a - 5, "backwards", ""), (a + 10_000_000, a + 10_000_100, "future", ""),
            (a, a + 200_000, "longer-than-a-day", ""), (a, a + 5, "", ""), (a, a + 30, "fine", ""),
        ], now=a + 100)
        self.assertEqual(result["spans"], 1)
        with self.assertRaises(ValueError):
            core.ingest(self.db, {"device": {"id": "x y"}})

    def test_a_dashboard_rename_survives_the_next_sync(self):
        a = at("2026-03-10", 9)
        self.send("desk-1", [], now=a)
        self.db.execute("UPDATE devices SET name = 'tycho', renamed = 1")
        self.send("desk-1", [], now=a + 60)
        self.assertEqual(self.db.execute("SELECT name FROM devices").fetchone()[0], "tycho")


class Reports(Base):
    def test_two_screens_at_once_count_once(self):
        a = at("2026-03-10", 9)
        self.send("desk-1", [(a, a + 3600, "kitty", "")], now=a + 7200)
        self.send("phone-1", [(a + 1800, a + 5400, "com.instagram.android", "")], kind="phone", now=a + 7200)
        r = core.day_report(self.db, "2026-03-10", now=a + 7200)
        self.assertEqual(r["lit"], 5400)      # 09:00 to 10:30, once
        self.assertEqual(r["summed"], 7200)   # an hour on each
        self.assertEqual(r["both"], 1800)

    def test_a_span_across_four_in_the_morning_is_split_between_days(self):
        a = at("2026-03-11", 3, 30)
        self.send("desk-1", [(a, a + 3600, "kitty", "")], now=a + 4000)
        self.assertEqual(core.day_report(self.db, "2026-03-10", now=a + 4000)["lit"], 1800)
        self.assertEqual(core.day_report(self.db, "2026-03-11", now=a + 4000)["lit"], 1800)

    def test_a_device_shows_one_thing_at_a_time(self):
        a = at("2026-03-10", 9)
        # the first span claims to run on past the start of the second
        self.send("desk-1", [(a, a + 900, "kitty", ""), (a + 600, a + 1200, "zen", "YouTube")], now=a + 2000)
        r = core.day_report(self.db, "2026-03-10", now=a + 2000)
        self.assertEqual(r["lit"], 1200)
        by = {e["label"]: e["seconds"] for e in r["entities"]}
        self.assertEqual(by, {"Kitty": 600, "YouTube": 600} if "Kitty" in by else {"kitty": 600, "YouTube": 600})

    def test_the_app_and_the_site_of_one_thing_are_one_thing(self):
        a = at("2026-03-10", 9)
        self.send("desk-1", [(a, a + 600, "zen", "YouTube")], now=a + 5000)
        self.send("phone-1", [(a + 1000, a + 1300, "com.google.android.youtube", "")], kind="phone", now=a + 5000,
                  names={"com.google.android.youtube": {"label": "YouTube", "hint": "watch"}})
        r = core.day_report(self.db, "2026-03-10", now=a + 5000)
        self.assertEqual(len(r["entities"]), 1)
        self.assertEqual(r["entities"][0]["seconds"], 900)
        self.assertEqual({p["kind"] for p in r["entities"][0]["parts"]}, {"app", "site"})

    def test_coming_back_within_the_gap_is_the_same_visit(self):
        a = at("2026-03-10", 9)
        self.send("desk-1", [(a, a + 100, "kitty", ""), (a + 120, a + 300, "kitty", ""),
                             (a + 1000, a + 1100, "kitty", "")], now=a + 2000)
        r = core.day_report(self.db, "2026-03-10", now=a + 2000)
        self.assertEqual(r["entities"][0]["sessions"], 2)

    def test_usual_is_not_claimed_without_enough_history(self):
        a = at("2026-03-10", 9)
        self.send("desk-1", [(a, a + 600, "kitty", "")], now=a + 700)
        self.assertFalse(core.day_report(self.db, "2026-03-10", now=a + 700)["usual"]["enough"])

    def test_usual_is_the_median_of_the_days_before(self):
        spans = []
        for i, minutes in enumerate((60, 60, 120, 120, 120, 600)):  # one wild day shouldn't move the middle
            a = at(core.shift("2026-03-01", i), 10)
            spans.append((a, a + minutes * 60, "kitty", ""))
        now = at("2026-03-07", 12)
        self.send("desk-1", spans + [(now - 600, now, "kitty", "")], now=now)
        usual = core.day_report(self.db, "2026-03-07", now=now)["usual"]
        self.assertTrue(usual["enough"])
        self.assertEqual(round(usual["lit"]), 120 * 60)

    def test_a_phone_that_has_not_synced_is_said_so(self):
        a = at("2026-03-10", 9)
        self.send("phone-1", [(a, a + 600, "com.whatsapp", "")], kind="phone", now=a + 700)
        r = core.day_report(self.db, "2026-03-10", now=a + 4 * 3600)
        self.assertEqual([n["device"] for n in r["notes"]], ["phone-1"])
        # a computer that was idle when it last reported is simply off
        self.send("desk-1", [(a, a + 600, "kitty", "")], now=a + 700, active=False)
        r = core.day_report(self.db, "2026-03-10", now=a + 4 * 3600)
        self.assertEqual([n["device"] for n in r["notes"]], ["phone-1"])

    def test_categories_follow_the_users_choice(self):
        a = at("2026-03-10", 9)
        self.send("desk-1", [(a, a + 600, "zen", "Wikipedia")], now=a + 700)
        namer = core.Namer(self.db)
        key = namer.key("zen", "Wikipedia")
        before = namer.category("zen", "Wikipedia")
        self.db.execute("INSERT INTO categories (key, cat) VALUES (?, 'make')", (key,))
        self.assertNotEqual(before, "make")
        self.assertEqual(core.Namer(self.db).category("zen", "Wikipedia"), "make")

    def test_range_rows_and_census(self):
        for i in range(5):
            a = at(core.shift("2026-03-01", i), 10)
            self.send("desk-1", [(a, a + 1800, "kitty", ""), (a + 1800, a + 2400, "zen", "Reddit")], now=a + 3000)
        r = core.range_report(self.db, "2026-03-05", 7, now=at("2026-03-05", 23))
        self.assertEqual([row["lit"] for row in r["rows"]], [0, 0, 2400, 2400, 2400, 2400, 2400])
        self.assertEqual(r["data_days"], 5)
        census = {e["label"]: e for e in r["census"]}
        self.assertEqual(census["Reddit"]["seconds"], 3000)
        self.assertEqual(census["Reddit"]["days"], 5)


class Legacy(Base):
    def test_waytime_totals_show_as_totals_only(self):
        core.ingest(self.db, {"device": {"id": "desk-1"}, "legacy": [
            ["2026-01-05", "zen", 3600], ["2026-01-05", "kitty", 1800], ["2026-01-05", "plasmashell", 9000],
            ["2026-01-05", "site:YouTube", 2400]]})
        r = core.day_report(self.db, "2026-01-05", now=at("2026-03-01", 12))
        self.assertEqual(r["lit"], 0)
        self.assertEqual(r["legacy"]["seconds"], 5400)  # the desktop itself never counted; sites are inside the browser
        rows = {label: seconds for label, seconds, _ in r["legacy"]["apps"]}
        self.assertEqual(rows["YouTube"], 2400)
        self.assertEqual(rows["Zen"], 1200)


    def test_a_phone_with_spans_does_not_hide_a_computer_with_only_totals(self):
        a = at("2026-01-05", 9)
        core.ingest(self.db, {"device": {"id": "desk-1"}, "legacy": [["2026-01-05", "kitty", 1800]]})
        self.send("phone-1", [(a, a + 600, "org.signal.signal", "")], kind="phone", now=a + 4000)
        day = core.day_report(self.db, "2026-01-05", now=at("2026-03-01", 12))
        self.assertEqual(day["lit"], 600)
        self.assertEqual(day["legacy"]["seconds"], 1800)
        r = core.range_report(self.db, "2026-01-05", 2, now=at("2026-03-01", 12))
        self.assertEqual(r["rows"][-1]["lit"], 2400)
        self.assertEqual(r["legacy_apps"], [["kitty", 1800, "make"]])

    def test_once_a_computer_has_spans_its_total_for_that_day_is_dropped(self):
        a = at("2026-01-05", 9)
        core.ingest(self.db, {"device": {"id": "desk-1"}, "legacy": [["2026-01-05", "kitty", 1800]]})
        self.send("desk-1", [(a, a + 600, "kitty", "")], now=a + 4000)
        day = core.day_report(self.db, "2026-01-05", now=at("2026-03-01", 12))
        self.assertIsNone(day["legacy"])

    def test_document_titles_waytime_kept_as_sites_go_back_to_the_browser(self):
        core.ingest(self.db, {"device": {"id": "desk-1"}, "legacy": [
            ["2026-01-05", "zen", 3600], ["2026-01-05", "site:Essay draft.docx", 600],
            ["2026-01-05", "site:Trifold Brochure", 600], ["2026-01-05", "site:Printables.com", 900]]})
        r = core.day_report(self.db, "2026-01-05", now=at("2026-03-01", 12))
        rows = {label: seconds for label, seconds, _ in r["legacy"]["apps"]}
        self.assertEqual(rows, {"Zen": 2700, "Printables": 900})


class OneDevice(Base):
    def setUp(self):
        super().setUp()
        a = self.a = at("2026-03-10", 14)
        self.send("desk-1", [(a, a + 3600, "kitty", "")], now=a + 8000)
        self.send("phone-1", [(a + 1800, a + 4800, "com.reddit.frontpage", "")], kind="phone", now=a + 8000,
                  pickups=[ms(a + 1800)])

    def test_the_day_as_one_device_saw_it(self):
        r = core.day_report(self.db, "2026-03-10", now=self.a + 8000, device="phone-1")
        self.assertEqual(r["lit"], 3000)
        self.assertEqual(len(r["entities"]), 1)
        self.assertEqual(r["attention"]["pickups"], 1)
        self.assertEqual({d["id"]: d["seconds"] for d in r["devices"]}, {"desk-1": 3600, "phone-1": 3000})

    def test_every_device_together(self):
        r = core.day_report(self.db, "2026-03-10", now=self.a + 8000)
        self.assertEqual(r["lit"], 4800)

    def test_a_range_for_one_device(self):
        cache = core.Cache()
        r = core.range_report(self.db, "2026-03-10", 7, cache, now=self.a + 90000, device="desk-1")
        self.assertEqual(r["rows"][-1]["lit"], 3600)
        self.assertEqual({d["id"]: d["seconds"] for d in r["devices"]}, {"desk-1": 3600, "phone-1": 3000})
        everyone = core.range_report(self.db, "2026-03-10", 7, cache, now=self.a + 90000)
        self.assertEqual(everyone["rows"][-1]["lit"], 4800)  # the cache keeps the two apart


class Rules(Base):
    def test_a_browser_is_a_browser_whatever_the_phone_calls_it(self):
        a = at("2026-03-10", 14)
        self.send("phone-1", [(a, a + 600, "org.mozilla.firefox", "")], kind="phone", now=a + 4000,
                  names={"org.mozilla.firefox": {"label": "Firefox", "hint": "talk"}})
        r = core.day_report(self.db, "2026-03-10", now=a + 4000)
        self.assertEqual(r["entities"][0]["cat"], "browse")

    def test_old_phone_apps_cannot_send_all_night_spans(self):
        a = at("2026-03-10", 1)
        body = {"device": {"id": "phone-1", "kind": "phone", "version": "1.0.1"},
                "spans": [[ms(a), ms(a + 30000), "x.blocker", ""], [ms(a + 30000), ms(a + 30600), "x.app", ""]]}
        core.ingest(self.db, body, now=a + 40000)
        self.assertEqual([r[0] for r in self.db.execute("SELECT app FROM spans")], ["x.app"])

    def test_a_device_can_replace_what_it_sent(self):
        a = at("2026-03-10", 14)
        self.send("phone-1", [(a - 7200, a - 7000, "x.before", ""), (a, a + 30000, "x.blocker", "")], kind="phone", now=a + 40000)
        _, touched = self.send("phone-1", [(a + 29000, a + 29100, "x.blocker", "")], kind="phone", now=a + 40000,
                               replace_from=ms(a - 60))
        rows = self.db.execute("SELECT app, (end - start) / 1000 FROM spans ORDER BY start").fetchall()
        self.assertEqual(rows, [("x.before", 200), ("x.blocker", 100)])
        self.assertIn("2026-03-10", touched)


class Burns(Base):
    def test_what_was_on_screen_during_a_burn(self):
        a = at("2026-03-10", 14)
        self.send("desk-1", [(a, a + 1200, "kitty", ""), (a + 1200, a + 1500, "zen", "YouTube")], now=a + 4000)
        burn = {"id": "b1", "start": a, "end": a + 1800, "minutes": 30}
        r = core.day_report(self.db, "2026-03-10", now=a + 4000, burns=[burn])
        b = r["burns"][0]
        self.assertEqual(b["lit"], 1500)
        self.assertEqual(b["cats"]["make"], 1200)
        self.assertEqual(b["leaks"], [["YouTube", 300]])


if __name__ == "__main__":
    unittest.main()
