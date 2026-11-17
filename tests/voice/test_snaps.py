"""voice/snaps.py: photos from the call screen's camera wait for the next turn; voice/screenshot.py draws a pane."""

import tempfile
import time
import unittest
from pathlib import Path

from tests import sandbox  # noqa: F401  (first: a throwaway HOME)
from voice import screenshot, snaps


class Waiting(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.a = self.dir / "a.jpg"
        self.a.write_bytes(b"x" * 10)
        snaps.take()  # empty the list

    def test_the_next_turn_takes_them_once(self):
        snaps.add([self.a], "claude-1")
        self.assertEqual(snaps.paths(snaps.take("claude-1")), [self.a])
        self.assertEqual(snaps.take("claude-1"), [])

    def test_another_agents_photo_stays(self):
        snaps.add([self.a], "claude-1")
        self.assertEqual(snaps.take("cursor-2"), [])
        self.assertEqual(snaps.paths(snaps.take("claude-1")), [self.a])

    def test_the_timer_only_takes_those_that_waited(self):
        snaps.add([self.a], "claude-1", now=time.time())
        self.assertEqual(snaps.take("claude-1", older_than=snaps.WAIT), [])
        later = time.time() + snaps.WAIT + 1
        self.assertEqual(snaps.paths(snaps.take("claude-1", older_than=snaps.WAIT, now=later)), [self.a])

    def test_put_back_keeps_its_age(self):
        snaps.add([self.a], "claude-1", now=time.time() - 100)
        taken = snaps.take("claude-1")
        snaps.put_back(taken)
        self.assertEqual(snaps.paths(snaps.take("claude-1", older_than=snaps.WAIT)), [self.a])

    def test_a_photo_that_cannot_be_delivered_is_let_go_after_a_few_tries(self):
        snaps.add([self.a], "claude-1")
        for _ in range(snaps.TRIES - 1):
            self.assertEqual(snaps.put_back(snaps.take("claude-1"), tried=True), [])
        self.assertEqual(len(snaps.put_back(snaps.take("claude-1"), tried=True)), 1)
        self.assertEqual(snaps.take("claude-1"), [])

    def test_an_old_photo_is_let_go(self):
        snaps.add([self.a], "claude-1", now=time.time() - snaps.MAX_AGE - 1)
        self.assertEqual(snaps.take("claude-1"), [])
        self.assertFalse(snaps.waiting())

    def test_a_new_call_clears_them(self):
        snaps.add([self.a], "claude-1")
        snaps.clear()
        self.assertEqual(snaps.take("claude-1"), [])

    def test_a_photo_gone_from_the_disk_is_dropped(self):
        snaps.add([self.dir / "gone.jpg"], "claude-1")
        self.assertEqual(snaps.take("claude-1"), [])

    def test_words_name_the_file(self):
        said = snaps.words([{"path": str(self.a), "session": "x", "at": 0}])
        self.assertIn(str(self.a), said)
        self.assertIn("a photo", said)
        self.assertEqual(snaps.words([]), "")


class Drawn(unittest.TestCase):
    def test_a_pane_with_colours_is_a_picture(self):
        out = Path(tempfile.mkdtemp()) / "pane.png"
        screenshot.draw("\x1b[1;32mok\x1b[0m plain\n\x1b[38;5;196mred\x1b[0m \x1b[48;2;0;0;90mblue\x1b[0m\n\n\n", out)
        self.assertGreater(out.stat().st_size, 100)

    def test_runs_keep_the_colours(self):
        found = screenshot.runs("\x1b[31mA\x1b[0mB")
        self.assertEqual([t for t, *_ in found], ["A", "B"])
        self.assertNotEqual(found[0][1], found[1][1])


if __name__ == "__main__":
    unittest.main()
