"""install.sh agents adds Cursor's debugging port to its launcher: the program word only, once, and leaves a
launcher it doesn't know alone (the reviewer's catch, 2026-11-16: an AppImage path got the flag in its middle)."""

import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STEP = re.search(r"^import re, sys\n.*?(?=^PYEOF$)", (ROOT / "install.sh").read_text(), re.S | re.M).group(0)


def launcher(text: str | None) -> tuple[int, str]:
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "cursor.desktop"
        if text is not None:
            path.write_text(text)
        done = subprocess.run([sys.executable, "-c", STEP, str(path)], capture_output=True, text=True)
        return done.returncode, path.read_text() if path.exists() else ""


class Launcher(unittest.TestCase):
    def test_flag_after_the_program(self):
        code, text = launcher("Exec=/usr/share/cursor/cursor --classic %F\n"
                              "Exec=/usr/share/cursor/cursor --new-window\n")
        self.assertEqual(code, 0)
        self.assertEqual(text.count("/cursor --remote-debugging-port=9333 "), 2)

    def test_once(self):
        first = launcher("Exec=/usr/share/cursor/cursor %F\n")[1]
        self.assertEqual(launcher(first), (0, first))

    def test_appimage_left_alone(self):
        for exec_line in ("Exec=/home/u/Applications/cursor.AppImage --no-sandbox %U",
                          "Exec=/opt/cursor/cursor-1.0.AppImage"):
            code, text = launcher(exec_line + "\n")
            self.assertEqual(code, 1)
            self.assertNotIn("--remote-debugging-port", text)


if __name__ == "__main__":
    unittest.main()
