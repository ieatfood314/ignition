import os
import sqlite3
import tempfile
import unittest

from tests import _path  # noqa: F401
from screen import core, tracker
from screen.tracker import Recorder

T = 1_780_000_000.0  # an arbitrary moment


class RecorderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = core.open_db(os.path.join(self.tmp.name, "t.db"))
        self.r = Recorder(self.db, "desk-1")

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def spans(self):
        return [(round((s - T * 1000) / 1000), round((e - T * 1000) / 1000), app, site)
                for s, e, app, site in self.db.execute("SELECT start, end, app, site FROM spans ORDER BY start")]

    def run_for(self, start, seconds):
        """Heartbeats every five seconds, as the daemon gives."""
        t = start
        while t < start + seconds:
            t += 5
            self.r.beat(t)
        return t

    def test_focus_changes_make_spans(self):
        self.r.set_focus("kitty", "", T)
        self.run_for(T, 60)
        self.r.set_focus("zen", "YouTube", T + 60)
        self.run_for(T + 60, 30)
        self.r.stop(T + 90)
        self.assertEqual(self.spans(), [(0, 60, "kitty", ""), (60, 90, "zen", "YouTube")])

    def test_a_tab_change_is_a_new_span(self):
        self.r.set_focus("zen", "YouTube", T)
        self.run_for(T, 40)
        self.r.set_focus("zen", "Reddit", T + 40)
        self.run_for(T + 40, 10)
        self.r.stop(T + 50)
        self.assertEqual(self.spans(), [(0, 40, "zen", "YouTube"), (40, 50, "zen", "Reddit")])

    def test_passing_through_a_window_leaves_no_trace(self):
        self.r.set_focus("kitty", "", T)
        self.run_for(T, 10)
        self.r.set_focus("dolphin", "", T + 10)
        self.r.set_focus("zen", "Reddit", T + 10.4)
        self.run_for(T + 10.4, 10)
        self.r.stop(T + 20.4)
        self.assertEqual([s[2] for s in self.spans()], ["kitty", "zen"])

    def test_the_desktop_itself_is_nobody(self):
        self.r.set_focus("kitty", "", T)
        self.run_for(T, 30)
        self.r.set_focus("plasmashell", "", T + 30)
        self.run_for(T + 30, 600)
        self.r.stop(T + 630)
        self.assertEqual(self.spans(), [(0, 30, "kitty", "")])

    def test_lock_stops_the_clock(self):
        self.r.set_focus("kitty", "", T)
        self.run_for(T, 100)
        self.r.set_locked(True, T + 100)
        self.run_for(T + 100, 300)
        self.r.set_locked(False, T + 400)
        self.run_for(T + 400, 50)
        self.r.stop(T + 450)
        self.assertEqual(self.spans(), [(0, 100, "kitty", ""), (400, 450, "kitty", "")])

    def test_agent_use_stops_the_clock(self):
        self.r.set_focus("kitty", "", T)
        self.run_for(T, 30)
        self.r.set_agent(True, T + 30)
        self.run_for(T + 30, 600)
        self.r.set_agent(False, T + 630)
        self.run_for(T + 630, 50)
        self.r.stop(T + 680)
        self.assertEqual(self.spans(), [(0, 30, "kitty", ""), (630, 680, "kitty", "")])

    def test_walking_away_is_dated_back_to_the_last_input(self):
        self.r.set_focus("kitty", "", T)
        t = self.run_for(T, 400)
        self.r.set_idle(True, t, since=t - 180)   # the compositor says: nothing for three minutes
        self.run_for(t, 1200)
        self.r.set_idle(False, t + 1200)
        self.run_for(t + 1200, 60)
        self.r.stop(t + 1260)
        self.assertEqual(self.spans(), [(0, 220, "kitty", ""), (1600, 1660, "kitty", "")])

    def test_a_few_minutes_of_reading_is_not_walking_away(self):
        self.r.set_focus("zen", "Wikipedia", T)
        t = self.run_for(T, 400)
        self.r.set_idle(True, t, since=t - 180)
        self.r.set_idle(False, t + 60)            # back four minutes after the last touch
        self.run_for(t + 60, 40)
        self.r.stop(t + 100)
        self.assertEqual(self.spans(), [(0, 500, "zen", "Wikipedia")])

    def test_coming_back_somewhere_else_does_not_stitch(self):
        self.r.set_focus("zen", "Wikipedia", T)
        t = self.run_for(T, 400)
        self.r.set_idle(True, t, since=t - 180)
        self.r.set_focus("kitty", "", t + 30)
        self.r.set_idle(False, t + 60)
        self.run_for(t + 60, 40)
        self.r.stop(t + 100)
        self.assertEqual(self.spans(), [(0, 220, "zen", "Wikipedia"), (460, 500, "kitty", "")])

    def test_an_inhibitor_change_does_not_restart_an_idle_night(self):
        inhibited = [True]
        idle = tracker.IdleGate(self.r, lambda: inhibited[0])
        self.r.set_focus("kitty", "", T)
        t = self.run_for(T, 120)
        idle.raw(True, t)                         # the input-only watch has decided nobody is there
        inhibited[0] = False
        idle.normal(False, t + 60)                # an inhibitor change, not keyboard or pointer input
        idle.apply(t + 60)
        t = self.run_for(t + 60, 420)
        idle.normal(False, t)
        idle.raw(False, t)                        # actual input returns
        self.run_for(t, 60)
        self.r.stop(t + 60)
        self.assertEqual(self.spans(), [(0, 120, "kitty", ""), (600, 660, "kitty", "")])

    def test_suspend_ends_the_span_at_the_last_heartbeat(self):
        self.r.set_focus("kitty", "", T)
        t = self.run_for(T, 100)
        self.r.beat(t + 8 * 3600)                 # the lid was shut for eight hours
        self.run_for(t + 8 * 3600, 20)
        self.r.stop(t + 8 * 3600 + 20)
        self.assertEqual(self.spans(), [(0, 100, "kitty", ""), (28900, 28920, "kitty", "")])

    def test_every_change_is_queued_for_the_hub(self):
        self.r.set_focus("kitty", "", T)
        self.run_for(T, 20)
        self.r.set_focus("zen", "Reddit", T + 20)
        self.run_for(T + 20, 20)
        self.r.stop(T + 40)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0], 2)


class AgentProcessTest(unittest.TestCase):
    def process(self, root, pid, command, cgroup):
        directory = os.path.join(root, str(pid))
        os.makedirs(directory)
        with open(os.path.join(directory, "comm"), "w") as f:
            f.write(command + "\n")
        with open(os.path.join(directory, "cgroup"), "w") as f:
            f.write(cgroup + "\n")

    def test_only_an_agent_turn_counts_not_the_t3_server(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.process(tmp, 10, "node-MainThread", "0::/user.slice/user@1000.service/app.slice/t3code.service\n")
            self.assertFalse(tracker.t3_agent_running(tmp))
            self.process(tmp, 11, "opencode", "0::/user.slice/user@1000.service/app.slice/other.service\n")
            self.assertFalse(tracker.t3_agent_running(tmp))
            self.process(tmp, 12, "claude", "0::/user.slice/user@1000.service/app.slice/t3code.service\n")
            self.assertTrue(tracker.t3_agent_running(tmp))


class ImportTest(unittest.TestCase):
    def test_waytime_history_comes_over_once_without_the_desktop(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_path = os.path.join(tmp, "waytime.db")
            old = sqlite3.connect(old_path)
            old.execute("CREATE TABLE usage (date TEXT, app TEXT, seconds REAL)")
            old.execute("CREATE TABLE sites (date TEXT, site TEXT, seconds REAL)")
            old.executemany("INSERT INTO usage VALUES (?,?,?)", [
                ("2026-07-04", "zen", 3600), ("2026-07-04", "plasmashell", 50000), ("2026-07-05", "kitty", 60)])
            old.execute("INSERT INTO sites VALUES ('2026-07-04', 'YouTube', 1800)")
            old.commit()
            old.close()
            db = core.open_db(os.path.join(tmp, "new.db"))
            self.assertEqual(tracker.import_waytime(db, "desk-1", old_path), 3)
            self.assertEqual(tracker.import_waytime(db, "desk-1", old_path), 0)
            rows = db.execute("SELECT date, app, seconds FROM legacy ORDER BY date, app").fetchall()
            self.assertEqual(rows, [("2026-07-04", "site:YouTube", 1800.0), ("2026-07-04", "zen", 3600.0),
                                    ("2026-07-05", "kitty", 60.0)])
            db.close()


if __name__ == "__main__":
    unittest.main()
