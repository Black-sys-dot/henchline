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


class FoundThenHeard(unittest.TestCase):
    """An agent started from a new chat is found running under one key, then speaks or texts under its own."""

    def setUp(self):
        state.SPEAKERS.mkdir(parents=True, exist_ok=True)
        for path in state.SPEAKERS.glob("*.json"):
            path.unlink()
        state.RENAMED.unlink(missing_ok=True)

    def test_the_found_record_goes_and_the_phone_is_told(self):
        tab = state.Tab("tmux", "%1")
        state.Speaker("claude-_home_me_sonar_rig", "claude", "sonar_rig", tab).save(state.SPEAKERS / "claude-_home_me_sonar_rig.json")
        heard = state.Speaker("claude-0c84", "claude", "sonar_rig", tab)
        heard.save(state.SPEAKERS / "claude-0c84.json")
        heard.replaces_found()
        self.assertEqual(sorted(p.stem for p in state.SPEAKERS.glob("*.json")), ["claude-0c84"])
        self.assertEqual(state.read_json(state.RENAMED), {"claude-_home_me_sonar_rig": "claude-0c84"})

    def test_a_chain_of_renames_points_at_the_newest(self):
        state.renamed("a", "b")
        state.renamed("b", "c")
        self.assertEqual(state.read_json(state.RENAMED), {"a": "c", "b": "c"})

    def test_a_new_session_in_that_tab_is_not_a_rename(self):
        tab = state.Tab("tmux", "%1")
        state.Speaker("claude-11111111-2222-3333-4444-555555555555", "claude", "x", tab).save(
            state.SPEAKERS / "claude-11111111-2222-3333-4444-555555555555.json")
        heard = state.Speaker("claude-0c84", "claude", "x", tab)
        heard.save(state.SPEAKERS / "claude-0c84.json")
        heard.replaces_found()
        self.assertEqual(state.read_json(state.RENAMED), {})  # the old record goes, its chat stays its own

    def test_a_found_key_in_use_again_is_no_longer_renamed(self):
        state.renamed("claude-_home_me_sonar_rig", "claude-0c84")
        state.Speaker("claude-_home_me_sonar_rig", "claude", "sonar_rig").save(
            state.SPEAKERS / "claude-_home_me_sonar_rig.json")
        self.assertEqual(state.read_json(state.RENAMED), {})

    def test_another_tab_stays(self):
        state.Speaker("claude-other", "claude", "x", state.Tab("tmux", "%2")).save(state.SPEAKERS / "claude-other.json")
        heard = state.Speaker("claude-0c84", "claude", "y", state.Tab("tmux", "%1"))
        heard.save(state.SPEAKERS / "claude-0c84.json")
        heard.replaces_found()
        self.assertTrue((state.SPEAKERS / "claude-other.json").exists())
        self.assertEqual(state.read_json(state.RENAMED), {})
