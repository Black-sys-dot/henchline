"""voice/state.py: whole-or-nothing writes, private files, and the words a call may keep."""

import json
import os
import stat
import unittest
from pathlib import Path
from unittest import mock

from tests import sandbox  # noqa: F401  (first: a throwaway HOME)
from voice import state


class WriteJson(unittest.TestCase):
    def setUp(self):
        self.folder = Path(sandbox.HOME) / f"writes-{self._testMethodName}"
        self.folder.mkdir()

    def test_private_is_owner_only_and_leaves_no_temp_file(self):
        path = self.folder / "secret.json"
        state.write_json(path, {"a": 1}, private=True)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(json.loads(path.read_text()), {"a": 1})
        self.assertEqual([p.name for p in self.folder.iterdir()], ["secret.json"])

    def test_a_rewrite_replaces_it_whole(self):
        path = self.folder / "plain.json"
        state.write_json(path, {"a": 1})
        state.write_json(path, {"b": 2})
        self.assertEqual(json.loads(path.read_text()), {"b": 2})
        self.assertFalse(any(p.name.endswith(".tmp") for p in self.folder.iterdir()))


class SentWords(unittest.TestCase):
    def setUp(self):
        state.STATE.mkdir(parents=True, exist_ok=True)

    def test_words_only_during_a_call(self):
        state.Sent("claude", "call", session="claude-1", words="hello there").save()
        with mock.patch.object(state, "in_call", return_value=True):
            self.assertEqual(state.Sent.last().words, "hello there")
        with mock.patch.object(state, "in_call", return_value=False):
            self.assertEqual(state.Sent.last().words, "")
        self.assertEqual(stat.S_IMODE(os.stat(state.SENT).st_mode), 0o600)

    def test_newer_fields_are_ignored(self):
        state.SENT.write_text(json.dumps({"agent": "claude", "heard": "reply", "at": 1.0, "from_the_future": 1}))
        self.assertEqual(state.Sent.last().agent, "claude")


if __name__ == "__main__":
    unittest.main()
