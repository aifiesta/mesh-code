"""The project journal — a product manager's notebook the agent keeps.

The recurring failure this exists to stop: a session restarts, the model has
no idea the project already works, and it REBUILDS it — re-scaffolding,
re-installing, re-fighting the same port problem it solved yesterday. Repo
memory (memory.py) stores structure — symbols and file maps. The journal
stores *operational* knowledge, the things a colleague would tell you at
handover:

  - HOW TO RUN IT (the exact commands + URLs that actually worked)
  - WHAT'S BUILT (features finished, in plan-checklist form)
  - LEARNINGS (gotchas, decisions, workarounds — fed by the remember tool)
  - RECENT WORK (a dated line per turn)

It is one markdown file per workspace under ~/.mesh-code/journal/ — outside
the repo, so nothing pollutes the user's git status — rendered in the app's
Journal panel for the user, and injected (bounded) into the system prompt at
session start so even a small model opens knowing "vite runs on :4000 via
`cd frontend && npm run dev`" instead of discovering it through four failed
attempts.

Everything here is best-effort: a journal bug must never break a turn.
"""
from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path

from . import config

JOURNAL_DIR = config.CONFIG_DIR / "journal"
INJECT_CAP = 4_000          # chars of journal injected into the system prompt
RECENT_CAP = 30             # dated turn lines kept
LEARNINGS_CAP = 60          # learnings kept (oldest dropped)

_SECTIONS = ("How to run", "What's built", "Learnings", "Recent work")


def path_for(workspace) -> Path:
    ws = str(Path(workspace).expanduser().resolve())
    digest = hashlib.sha256(ws.encode("utf-8", "replace")).hexdigest()[:16]
    name = Path(ws).name or "project"
    safe = "".join(c for c in name if c.isalnum() or c in "-_")[:40]
    return JOURNAL_DIR / f"{safe or 'project'}-{digest}.md"


def load(workspace) -> str:
    try:
        return path_for(workspace).read_text()
    except OSError:
        return ""


def _parse(text: str, workspace: str) -> dict:
    """Split the markdown into our sections; unknown text is preserved
    under the section it appeared in."""
    out: dict = {s: [] for s in _SECTIONS}
    if not text:
        return out
    current = None
    for line in text.splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            current = m.group(1) if m.group(1) in _SECTIONS else None
            continue
        if line.startswith("# "):
            continue
        if current is not None and line.strip() != "_nothing yet_":
            out[current].append(line)
    for k in out:
        # strip leading/trailing blank lines inside each section
        while out[k] and not out[k][0].strip():
            out[k].pop(0)
        while out[k] and not out[k][-1].strip():
            out[k].pop()
    return out


def _render(workspace: str, sections: dict) -> str:
    name = Path(str(workspace)).name or "project"
    parts = [f"# {name} — project journal",
             "",
             "_Kept by Mesh Code. How this project runs, what's built, and "
             "what was learned — so any session (and any model) can pick up "
             "where the last one left off._",
             ""]
    for s in _SECTIONS:
        body = sections.get(s) or []
        parts.append(f"## {s}")
        parts.append("")
        if body:
            parts.extend(body)
        else:
            parts.append("_nothing yet_")
        parts.append("")
    return "\n".join(parts)


def _save(workspace, sections: dict) -> None:
    p = path_for(workspace)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(_render(str(workspace), sections))
    tmp.replace(p)


def record_turn(workspace, *, plan: "dict | None", servers: list,
                files_written: list, model: str, ok: bool,
                summary: "str | None" = None) -> None:
    """Fold one finished turn into the journal. Called at TURN_FINISHED."""
    try:
        sections = _parse(load(workspace), str(workspace))

        # -- How to run: every live server's exact command + URL. These are
        # PROVEN commands — they are literally running — which is exactly
        # what the next session needs to not rediscover.
        if servers:
            lines = []
            for s in servers:
                lines.append(f"- `{s.get('cmd', '')}` → {s.get('url', '')}")
            sections["How to run"] = lines

        # -- What's built: mirror the plan as a checklist. A plan IS the
        # sprint board; syncing it here survives the session.
        if plan and plan.get("steps"):
            done_marks = []
            for st in plan["steps"]:
                mark = "x" if st.get("status") == "completed" else " "
                done_marks.append(f"- [{mark}] {st.get('title', '')}")
            stamp = time.strftime("%Y-%m-%d")
            sections["What's built"] = (
                [f"_Plan as of {stamp}:_", ""] + done_marks)

        # -- Recent work: one dated line per turn, newest first.
        stamp = time.strftime("%Y-%m-%d %H:%M")
        wrote = f", wrote {len(files_written)} file(s)" if files_written else ""
        state = "ok" if ok else "stopped"
        line = f"- {stamp} · {model} · {state}{wrote}"
        if summary:
            line += f" — {summary[:120]}"
        recent = [line] + (sections.get("Recent work") or [])
        sections["Recent work"] = recent[:RECENT_CAP]

        _save(workspace, sections)
    except Exception:
        pass


def note(workspace, text: str) -> None:
    """A learning from the remember tool — the PM notebook's core entries."""
    try:
        text = (text or "").strip()
        if not text:
            return
        sections = _parse(load(workspace), str(workspace))
        entry = f"- {text}"
        existing = sections.get("Learnings") or []
        if entry not in existing:
            sections["Learnings"] = (existing + [entry])[-LEARNINGS_CAP:]
            _save(workspace, sections)
    except Exception:
        pass


def prompt_block(workspace) -> str:
    """The journal, bounded, for injection into the system prompt."""
    try:
        text = load(workspace)
        if not text.strip():
            return ""
        if len(text) > INJECT_CAP:
            # Keep the head (how-to-run + what's-built lead) — the most
            # operationally valuable part — and say we cut it.
            text = text[:INJECT_CAP] + "\n…(journal truncated)"
        return (
            "PROJECT JOURNAL — what previous sessions in this project "
            "learned. TRUST IT: if it says how to run something, run that "
            "command instead of rediscovering it; if it says a feature is "
            "built, verify by running, not by rebuilding.\n\n" + text
        )
    except Exception:
        return ""
