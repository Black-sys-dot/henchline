"""install.sh agents adds Cursor's debugging port to every launcher of the user's that starts Cursor (a package's
copy, an AppImage's own): right after the program, once, and to nothing else (an AppImage path once got the flag
in its middle, 2026-11-16)."""

import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STEP = re.search(r"^import re, shlex, sys\n.*?(?=^PYEOF$)", (ROOT / "install.sh").read_text(), re.S | re.M).group(0)
FLAG = "--remote-debugging-port=9333"


def run(files: dict[str, str]) -> tuple[int, dict[str, str]]:
    with tempfile.TemporaryDirectory() as folder:
        apps = Path(folder) / "applications"
        apps.mkdir()
        for name, text in files.items():
            (apps / name).write_text(text)
        done = subprocess.run([sys.executable, "-c", STEP, str(apps)], capture_output=True, text=True)
        return done.returncode, {f.name: f.read_text() for f in apps.iterdir()}


def entry(exec_line: str, name: str = "Cursor") -> str:
    return f"[Desktop Entry]\nName={name}\n{exec_line}\n"


class Launchers(unittest.TestCase):
    def test_package_copy(self):
        code, out = run({"cursor.desktop": entry("Exec=/usr/share/cursor/cursor --classic %F")})
        self.assertEqual(code, 0)
        self.assertIn(f"Exec=/usr/share/cursor/cursor {FLAG} --classic %F", out["cursor.desktop"])

    def test_appimage(self):
        for program in ("/home/u/Applications/cursor.AppImage", "/opt/cursor/Cursor-1.0-x86_64.AppImage"):
            code, out = run({"appimagekit-cursor.desktop": entry(f"Exec={program} --no-sandbox %U")})
            self.assertEqual(code, 0)
            self.assertIn(f"Exec={program} {FLAG} --no-sandbox %U", out["appimagekit-cursor.desktop"])

    def test_quoted_and_env(self):
        code, out = run({"a.desktop": entry('Exec=env A=1 "/home/u/My Apps/cursor.AppImage" %U')})
        self.assertEqual(code, 0)
        self.assertIn(f'Exec=env A=1 "/home/u/My Apps/cursor.AppImage" {FLAG} %U', out["a.desktop"])

    def test_once(self):
        first = run({"c.desktop": entry("Exec=/usr/share/cursor/cursor %F")})[1]["c.desktop"]
        self.assertEqual(run({"c.desktop": first}), (0, {"c.desktop": first}))

    def test_other_apps_untouched(self):
        other = entry("Exec=/usr/bin/firefox %u", name="Firefox")
        code, out = run({"firefox.desktop": other, "cursor.desktop": entry("Exec=/usr/share/cursor/cursor %F")})
        self.assertEqual(out["firefox.desktop"], other)
        self.assertEqual(code, 0)

    def test_cursor_launcher_running_something_else(self):
        code, out = run({"c.desktop": entry("Exec=/usr/bin/some-wrapper %F")})
        self.assertEqual(code, 1)
        self.assertNotIn(FLAG, out["c.desktop"])


if __name__ == "__main__":
    unittest.main()
