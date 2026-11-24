import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from tests import _path  # noqa: F401
from ignition import App, Store
from screen import limits
from screen.hub import Screen


class BurnMigration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = App(Store(Path(self.tmp.name) / "ignition.db"))
        settings = self.app.settings()[0]
        settings["block"] = {
            "sites": ["youtube.com"], "apps": [{"id": "com.termux", "label": "Termux"}],
            "programs": ["vlc"], "games": True, "pc_games": False,
        }
        self.app.s.x("INSERT INTO kv (key, value, updated_at, rev) VALUES ('settings', ?, ?, ?)",
                     (json.dumps(settings), 1, self.app.s.next_rev()))
        self.path = os.path.join(self.tmp.name, "screen.db")

    def tearDown(self):
        self.app.s.db.close()
        self.tmp.cleanup()

    def test_legacy_block_becomes_one_burn_limit_once(self):
        screen = Screen(self.path, self.app)
        rows = limits.rows(screen.db)
        self.assertEqual(len(rows), 1)
        limit = rows[0]["limit"]
        self.assertEqual(limit["id"], "l-burning")
        self.assertEqual(limit["rules"], [{"kind": "burn"}])
        self.assertEqual(limit["targets"], ["s:youtube.com", "a:com.termux", "p:vlc", "g:phone"])
        self.assertEqual(limit["labels"]["a:com.termux"], "Termux")
        self.assertNotIn("block", self.app.settings()[0])
        limits.delete(screen.db, "l-burning")
        screen.db.close()
        again = Screen(self.path, self.app)
        self.assertEqual(limits.rows(again.db), [])
        again.db.close()

    def test_burn_limit_cannot_be_loosened_mid_burn(self):
        screen = Screen(self.path, self.app)
        old = limits.rows(screen.db)[0]["limit"]
        self.app.insert_session("burn", 50, int(time.time()), "manual")
        looser = dict(old, targets=old["targets"][:-1])
        with self.assertRaisesRegex(ValueError, "can't be loosened"):
            screen.guard_burn_save(looser)
        with self.assertRaisesRegex(ValueError, "can't be removed"):
            screen.guard_burn_delete(old["id"])
        screen.db.close()


if __name__ == "__main__":
    unittest.main()
