"""voice/pickup.py: whether an agent has taken in a message the phone sent (two ticks)."""

import json
import tempfile
import time
import unittest
from pathlib import Path

from tests import sandbox  # noqa: F401  (first: a throwaway HOME)
from voice import pickup


def stamp(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(t))


class ClaudeTook(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.log = self.home / ".claude/projects/-x/abc.jsonl"
        self.log.parent.mkdir(parents=True)
        self.now = time.time()

    def write(self, *entries):
        self.log.write_text("".join(json.dumps(e) + "\n" for e in entries))

    def took(self, words):
        return pickup.claude_took("abc", pickup.key(words), self.now - 10, self.home)

    def test_waiting_in_the_queue_is_not_taken(self):
        self.write({"type": "queue-operation", "operation": "enqueue", "timestamp": stamp(self.now),
                    "content": "[phone · text] Check the logs please, then tell me"})
        self.assertFalse(self.took("Check the logs please, then tell me"))

    def test_handed_in_mid_turn_is_taken(self):
        self.write({"type": "attachment", "timestamp": stamp(self.now),
                    "attachment": {"type": "queued_command", "prompt": "[phone · text] Check the logs, please!"}})
        self.assertTrue(self.took("check the logs please"))

    def test_a_turn_of_its_own_is_taken(self):
        self.write({"type": "user", "timestamp": stamp(self.now),
                    "message": {"content": [{"type": "text", "text": "[hands-free · call] Check the logs please"}]}})
        self.assertTrue(self.took("Check the logs please"))

    def test_an_older_same_message_does_not_count(self):
        self.write({"type": "user", "timestamp": stamp(self.now - 600), "message": {"content": "Check the logs please"}})
        self.assertFalse(self.took("Check the logs please"))


class Words(unittest.TestCase):
    def test_whole_words_only(self):
        self.assertFalse(pickup.says("[phone · text] look at that", pickup.key("ok")))
        self.assertTrue(pickup.says("[phone · text] ok", pickup.key("Ok!")))

    def test_nothing_to_know_it_by(self):
        self.assertIsNone(pickup.took("claude", "claude-x", "👍"))


class Others(unittest.TestCase):
    def test_cursor_cannot_say(self):
        self.assertIsNone(pickup.took("cursor", "cursor-x", "hello"))


if __name__ == "__main__":
    unittest.main()
