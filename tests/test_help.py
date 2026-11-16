"""Every command answers -h / --help with its usage and does nothing else, wherever the flag is: agents try it
first, and `handsfree ring --help` once rang the phone with "--help" as the reason (2026-11-16)."""

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(*command: str) -> subprocess.CompletedProcess:
    return subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=20)


class Help(unittest.TestCase):
    def check(self, *command: str, says: str) -> None:
        for flag in ("--help", "-h"):
            done = run(*command, flag)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertIn(says, done.stdout)

    def test_handsfree_ring(self):
        self.check(sys.executable, "handsfree/handsfree.py", "ring", says="handsfree ring [--done]")

    def test_handsfree_text(self):
        self.check(sys.executable, "handsfree/handsfree.py", "text", "hello", says="handsfree text")

    def test_ptt(self):
        self.check(sys.executable, "ptt/ptt.py", says="push-to-talk listener")

    def test_doctor(self):
        self.check(sys.executable, "doctor.py", "agents", says="./doctor.py")

    def test_install(self):
        self.check("sh", "install.sh", "agents", says="./install.sh ptt")

    def test_talkback(self):
        self.check(sys.executable, "talkback/scripts/say.py", says="usage: talkback")


if __name__ == "__main__":
    unittest.main()
