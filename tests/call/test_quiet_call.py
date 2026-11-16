"""A text that goes unanswered escalates to a call; a quiet call asks after you, then calls back."""

import importlib.util
import unittest

from tests import sandbox  # first: a throwaway HOME
from voice.state import TEXTS, Sent, read_json, write_json

spec = importlib.util.spec_from_file_location("call_server_quiet", sandbox.REPO / "call/server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)

hf_spec = importlib.util.spec_from_file_location("handsfree_cli", sandbox.REPO / "handsfree/handsfree.py")
handsfree = importlib.util.module_from_spec(hf_spec)
hf_spec.loader.exec_module(handsfree)


class Replied(unittest.TestCase):
    def test_a_message_answers_that_agents_texts_only(self):
        TEXTS.mkdir(parents=True, exist_ok=True)
        write_json(TEXTS / "aaaa.json", {"id": "aaaa", "session": "claude-x", "status": "delivered"})
        write_json(TEXTS / "bbbb.json", {"id": "bbbb", "session": "codex-y", "status": "delivered"})
        Sent("claude", "texted", session="claude-x").save()  # any delivery of theirs to that agent
        self.assertTrue(read_json(TEXTS / "aaaa.json").get("replied"))
        self.assertFalse(read_json(TEXTS / "bbbb.json").get("replied"))
        self.assertTrue(handsfree.text_replied("aaaa"))
        self.assertFalse(handsfree.text_replied("bbbb"))
        self.assertFalse(handsfree.text_replied(""))


class RingAs(unittest.TestCase):
    def test_unknown_agent_is_not_rung(self):
        self.assertEqual(handsfree.ring("why", now=True, wait=False, as_session="claude-nobody"), 1)


class Nudges(unittest.TestCase):
    def test_two_nudges_then_call_back(self):
        self.assertEqual(len(server.NUDGES), 2)
        self.assertLess(server.NUDGE_AFTER, server.NUDGE_UNSURE)


if __name__ == "__main__":
    unittest.main()
