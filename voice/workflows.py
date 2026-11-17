"""A Claude session's workflows (multi-agent runs), for the phone (the user's wish, 2026-11-17: watch them from
afar, as /workflows does in the terminal): read from the files Claude Code keeps for each run.

- <session dir>/subagents/workflows/<run>/journal.jsonl: "launched", then per agent "started" {agentId, label,
  phase} and "result" {agentId, result}, live.
- <session dir>/workflows/<run>.json: written when the run ends ({script, result, agentCount, logs, ...}).
- <session dir>/workflows/scripts/<name>-<run>.js: the script; its meta names the run and its phases.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

PROJECTS = Path.home() / ".claude/projects"
STALE = 1800  # seconds without a journal line before a run without an end record counts as stopped
RESULT_CHARS = 1200


def _session_dir(session: str, root: Path = PROJECTS) -> Path | None:
    uuid = session.removeprefix("claude-")
    if not re.fullmatch(r"[0-9a-f-]{36}", uuid):
        return None
    found = [d for d in root.glob(f"*/{uuid}") if d.is_dir()]
    return found[0] if len(found) == 1 else None


def _meta(script: str) -> tuple[str, list[str]]:
    """The run's name and its phases' titles, from the script's meta literal."""
    name = re.search(r"name\s*:\s*['\"]([^'\"]+)['\"]", script)
    head = script[:script.find("}\n")] if "}\n" in script else script[:4000]
    phases = re.findall(r"title\s*:\s*['\"]([^'\"]+)['\"]", head)
    return (name.group(1) if name else "workflow"), phases


def _run(folder: Path, sdir: Path) -> dict:
    run_id = folder.name
    record_path = sdir / "workflows" / f"{run_id}.json"
    try:
        record = json.loads(record_path.read_text()) if record_path.exists() else {}
    except (OSError, ValueError):
        record = {}
    script = str(record.get("script") or "")
    if not script:
        found = list((sdir / "workflows/scripts").glob(f"*-{run_id}.js"))
        try:
            script = found[0].read_text()[:20000] if found else ""
        except OSError:
            script = ""
    name, titles = _meta(script)
    agents: dict[str, dict] = {}
    journal = folder / "journal.jsonl"
    try:
        lines = journal.read_text(errors="replace").splitlines()
        changed = journal.stat().st_mtime
    except OSError:
        lines, changed = [], 0.0
    for raw in lines:
        try:
            event = json.loads(raw)
        except ValueError:
            continue
        aid = str(event.get("agentId", ""))
        kind = event.get("type")
        if kind == "started" and aid:
            agents[aid] = {"id": aid, "label": str(event.get("label") or aid[:8]), "phase": str(event.get("phase") or ""),
                           "status": "running", "result": ""}
        elif aid in agents and kind == "result":
            result = event.get("result")
            text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
            agents[aid].update(status="done" if text else "empty", result=(text or "")[:RESULT_CHARS])
        elif aid in agents and kind in ("error", "failed", "skipped"):
            agents[aid].update(status="failed" if kind != "skipped" else "skipped",
                               result=str(event.get("error") or event.get("reason") or kind)[:RESULT_CHARS])
    ended = bool(record)
    state = "done" if ended else ("running" if time.time() - changed < STALE else "stopped")
    if ended and (record.get("error") or any(a["status"] == "failed" for a in agents.values())):
        state = "failed"
    if not ended:  # an agent still "running" in a stopped run never finished
        for a in agents.values():
            if a["status"] == "running" and state == "stopped":
                a["status"] = "stopped"
    order = titles + [a["phase"] for a in agents.values() if a["phase"] not in titles]
    phases = [{"title": t, "agents": [a for a in agents.values() if a["phase"] == t]}
              for t in dict.fromkeys(order)]
    phases = [p for p in phases if p["agents"] or p["title"] in titles]
    current = next((p["title"] for p in phases if any(a["status"] == "running" for a in p["agents"])), "")
    return {"id": run_id, "name": name, "state": state, "phase": current, "phases": phases,
            "done": sum(a["status"] in ("done", "empty") for a in agents.values()), "total": len(agents),
            "logs": [str(x)[:300] for x in (record.get("logs") or [])][-5:], "changed": changed}


def runs(session: str, most: int = 5, root: Path = PROJECTS) -> list[dict]:
    """That Claude session's newest workflow runs, newest first ([] for any other agent)."""
    sdir = _session_dir(session, root)
    if sdir is None:
        return []
    folders = [f for f in (sdir / "subagents/workflows").glob("wf_*") if f.is_dir()]

    def changed(f: Path) -> float:
        try:
            return (f / "journal.jsonl").stat().st_mtime
        except OSError:
            return 0.0

    return [_run(f, sdir) for f in sorted(folders, key=changed, reverse=True)[:most]]


def live(session: str, root: Path = PROJECTS) -> dict | None:
    """The run going on now in that session, briefly (for the strip at the bottom of its chat), or None."""
    for run in runs(session, 2, root):
        if run["state"] == "running":
            return {k: run[k] for k in ("id", "name", "phase", "done", "total")} | {
                "phases": len(run["phases"]),
                "at": next((i + 1 for i, p in enumerate(run["phases"]) if p["title"] == run["phase"]), 0)}
    return None
