"""voice/files.py: names, where files land, what is served how, and what is never sent."""

import os
import time
import unittest
from pathlib import Path

from tests import sandbox  # noqa: F401  (first: a throwaway HOME)
from voice import files


class Names(unittest.TestCase):
    def test_a_name_never_leaves_its_folder(self):
        self.assertEqual(files.safe_name("../../etc/passwd"), "passwd")
        self.assertEqual(files.safe_name("a\\b"), "a_b")
        self.assertEqual(files.safe_name(".hidden"), "hidden")
        self.assertEqual(files.safe_name("bad\x07name\n.txt"), "bad_name_.txt")
        self.assertEqual(files.safe_name("   "), "file")

    def test_nothing_is_overwritten(self):
        folder = Path(sandbox.HOME) / "unused"
        folder.mkdir()
        (folder / "photo.png").write_text("x")
        self.assertEqual(files.unused(folder, "photo.png").name, "photo (2).png")
        (folder / "photo (2).png").write_text("x")
        self.assertEqual(files.unused(folder, "photo.png").name, "photo (3).png")


class Received(unittest.TestCase):
    def test_only_the_phones_own_uploads_today_or_yesterday(self):
        today = files.inbox_today()
        (today / "map.png").write_text("x")
        self.assertEqual(files.received("map.png"), today / "map.png")
        self.assertIsNone(files.received("../map.png"))
        self.assertIsNone(files.received("missing.png"))


class Sent(unittest.TestCase):
    def test_only_the_secret_finds_a_file(self):
        source = Path(sandbox.HOME) / "report.pdf"
        source.write_text("pdf")
        meta = files.post(source, "here you go", "claude", "claude-test")
        self.assertEqual(files.find(meta["secret"], "report.pdf").read_text(), "pdf")
        self.assertIsNone(files.find(meta["secret"], "meta.json"))
        self.assertIsNone(files.find("A" * 24, "report.pdf"))
        self.assertIsNone(files.find(meta["secret"], "../report.pdf"))
        self.assertIn(meta, files.undelivered())
        files.delivered(meta)
        self.assertNotIn(meta["secret"], [m["secret"] for m in files.undelivered()])

    def test_old_files_go(self):
        source = Path(sandbox.HOME) / "old.txt"
        source.write_text("old")
        meta = files.post(source, "", "claude", "claude-test")
        folder = files.OUTBOX / meta["secret"]
        past = time.time() - (files.KEEP_DAYS + 1) * 86400
        os.utime(folder, (past, past))
        files.clean()
        self.assertFalse(folder.exists())


class Served(unittest.TestCase):
    def test_only_media_is_shown_and_everything_is_sandboxed(self):
        for name in ("a.png", "a.jpg", "a.mp3", "a.mp4"):
            self.assertTrue(files.headers(Path(name))["Content-Disposition"].startswith("inline"), name)
        for name in ("a.svg", "a.html", "a.xml", "a.zip", "a.js"):
            head = files.headers(Path(name))
            self.assertTrue(head["Content-Disposition"].startswith("attachment"), name)
            self.assertEqual(head["Content-Type"], "application/octet-stream", name)
        self.assertEqual(files.headers(Path("a.png"))["X-Content-Type-Options"], "nosniff")
        self.assertIn("sandbox", files.headers(Path("a.png"))["Content-Security-Policy"])


class Private(unittest.TestCase):
    def test_keys_and_credentials_stay(self):
        home = Path.home()
        for path in (home / ".ssh/id_ed25519", home / ".gnupg/x", home / ".config/ptt/ntfy-topic",
                     home / "work/server.pem", home / "id_rsa"):
            self.assertTrue(files.private(path), path)
        for path in (home / "Pictures/a.png", Path("/tmp/report.pdf")):
            self.assertFalse(files.private(path), path)


class Message(unittest.TestCase):
    def test_one_line_with_paths_types_and_words(self):
        today = files.inbox_today()
        (today / "notes.txt").write_text("hello")
        said = files.message([today / "notes.txt"], "", "look at\nthis")
        self.assertIn(str(today / "notes.txt"), said)
        self.assertIn("text/plain", said)
        self.assertNotIn("\n", said)
        self.assertTrue(files.message([], "a link", "").startswith("The user shared this from their phone"))


if __name__ == "__main__":
    unittest.main()
