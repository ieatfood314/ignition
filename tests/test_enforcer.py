import importlib.util
import unittest
from pathlib import Path
from unittest import mock


spec = importlib.util.spec_from_file_location("ignition_enforcer", Path(__file__).parents[1] / "enforcer/ignition-enforcer.py")
enforcer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(enforcer)


class OfflineBurn(unittest.TestCase):
    def test_cached_countdown_locks_on_time(self):
        obj = enforcer.Enforcer.__new__(enforcer.Enforcer)
        obj.session = {"session_id": "burn", "active": False, "started_at": 1100, "ends_at": 1900}
        obj.limits = {"limits": [{"id": "l-burning", "burn": True, "starts_at": 1100, "until": 1900,
                                  "sites": ["example.com"], "programs": ["vlc"], "games": False}]}
        with mock.patch.object(enforcer.time, "time", return_value=1000):
            self.assertFalse(obj.active())
            self.assertEqual(obj.closed_limits(), [])
        with mock.patch.object(enforcer.time, "time", return_value=1100):
            self.assertTrue(obj.active())
            self.assertEqual(obj.closed_limits()[0]["sites"], ["example.com"])
        with mock.patch.object(enforcer.time, "time", return_value=1900):
            self.assertFalse(obj.active())
            self.assertEqual(obj.closed_limits(), [])

    def test_legacy_cached_blocks_hold_until_unified_sync(self):
        obj = enforcer.Enforcer.__new__(enforcer.Enforcer)
        obj.session = {"session_id": "burn", "active": False, "started_at": 1100, "ends_at": 1900}
        obj.limits = {"limits": []}
        obj.legacy_block = {"sites": ["example.com"], "programs": ["vlc"], "games": False}
        obj.local_block = []
        obj.limit_since = {}
        with mock.patch.object(enforcer.time, "time", return_value=1100):
            wanted = obj.wanted()
            self.assertEqual(wanted["vlc"][0], 1100)


if __name__ == "__main__":
    unittest.main()
