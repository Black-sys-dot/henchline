#!/usr/bin/env python3
"""Add or remove call/mirror.py as a Claude Code hook in ~/.claude/settings.json (install.sh call does this).

    call/claude_hooks.py add | remove

Only this repository's own hook entries are touched; anything else in the settings stays as it was.
"""
import json
import sys
from pathlib import Path

SETTINGS = Path.home() / ".claude/settings.json"
MIRROR = str(Path(__file__).resolve().parent / "mirror.sh")
OLD = (str(Path(__file__).resolve().parent / "mirror.py"),)  # what earlier versions added
EVENTS = ("UserPromptSubmit", "Stop")


def main() -> int:
    if sys.argv[1:] not in (["add"], ["remove"]):
        print(__doc__)
        return 1
    settings = json.loads(SETTINGS.read_text()) if SETTINGS.exists() else {}
    hooks = settings.setdefault("hooks", {})
    for event in EVENTS:
        kept = [group for group in hooks.get(event, [])
                if not any(h.get("command") in (MIRROR, *OLD) for h in group.get("hooks", []))]
        if sys.argv[1] == "add":
            kept.append({"hooks": [{"type": "command", "command": MIRROR, "timeout": 5}]})
        if kept:
            hooks[event] = kept
        else:
            hooks.pop(event, None)
    if not hooks:
        settings.pop("hooks")
    target = SETTINGS.resolve()  # a linked settings file stays linked: its target is what changes
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(settings, indent=2) + "\n")
    if target.exists():
        tmp.chmod(target.stat().st_mode & 0o777)  # it can hold tokens: as private as it was
    tmp.replace(target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
