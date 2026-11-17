"""A turn ends sooner when the words so far end a sentence, not on a filler."""

import unittest
from types import SimpleNamespace

from tests import sandbox  # first: a throwaway HOME
import turn

assert sandbox.HOME  # imported for its effect


def done(said: str) -> bool:
    fake = SimpleNamespace(live=SimpleNamespace(so_far=lambda: said))
    return turn.Turn.sentence_done(fake)


class SentenceDone(unittest.TestCase):
    def test_finished_sentence(self):
        self.assertTrue(done("Run the tests."))
        self.assertTrue(done("Is it working?"))

    def test_trailing_off(self):
        self.assertFalse(done("Run the tests and uh."))
        self.assertFalse(done("Check the logs because."))
        self.assertFalse(done("Run the tests"))  # no full stop yet
        self.assertFalse(done(""))

    def test_over_is_its_own_check(self):
        self.assertFalse(done("That's all, over."))

    def test_no_live_words(self):
        self.assertFalse(turn.Turn.sentence_done(SimpleNamespace(live=None)))


if __name__ == "__main__":
    unittest.main()
