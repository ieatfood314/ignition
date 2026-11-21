import io
import json
import os
import tempfile
import time
import unittest
from unittest import mock

from tests import _path  # noqa: F401
from screen import core, limits
from screen.hub import Screen


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def chat(payload):
    return Response(json.dumps({"choices": [{"message": {"content": json.dumps(payload)}}]}).encode())


class AIClassification(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.screen = Screen(os.path.join(self.tmp.name, "screen.db"), None)
        self.db = self.screen.db
        now = int(time.time() * 1000)
        core.ingest(self.db, {"device": {"id": "test-pc", "name": "Test PC", "kind": "desktop"},
                              "spans": [[now - 1200_000, now - 600_000, "claude-desktop", ""],
                                        [now - 600_000, now, "zen", "YouTube"]]})
        defs = core.save_categories(self.db, [*core.category_list(self.db),
                                              {"id": "", "name": "Time wasting", "color": "#ff0000"}])
        self.cat = next(c for c in defs if c["name"] == "Time wasting")
        self.env = mock.patch.dict(os.environ, {}, clear=True)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.db.close()
        self.tmp.cleanup()

    def configure(self):
        return self.screen.save_ai_settings({"url": "https://ai.example/v1", "model": "test-model",
                                             "api_key": "secret"})

    def test_settings_keep_the_key_private(self):
        settings = self.configure()
        self.assertEqual(settings, {"url": "https://ai.example/v1", "model": "test-model", "has_key": True})
        self.assertNotIn("api_key", settings)
        self.assertNotIn("secret", json.dumps(settings))

    def test_ai_assigns_only_known_automatic_things(self):
        self.configure()
        limits.set_category(self.db, "n:youtube", "watch")  # an explicit choice is not offered to the AI
        with mock.patch("urllib.request.urlopen", return_value=chat({"keys": ["n:claude", "n:youtube", "n:invented"]})) as call:
            result = self.screen.ai_classify({"cat": self.cat["id"]})
        self.assertEqual(result["assigned"], 1)
        self.assertEqual(result["pending"], 0)
        self.assertEqual(core.Namer(self.db).category("claude-desktop", ""), self.cat["id"])
        self.assertEqual(core.Namer(self.db).category("zen", "YouTube"), "watch")
        request = call.call_args[0][0]
        self.assertEqual(request.full_url, "https://ai.example/v1/chat/completions")
        self.assertEqual(request.headers["Authorization"], "Bearer secret")
        prompt = json.loads(request.data)["messages"][0]["content"]
        self.assertIn("Claude", prompt)
        self.assertNotIn("YouTube", prompt)

    def test_manual_choice_made_while_ai_runs_wins(self):
        self.configure()
        def answer(*_, **__):
            limits.set_category(self.db, "n:claude", "play")
            return chat({"keys": ["n:claude"]})
        with mock.patch("urllib.request.urlopen", side_effect=answer):
            result = self.screen.ai_classify({"cat": self.cat["id"]})
        self.assertEqual(result["assigned"], 0)
        self.assertEqual(core.Namer(self.db).category("claude-desktop", ""), "play")

    def test_bad_ai_answer_changes_nothing(self):
        self.configure()
        with mock.patch("urllib.request.urlopen", return_value=Response(b"not json")):
            with self.assertRaises(ValueError):
                self.screen.ai_classify({"cat": self.cat["id"]})
        self.assertEqual(core.Namer(self.db).category("claude-desktop", ""), "make")


if __name__ == "__main__":
    unittest.main()
