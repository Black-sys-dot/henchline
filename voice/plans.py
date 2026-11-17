"""A chat's plan document, for the phone.

Cursor keeps one markdown file per plan in ~/.cursor/plans, with a to-do list in the header (pending, in progress,
done) and a mermaid diagram in the body. The phone shows the steps and how many are done. Claude and Codex use the
same file shape when they have one: a *.plan.md in the project, or in ~/.claude/plans or ~/.codex/plans.
"""

from __future__ import annotations

import re
import urllib.parse
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
    if not todos:  # a plan written as Markdown (Claude's): its checkboxes are its steps
        for box in re.finditer(r"^\s*[-*]\s+\[([ xX~])\]\s+(.+)$", body, re.M):
            todos.append({"id": str(len(todos) + 1), "content": box.group(2).strip(),
                          "status": "completed" if box.group(1) in "xX" else "in_progress" if box.group(1) == "~" else "pending"})
    if not name:
        heading = re.search(r"^#\s+(.+)$", body, re.M)
        name = heading.group(1).strip() if heading else "Plan"
    done = sum(1 for step in todos if step["status"] == "completed")
    return {"name": name, "overview": overview, "todos": todos, "done": done, "total": len(todos),
            "body": body.strip()[:6000], "html": html(body.strip()[:20000]), "blocks": blocks(body.strip()[:40000], name)}


def html(markdown: str) -> str:
    """The plan's Markdown as the little HTML a phone's text view shows (headings, lists, bold, code); a diagram
    block becomes a note, as the phone doesn't draw it."""
    import html as escape

    out, code, fence = [], [], None
    for line in markdown.splitlines():
        if line.strip().startswith("```"):
            if fence is None:
                fence = line.strip()[3:].strip().lower()
                code = []
            else:
                if fence == "mermaid":
                    out.append("<p><i>(a diagram: see it on the laptop)</i></p>")
                else:
                    out.append("<p><tt>" + "<br>".join(escape.escape(c) for c in code) + "</tt></p>")
                fence = None
            continue
        if fence is not None:
            code.append(line)
            continue
        text = escape.escape(line.rstrip())
        spans: list[str] = []  # code first, kept out of the bold and italics (`a*b*c` stays as written)
        text = re.sub(r"`([^`]+)`", lambda m: spans.append(m.group(1)) or f"\x00{len(spans) - 1}\x00", text)
        text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
        text = re.sub(r"(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?!\w)", r"<i>\1</i>", text)
        text = re.sub("\x00(\\d+)\x00", lambda m: f"<tt>{spans[int(m.group(1))]}</tt>", text)
        heading = re.match(r"(#{1,6})\s+(.*)", text)
        bullet = re.match(r"\s*(?:[-*]|\d+[.)])\s+(?:\[([ xX~])\]\s+)?(.*)", text)
        if heading:
            size = {1: "big", 2: "big"}.get(len(heading.group(1)), "")
            inner = f"<b>{heading.group(2)}</b>"
            out.append(f"<p><{size}>{inner}</{size}></p>" if size else f"<p>{inner}</p>")
        elif bullet:
            mark = {"x": "✓ ", "X": "✓ ", "~": "● ", " ": "○ "}.get(bullet.group(1) or "", "• ")
            out.append(f"&nbsp;&nbsp;{mark}{bullet.group(2)}<br>")
        elif text.strip():
            out.append(text + "<br>")
        else:
            out.append("<br>")
    return "".join(out)


def _inline(text: str) -> str:
    """One line's Markdown as the small HTML a phone's text view shows: code first (kept literal), then bold,
    italics and links. Everything escaped before any tag goes in."""
    import html as escape

    text = escape.escape(text)
    spans: list[str] = []
    text = re.sub(r"`([^`]+)`", lambda m: spans.append(m.group(1)) or f"\x00{len(spans) - 1}\x00", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?!\w)", r"<i>\1</i>", text)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2">\1</a>', text)
    return re.sub("\x00(\\d+)\x00", lambda m: f"<tt>{spans[int(m.group(1))]}</tt>", text)


def blocks(markdown: str, title: str = "") -> list[dict]:
    """The plan's Markdown as blocks the phone lays out one view each (the design review, 2026-11-17): headings,
    paragraphs, lists (depth, checkbox), code, diagrams (their node labels), tables, rules. The first heading
    is left out when it repeats the plan's name."""
    out: list[dict] = []
    lines = markdown.splitlines()
    i = 0
    para: list[str] = []

    def flush() -> None:
        if para:
            out.append({"t": "p", "html": _inline(" ".join(x.strip() for x in para))})
            para.clear()

    while i < len(lines):
        line = lines[i]
        fence = re.match(r"\s*```\s*([\w+-]*)", line)
        if fence:
            flush()
            lang, code = fence.group(1).lower(), []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1
            if lang == "mermaid":
                nodes = re.findall(r"[\[(\{]\s*\"?([^\]\)\}\"]{2,40})\"?\s*[\])\}]", "\n".join(code))
                out.append({"t": "diagram", "kind": "mermaid", "nodes": list(dict.fromkeys(n.strip() for n in nodes))[:6]})
            else:
                out.append({"t": "code", "lang": lang, "text": "\n".join(code)[:6000]})
            continue
        heading = re.match(r"(#{1,6})\s+(.*)", line)
        if heading:
            flush()
            words = heading.group(2).strip()
            if not (not out and title and words.lower() == title.lower()):
                out.append({"t": "h", "level": len(heading.group(1)), "html": _inline(words)})
            i += 1
            continue
        if re.match(r"\s*\|.*\|\s*$", line):
            flush()
            rows = []
            while i < len(lines) and re.match(r"\s*\|.*\|\s*$", lines[i]):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                    rows.append([_inline(c) for c in cells])
                i += 1
            if rows:
                out.append({"t": "table", "head": rows[0], "rows": rows[1:60]})
            continue
        item = re.match(r"(\s*)(?:[-*+]|\d+[.)])\s+(?:\[([ xX~])\]\s+)?(.*)", line)
        if item:
            flush()
            items, ordered = [], bool(re.match(r"\s*\d", line))
            while i < len(lines) and (item := re.match(r"(\s*)(?:[-*+]|\d+[.)])\s+(?:\[([ xX~])\]\s+)?(.*)", lines[i])):
                check = item.group(2)
                items.append({"html": _inline(item.group(3).strip()), "depth": min(len(item.group(1).expandtabs(4)) // 2, 4),
                              "check": None if check is None else ("x" if check in "xX" else check)})
                i += 1
            out.append({"t": "list", "ordered": ordered, "items": items})
            continue
        if re.fullmatch(r"\s*(-{3,}|\*{3,}|_{3,})\s*", line):
            flush()
            out.append({"t": "rule"})
            i += 1
            continue
        if line.strip():
            para.append(line)
        else:
            flush()
        i += 1
    flush()
    return out[:400]


def for_session(session: str, home: Path | None = None) -> Path | None:
    """The plan file that chat itself wrote or opened, from the agent's own records (never guessed by name):
    Claude logs planFilePath in its session log; Cursor links an editor chat to its plan in its store. (A
    plan path a transcript merely mentions is not the chat's own: the CLI read another project's plan once.)"""
    import contextlib
    import json
    import sqlite3

    home = home or Path.home()
    key = session.split("-", 1)[1] if "-" in session else ""
    if session.startswith("claude-") and re.fullmatch(r"[0-9a-f-]{36}", key):
        for log in (home / ".claude/projects").glob(f"*/{key}.jsonl"):
            try:
                with log.open("rb") as f:
                    f.seek(max(0, log.stat().st_size - 8 * 1024 * 1024))
                    found = re.findall(rb'"planFilePath":"([^"]+)"', f.read())
            except OSError:
                continue
            if found:
                try:
                    path = Path(json.loads(b'"' + found[-1] + b'"'))  # a JSON string: its escapes undone
                except ValueError:
                    continue
                return path if path.is_file() else None
        return None
    if not session.startswith("cursor-") or not re.fullmatch(r"[0-9a-f-]{36}", key):
        return None
    try:
        store = home / ".config/Cursor/User/globalStorage/state.vscdb"
        with contextlib.closing(sqlite3.connect(f"file:{store}?mode=ro", uri=True, timeout=1)) as db:
            rows = db.execute("select key from cursorDiskKV where key like ?", (f"ofsContent:{key}:%plan.md",)).fetchall()
        for (row,) in reversed(rows):
            path = Path(urllib.parse.unquote(row.split(":file://", 1)[-1]))
            if path.is_file():
                return path
    except sqlite3.Error:
        pass
    return None


def of_session(session: str, project: Path | None) -> dict | None:
    """That chat's own plan, parsed; else, as before, the newest plan naming its project."""
    path = for_session(session)
    if path is None:  # only a plan that names its project, never the newest one anywhere
        return find(project) if project else None
    try:
        plan = parse(path.read_text(errors="replace"))
    except OSError:
        return None
    plan["file"] = str(path)
    return plan


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
