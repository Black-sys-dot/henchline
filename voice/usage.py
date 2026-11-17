"""How much each agent has used, for the phone's /usage (the user's wish, 2026-11-17: what Claude's /usage and
/context show in its terminal, as bars on the phone): its plan's limits and its context, from what each agent
already writes down, never from its screen.

- Claude: its status line's report (agents/claude-status.py): the 5-hour and weekly limits, the context, the cost.
- Codex: the last token count in its session log: the 5-hour and weekly limits, the context.
- Cursor: its own record of the chat: the context, and what fills it. Its plan's limits aren't kept on the laptop.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

CLAUDE_STATUS = Path.home() / ".local/state/ptt/claude-status"
CODEX_SESSIONS = Path.home() / ".codex/sessions"


def _limit(label: str, percent, resets) -> dict | None:
    if not isinstance(percent, (int, float)):
        return None
    return {"label": label, "percent": round(float(percent), 1), "resets": int(resets) if isinstance(resets, (int, float)) else 0}


def _window_name(minutes) -> str:
    if minutes == 300:
        return "5-hour session"
    if minutes == 10080:
        return "Week"
    if isinstance(minutes, (int, float)) and minutes:
        return f"{int(minutes) // 60} hours" if minutes < 2880 else f"{int(minutes) // 1440} days"
    return "Limit"


def claude(session: str, status_dir: Path = CLAUDE_STATUS) -> dict:
    uuid = session.removeprefix("claude-")
    if not re.fullmatch(r"[0-9a-f-]{36}", uuid):
        return {}
    try:
        status = json.loads((status_dir / f"{uuid}.json").read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(status, dict):
        return {}
    limits = status.get("rate_limits") or {}
    window = status.get("context_window") or {}
    size, used = window.get("context_window_size"), window.get("used_percentage")
    current = window.get("current_usage") or {}
    tokens = sum(v for k, v in current.items() if k.endswith("input_tokens") and isinstance(v, int)) if isinstance(current, dict) else 0
    cost = (status.get("cost") or {}).get("total_cost_usd")
    return {
        "model": str((status.get("model") or {}).get("display_name", "")),
        "limits": [x for x in (_limit("5-hour session", (limits.get("five_hour") or {}).get("used_percentage"),
                                      (limits.get("five_hour") or {}).get("resets_at")),
                               _limit("Week", (limits.get("seven_day") or {}).get("used_percentage"),
                                      (limits.get("seven_day") or {}).get("resets_at"))) if x],
        "context": {"percent": float(used), "size": int(size or 0), "used": tokens, "parts": []}
                   if isinstance(used, (int, float)) else None,
        "notes": [f"This session has cost ${cost:,.2f} at API prices"] if isinstance(cost, (int, float)) else [],
    }


def _codex_log(folder_key: str, root: Path) -> Path | None:
    """The newest Codex session log in that folder (its key: the folder's path with odd characters as _)."""
    def mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    for log in sorted(root.glob("*/*/*/rollout-*.jsonl"), key=mtime, reverse=True)[:30]:
        try:
            with log.open("rb") as f:
                meta = json.loads(f.readline(1024 * 1024) or b"{}")
        except (OSError, ValueError):
            continue
        cwd = str((meta.get("payload") or {}).get("cwd", "")) if isinstance(meta, dict) else ""
        if cwd and re.sub(r"[^A-Za-z0-9_.-]+", "_", cwd)[-120:] == folder_key[-120:]:
            return log
    return None


def codex(session: str, root: Path = CODEX_SESSIONS) -> dict:
    log = _codex_log(session.removeprefix("codex-"), root)
    if not log:
        return {}
    last = None
    try:
        with log.open("rb") as f:
            for raw in f:
                if b'"token_count"' in raw:
                    last = raw
    except OSError:
        return {}
    try:
        payload = (json.loads(last) or {}).get("payload") or {} if last else {}
    except ValueError:
        payload = {}
    limits = payload.get("rate_limits") or {}
    info = payload.get("info") or {}
    size = info.get("model_context_window")
    used = ((info.get("last_token_usage") or {}).get("input_tokens")) if isinstance(info, dict) else None
    found = []
    for key in ("primary", "secondary"):
        window = limits.get(key) or {}
        if isinstance(window, dict):
            item = _limit(_window_name(window.get("window_minutes")), window.get("used_percent"), window.get("resets_at"))
            if item:
                found.append(item)
    context = None
    if isinstance(size, int) and size and isinstance(used, int):
        context = {"percent": round(100 * used / size, 1), "size": size, "used": used, "parts": []}
    plan = limits.get("plan_type")
    return {"model": "", "limits": found, "context": context,
            "notes": [f"Plan: {plan}"] if isinstance(plan, str) and plan else []}


def cursor(record: dict) -> dict:
    """From Cursor's own record of the chat (cursor_bridge.composer)."""
    if not record:
        return {}
    used, size, percent = record.get("contextTokensUsed"), record.get("contextTokenLimit"), record.get("contextUsagePercent")
    breakdown = record.get("promptTokenBreakdown") or {}
    parts = [{"label": str(c.get("label") or c.get("id") or "?"), "tokens": int(c.get("estimatedTokens") or 0)}
             for c in (breakdown.get("categories") or []) if isinstance(c, dict) and c.get("estimatedTokens")]
    context = None
    if isinstance(percent, (int, float)):
        context = {"percent": round(float(percent), 1), "size": int(size or 0), "used": int(used or 0), "parts": parts}
    model = (record.get("modelConfig") or {}).get("modelName") if isinstance(record.get("modelConfig"), dict) else ""
    return {"model": str(model or ""), "limits": [], "context": context,
            "notes": ["Cursor keeps its plan's limits on its website, not on the laptop"]}
