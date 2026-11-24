"""A daily share of a monthly plan, so a small plan lasts the month.

The monthly plan (in dollars) and the day it renews are fixed for the person,
not a setting they can change (the user's call, 24 Nov): they come from
~/.mesh-code/plan.json, or MESH_CODE_MONTHLY_PLAN_USD / MESH_CODE_PLAN_RESET_DAY
in the environment. No plan = off. Each day gets an even share of what is left:

    today's share = (plan - spent this period before today) / days left

counting today. A quiet day leaves more for the days after it; a heavy day
leaves less. Once today's cloud spend reaches today's share, cloud models
are off until tomorrow and work carries on, free, on the local model (the
user's rule, 23 Nov: "the moment people are out of their day's limit they
can code but using free models only").

Only cloud steps count, at what the gateway billed (or the estimate when the
bill is missing). Local steps are free and never count.

The ledger is one small JSON file shared by every window of the app. It is
display-and-gate state, not money: Mesh bills the account as before.
"""
from __future__ import annotations

import calendar
import json
import os
import tempfile
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

HARD_STOP_SHARES = 2.0   # a task running past the line stops between steps at twice the share


def _add_month(d: date, reset_day: int) -> date:
    y, m = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
    return date(y, m, min(reset_day, calendar.monthrange(y, m)[1]))


def period_for(today: date, reset_day: int) -> "tuple[date, date]":
    """(first day, first day of the next period) for the period holding
    `today`. A reset day past the end of a short month falls on its last day."""
    reset_day = max(1, min(28, int(reset_day or 1)))
    this_month = date(today.year, today.month, reset_day)
    if today >= this_month:
        start = this_month
    else:
        y, m = (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)
        start = date(y, m, reset_day)
    return start, _add_month(start, reset_day)


class FixedPlan:
    """Where the fixed plan comes from, read fresh each time (cheap): the
    environment first, then ~/.mesh-code/plan.json. Looks like a dict to
    DailyCap, so tests can pass a plain one."""

    ENV = {"monthly_plan_usd": "MESH_CODE_MONTHLY_PLAN_USD",
           "plan_reset_day": "MESH_CODE_PLAN_RESET_DAY"}

    def __init__(self, path: "Path | None" = None):
        from . import config as config_mod
        self.path = Path(path) if path else config_mod.CONFIG_DIR / "plan.json"

    def get(self, key: str, default=None):
        env = os.environ.get(self.ENV.get(key, ""), "").strip()
        if env:
            return env
        try:
            d = json.loads(self.path.read_text())
            if isinstance(d, dict) and d.get(key) is not None:
                return d[key]
        except (OSError, ValueError):
            pass
        return default


class DailyCap:
    def __init__(self, cfg=None, path: "Path | None" = None, *, now=None):
        from . import config as config_mod
        self.cfg = cfg if cfg is not None else FixedPlan()
        self.path = Path(path) if path else config_mod.CONFIG_DIR / "daily_cap.json"
        self._now = now or time.time
        self._lock = threading.RLock()
        self.data = self._load()

    # ---- settings ---------------------------------------------------------
    def plan(self) -> float:
        try:
            return max(0.0, float(self.cfg.get("monthly_plan_usd") or 0))
        except (TypeError, ValueError):
            return 0.0

    def enabled(self) -> bool:
        return self.plan() > 0

    def _today(self) -> date:
        return datetime.fromtimestamp(self._now()).date()   # the computer's own day

    # ---- ledger -------------------------------------------------------------
    def _load(self) -> dict:
        try:
            d = json.loads(self.path.read_text())
            if isinstance(d, dict):
                return d
        except (OSError, ValueError):
            pass
        return {}

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".daily_cap")
            with os.fdopen(fd, "w") as f:
                json.dump(self.data, f)
            os.replace(tmp, self.path)
        except OSError:
            pass

    def _roll(self) -> None:
        """Start a new day or a new period when the date has moved on. The
        day's share is fixed when the day starts, so spending today cannot
        shrink today's share while you work."""
        # Re-read first: every app window (and every running copy of the app)
        # writes the same file, so what is in memory may be behind.
        fresh = self._load()
        if fresh or not self.path.exists():
            self.data = fresh
        today = self._today()
        try:
            reset_day = int(self.cfg.get("plan_reset_day", 1) or 1)
        except (TypeError, ValueError):
            reset_day = 1
        start, end = period_for(today, reset_day)
        d = self.data
        if d.get("period_start") != start.isoformat():
            d.clear()
            d.update({"period_start": start.isoformat(), "spent_period": 0.0})
        if d.get("day") != today.isoformat() or d.get("plan") != self.plan():
            before_today = max(0.0, float(d.get("spent_period", 0.0)) - (
                float(d.get("spent_day", 0.0)) if d.get("day") == today.isoformat() else 0.0))
            days_left = max(1, (end - today).days)
            spent_day = float(d.get("spent_day", 0.0)) if d.get("day") == today.isoformat() else 0.0
            d.update({"day": today.isoformat(), "spent_day": spent_day, "plan": self.plan(),
                      "share": max(0.0, self.plan() - before_today) / days_left,
                      "resets": end.isoformat()})
            self._save()

    def record(self, cost_usd: float) -> None:
        """Add one cloud step's cost. Local steps never come here."""
        cost = max(0.0, float(cost_usd or 0))
        if not cost:
            return
        with self._lock:
            self._roll()
            self.data["spent_period"] = round(float(self.data.get("spent_period", 0.0)) + cost, 8)
            self.data["spent_day"] = round(float(self.data.get("spent_day", 0.0)) + cost, 8)
            self._save()

    # ---- the gate -------------------------------------------------------------
    def share(self) -> float:
        with self._lock:
            self._roll()
            return float(self.data.get("share", 0.0))

    def used_today(self) -> float:
        with self._lock:
            self._roll()
            return float(self.data.get("spent_day", 0.0))

    def capped(self) -> bool:
        """Today's share is used: cloud models wait for tomorrow."""
        if not self.enabled():
            return False
        return self.used_today() >= self.share()

    def past_hard_stop(self) -> bool:
        """A task that kept going past the line has now spent twice today's
        share: it stops between steps."""
        if not self.enabled():
            return False
        return self.used_today() >= HARD_STOP_SHARES * self.share()

    def snapshot(self) -> dict:
        """What the app shows: shares, never money."""
        if not self.enabled():
            return {"enabled": False}
        with self._lock:
            self._roll()
            share = float(self.data.get("share", 0.0))
            used = float(self.data.get("spent_day", 0.0))
            pct = 100.0 if share <= 0 else min(999.0, 100.0 * used / share)
            tomorrow = self._today() + timedelta(days=1)
            return {"enabled": True, "used_pct": round(pct, 1),
                    "left_pct": round(max(0.0, 100.0 - pct), 1),
                    "capped": used >= share, "next_share_on": tomorrow.isoformat(),
                    "month_resets_on": self.data.get("resets")}
