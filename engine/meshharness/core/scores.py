"""Model scores for Smart routing: one entry per model AND thinking level.

`model_scores.json` (bundled next to this file) replaces the single 0-100
capability number in `routing_table.json` with figures in real units, for a
short list of models that each earn their place:

    everyday   share of ordinary build-and-fix tasks done correctly, 0-100
    hard       share of long, difficult tasks done to a mergeable standard
    margin     how far the true figure may lie from the score; callers rank
               on `score - margin`, so thin evidence ranks lower by itself
    cost_per_task / minutes_per_task   typical spend and wait for one task

Every number comes from third-party tests, per thinking level where they
were run that way. The model list, prices and availability still come from
the gateway live — this file only says how good an entry is.

This module only loads and flattens the file. Nothing in the router reads it
yet; the pick moves over to it in a later change.
"""
from __future__ import annotations

import json
from pathlib import Path

# Thinking levels, weakest to strongest. "default" = send no level at all
# (the only tested configuration for models that expose no setting).
LEVELS = ("default", "none", "low", "medium", "high", "xhigh", "max")

_CACHE: "dict | None" = None


def load(path: "str | None" = None) -> "dict | None":
    """The bundled scoring file; None when absent or unusable."""
    global _CACHE
    if _CACHE is not None and path is None:
        return _CACHE
    try:
        p = Path(path) if path else Path(__file__).parent / "model_scores.json"
        doc = json.loads(p.read_text())
        if not isinstance(doc, dict) or not isinstance(doc.get("models"), dict):
            return None
        if path is None:
            _CACHE = doc
        return doc
    except Exception:
        return None


def _usable(e) -> bool:
    try:
        return (isinstance(e, dict) and e.get("sendable") is True
                and float(e["everyday"]["score"]) >= 0
                and float(e["hard"]["score"]) >= 0
                and float(e["cost_per_task"]) > 0)
    except (KeyError, TypeError, ValueError):
        return False


def entries(doc: "dict | None", available=None, exclude=()) -> list:
    """Flat list of entries the engine may request, weakest level first
    within each model.

    `available` — model ids the gateway offers right now; an entry for a
    model that has left the catalog is dropped (None = don't filter).
    `exclude` — the user's never-pick list.

    Each entry: model, name, level, effort (what to send as the thinking
    level, None = send nothing), everyday, hard, cost_per_task,
    minutes_per_task. Entries that cannot be requested as tested, or that
    lack a number the pick needs, are left out rather than guessed at.
    """
    if not doc:
        return []
    avail = set(available) if available is not None else None
    skip = set(exclude or ())
    out = []
    for mid, m in (doc.get("models") or {}).items():
        if mid in skip or (avail is not None and mid not in avail):
            continue
        levels = (m or {}).get("levels") or {}
        for level in LEVELS:
            e = levels.get(level)
            if not _usable(e):
                continue
            out.append({
                "model": mid,
                "name": m.get("name") or mid,
                "level": level,
                "effort": None if level == "default" else level,
                "everyday": e["everyday"],
                "hard": e["hard"],
                "cost_per_task": float(e["cost_per_task"]),
                "minutes_per_task": e.get("minutes_per_task"),
            })
    return out


def levels(doc: "dict | None", model: str) -> list:
    """Requestable thinking levels with a score for one model, weakest first."""
    return [e["level"] for e in entries(doc) if e["model"] == model]
