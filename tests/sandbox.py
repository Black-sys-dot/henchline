"""Imported first by every unit test: HOME is a throwaway folder, so nothing reads or writes the real
~/.local/state, ~/.config or ~/Downloads, and the repository's modules are importable."""

import os
import sys
import tempfile
from pathlib import Path

HOME = tempfile.mkdtemp(prefix="henchline-tests-")
os.environ["HOME"] = HOME
REPO = Path(__file__).resolve().parents[1]
for path in (REPO / "handsfree/core", REPO / "call/hooks", REPO):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
assert Path.home() == Path(HOME), "HOME must be swapped before anything reads it"
