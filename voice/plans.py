"""A chat's plan document, for the phone.

Cursor keeps one markdown file per plan in ~/.cursor/plans, with a to-do list in the header (pending, in progress,
done) and a mermaid diagram in the body. The phone shows the steps and how many are done. Claude and Codex use the
same file shape when they have one: a *.plan.md in the project, or in ~/.claude/plans or ~/.codex/plans.
"""

from __future__ import annotations

import re
from pathlib import Path

STATUSES = {"pending", "in_progress", "completed"}


def parse(text: str) -> dict:
    """The plan's name, overview, and steps. The mermaid stays in the body; the phone lists the steps."""
    front, body = "", text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            front, body = text[4:end], text[end + 4:]
    name = _field(front, "name")
    overview = _field(front, "overview")
    todos, current = [], None
    for line in front.splitlines():
        item = re.match(r"\s*-\s+id:\s*(.+)", line)
        if item:
            current = {"id": item.group(1).strip().strip("\"'"), "content": "", "status": "pending"}
            todos.append(current)
            continue
        if current is None:
            continue
        content = re.match(r"\s+content:\s*(.*)", line)
        status = re.match(r"\s+status:\s*(\w+)", line)
        if content:
            current["content"] = content.group(1).strip().strip("\"'")
        elif status and status.group(1) in STATUSES:
            current["status"] = status.group(1)
    if not name:
        heading = re.search(r"^#\s+(.+)$", body, re.M)
        name = heading.group(1).strip() if heading else "Plan"
    done = sum(1 for step in todos if step["status"] == "completed")
    return {"name": name, "overview": overview, "todos": todos, "done": done, "total": len(todos),
            "body": body.strip()[:6000]}


def _field(front: str, key: str) -> str:
    found = re.search(rf"^{key}:\s*(.*)$", front, re.M)
    return found.group(1).strip().strip("\"'") if found else ""


def _places(project: Path | None) -> list[Path]:
    home = Path.home()
    found = [home / ".cursor/plans"]
    for folder in (home / ".claude/plans", home / ".codex/plans"):
        found.append(folder)
    if project:
        found.append(project)
    return found


def find(project: Path | None) -> dict | None:
    """The newest plan that belongs to that project. A plan belongs when it names the project, or it lives in the
    project. With no project, the newest plan anywhere these are kept."""
    files = []
    for folder in _places(project):
        if not folder.is_dir():
            continue
        files += [path for path in folder.glob("*.plan.md") if path.is_file()]
    if not files:
        return None
    mark = ""
    if project:
        mark = project.name.lower()
        named = []
        for path in files:
            try:
                text = path.read_text(errors="replace")[:40000]
            except OSError:
                continue
            if mark and (mark in text.lower() or str(project) in text) or path.parent == project:
                named.append((path, text))
        if not named:
            return None
        path, text = max(named, key=lambda item: item[0].stat().st_mtime)
    else:
        path = max(files, key=lambda item: item.stat().st_mtime)
        try:
            text = path.read_text(errors="replace")
        except OSError:
            return None
    plan = parse(text)
    plan["file"] = str(path)
    return plan


def approval(plan: dict) -> str:
    """What the agent is told when the user taps build. It names the file, so the agent updates that document."""
    return (f"The plan \"{plan['name']}\" is approved. Build it, and keep the plan document updated as you go "
            f"({plan['file']}): mark each step pending, in progress, or done.")
