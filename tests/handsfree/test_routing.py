"""handsfree/core/routing.py: what a transcribed turn asks for, before any agent sees it."""

import unittest
from unittest import mock

from tests import sandbox  # first: a throwaway HOME
import routing
from routing import Meaning

assert sandbox.HOME  # imported for its effect


class Switching(unittest.TestCase):
    def test_the_fixed_phrases(self):
        self.assertEqual(routing.switch_request("Switch me to Cursor"), ("cursor", None, False))
        self.assertEqual(routing.switch_request("just switch me to Cursor in pc diagnostics"),
                         ("cursor", "pc diagnostics", False))
        self.assertEqual(routing.switch_request("send that to Codex"), ("codex", None, True))
        self.assertEqual(routing.switch_request("Scratch that, hand this to Claude")[0], "claude")
        self.assertEqual(routing.switch_request("Yeah, I'm here now. Please switch me to Cursor.")[0], "cursor")

    def test_messages_that_merely_mention_an_agent_are_messages(self):
        self.assertIsNone(routing.switch_request("don't switch me to Claude"))
        self.assertIsNone(routing.switch_request("I wonder whether Cursor would handle this better than Claude"))
        long_lead = "This is a long message about the parser and the tests and the build. Switch me to Cursor."
        self.assertIsNone(routing.switch_request(long_lead))


class HangingUp(unittest.TestCase):
    def test_short_requests_only(self):
        self.assertTrue(routing.hangup_request("hang up"))
        self.assertTrue(routing.hangup_request("Okay, hang up now"))
        self.assertTrue(routing.hangup_request("I'm in, hang up"))
        self.assertFalse(routing.hangup_request("how do I hang up?"))
        self.assertFalse(routing.hangup_request("don't hang up"))

    def test_only_on_a_call(self):
        with mock.patch.object(routing, "in_call", return_value=True):
            self.assertEqual(routing.meaning("hang up"), Meaning.HANGUP)
        with mock.patch.object(routing, "in_call", return_value=False):
            self.assertEqual(routing.meaning("hang up"), Meaning.MESSAGE)


class Meanings(unittest.TestCase):
    def test_each_kind(self):
        self.assertEqual(routing.meaning("give me a minute"), Meaning.TIME)
        self.assertEqual(routing.meaning("who am I talking to?"), Meaning.ROUTING)
        self.assertEqual(routing.meaning("hands-free off"), Meaning.OFF)
        self.assertEqual(routing.meaning("never mind"), Meaning.CANCEL)
        self.assertEqual(routing.meaning("um"), Meaning.NOTHING)
        self.assertEqual(routing.meaning("   "), Meaning.NOTHING)
        self.assertEqual(routing.meaning("run the tests again please"), Meaning.MESSAGE)
        self.assertEqual(routing.meaning("yes"), Meaning.MESSAGE)


if __name__ == "__main__":
    unittest.main()
