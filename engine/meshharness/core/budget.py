"""The spend governor: a monthly limit the user sets, today's share of what is
left, and a ledger of what each cloud tier actually cost. Every cloud hop asks
`check` first and `record`s after; local hops never touch it."""
from __future__ import annotations

import calendar
import json
import os
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from . import pricing
from .config import CONFIG_DIR

LEDGER = CONFIG_DIR / "budget.json"
WARN_STEPS = (50, 80, 100)
KINDS = ("escalate", "audit", "auto", "pinned", "fallback", "other")


@dataclass
class Hold:
    scope: str      # day | month | key_cap
    message: str


class Budget:
    def __init__(self, cfg: dict, path: Path = LEDGER, *, now=None):
        self.cfg = cfg
        self.path = path
        self._now = now or time.time
        self._lock = threading.RLock()
        self.data = self._load()
        self._roll()

    # ---- limits -------------------------------------------------------
    def monthly_limit(self) -> float:
        return max(0.0, float(self.cfg.get("monthly_budget_usd") or 0))

    def daily_limit(self) -> float:
        """What was left of the month when today began, spread over the days
        left: an underspent week raises it, an overspent one lowers it."""
        month = self.monthly_limit()
        if not month:
            return 0.0
        with self._lock:
            before_today = max(0.0, self.data["spent_usd"] - self._today()["spent"])
        d = datetime.fromtimestamp(self._now()).date()
        days_left = calendar.monthrange(d.year, d.month)[1] - d.day + 1
        return max(0.0, month - before_today) / days_left

    # ---- the gate -----------------------------------------------------
    def check(self, kind: str, estimate_usd: float) -> Hold | None:
        """Whether a cloud hop of this kind may start. Local hops never ask."""
        with self._lock:
            self._roll()
            if self.data.get("key_cap"):
                return Hold("key_cap", "your Mesh key's spend cap was reached; raise it at app.meshapi.ai")
            month = self.monthly_limit()
            if not month:
                if self.data.get("held"):
                    self.data["held"] = None
                    self._save()
                return None
            if self.data["spent_usd"] + estimate_usd > month:
                return self._hold("month", f"the monthly budget (${month:.2f}) is used up; local work continues")
            day = self.daily_limit()
            if self._today()["spent"] + estimate_usd > day and self.data.get("override_day") != self._day():
                return self._hold("day", f"today's share (${day:.2f}) is used up; allow more or wait for midnight")
            if self.data.get("held"):
                self.data["held"] = None
                self._save()
            return None

    def _hold(self, scope: str, message: str) -> Hold:
        # Remembered so the UI can offer "Allow more today" even when spent is
        # under the share and only the pre-flight estimate went over it.
        if self.data.get("held") != {"scope": scope, "day": self._day()}:
            self.data["held"] = {"scope": scope, "day": self._day()}
            self._save()
        return Hold(scope, message)

    def override_today(self) -> None:
        with self._lock:
            self.data["override_day"] = self._day()
            if (self.data.get("held") or {}).get("scope") == "day":
                self.data["held"] = None
            self._save()

    def clear_hold(self) -> None:
        """A new limit voids a hold made under the old one; the next hop re-checks."""
        with self._lock:
            if self.data.get("held"):
                self.data["held"] = None
                self._save()

    def set_key_cap(self, hit: bool) -> None:
        with self._lock:
            self.data["key_cap"] = bool(hit)
            self._save()

    # ---- the ledger ---------------------------------------------------
    def record(self, kind: str, cost_usd: float, *, estimated: bool = False) -> list[int]:
        """Add a settled hop; returns the warning thresholds crossed now."""
        kind = kind if kind in KINDS else "other"
        with self._lock:
            self._roll()
            cost = max(0.0, float(cost_usd or 0))
            self.data["spent_usd"] = round(self.data["spent_usd"] + cost, 6)
            self.data["by_kind"][kind] = round(self.data["by_kind"].get(kind, 0.0) + cost, 6)
            if estimated:
                self.data["estimated_usd"] = round(self.data.get("estimated_usd", 0.0) + cost, 6)
            today = self._today()
            today["spent"] = round(today["spent"] + cost, 6)
            today["by_kind"][kind] = round(today["by_kind"].get(kind, 0.0) + cost, 6)
            crossed = self._warnings(today)
            self._save()
            return crossed

    def snapshot(self) -> dict:
        with self._lock:
            self._roll()
            today = self._today()
            day, month = self.daily_limit(), self.monthly_limit()
            held = self.data.get("held") or {}
            return {
                "month": {"spent": round(self.data["spent_usd"], 4), "limit": month,
                          "pct": _pct(self.data["spent_usd"], month), "resets": self._month_reset(),
                          "by_kind": dict(self.data["by_kind"])},
                # A positive monthly limit with nothing left today is a full day, not "no limit".
                "day": {"spent": round(today["spent"], 4), "limit": round(day, 4),
                        "pct": _pct(today["spent"], day) if day else (100 if month else 0),
                        "resets": self._day_reset(),
                        "by_kind": dict(today["by_kind"]), "override": self.data.get("override_day") == self._day()},
                "key_cap": bool(self.data.get("key_cap")),
                "held": held.get("scope") if held.get("day") == self._day() else None,
                "estimated_usd": round(self.data.get("estimated_usd", 0.0), 4),
            }

    # ---- internals ----------------------------------------------------
    def _warnings(self, today: dict) -> list[int]:
        crossed = []
        for scope, spent, limit in (("day", today["spent"], self.daily_limit()),
                                    ("month", self.data["spent_usd"], self.monthly_limit())):
            if not limit:
                continue
            for step in WARN_STEPS:
                key = f"{scope}:{self._day() if scope == 'day' else self.data['month']}:{step}"
                if spent >= limit * step / 100 and key not in self.data["warned"]:
                    self.data["warned"][key] = True
                    crossed.append(step if scope == "day" else step + 1000)   # month steps ride as 1050/1080/1100
        return crossed

    def _today(self) -> dict:
        return self.data["days"].setdefault(self._day(), {"spent": 0.0, "by_kind": {}})

    def _day(self) -> str:
        return datetime.fromtimestamp(self._now()).date().isoformat()

    def _month_key(self) -> str:
        return datetime.fromtimestamp(self._now()).strftime("%Y-%m")

    def _day_reset(self) -> float:
        d = datetime.fromtimestamp(self._now())
        return datetime.combine(d.date() + timedelta(days=1), datetime.min.time()).timestamp()

    def _month_reset(self) -> float:
        d = datetime.fromtimestamp(self._now())
        last = calendar.monthrange(d.year, d.month)[1]
        return datetime.combine(d.date().replace(day=last) + timedelta(days=1), datetime.min.time()).timestamp()

    def _roll(self) -> None:
        """A new month starts a fresh ledger; a new day drops yesterday's override."""
        month = self._month_key()
        if self.data.get("month") != month:
            self.data = _fresh(month)
            self._save()
            return
        if self.data.get("override_day") and self.data["override_day"] != self._day():
            self.data["override_day"] = None

    def _load(self) -> dict:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("month"), str):
                base = _fresh(raw["month"])
                # A hand-edited or foreign ledger must not break the handshake:
                # every field keeps its fresh value unless the type matches.
                base.update({k: raw[k] for k in base if k in raw and _same_shape(raw[k], base[k], k)})
                if all(_valid_day(v) for v in base["days"].values()) and _num_map(base["by_kind"]):
                    return base
        except (OSError, ValueError):
            pass
        return _fresh(self._month_key())

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".budget-", suffix=".json")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=1)
            os.replace(tmp, self.path)
            os.chmod(self.path, 0o600)
        except OSError:
            pass    # the ledger is advisory; a write failure must not stop a turn


def _fresh(month: str) -> dict:
    return {"month": month, "spent_usd": 0.0, "estimated_usd": 0.0, "by_kind": {},
            "days": {}, "warned": {}, "override_day": None, "key_cap": False, "held": None}


def _same_shape(value, fresh, key: str = "") -> bool:
    if key == "held":
        return value is None or (isinstance(value, dict) and isinstance(value.get("scope"), str)
                                 and isinstance(value.get("day"), str))
    if fresh is None:
        return value is None or isinstance(value, str)
    if isinstance(fresh, bool):
        return isinstance(value, bool)
    if isinstance(fresh, float):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, type(fresh))


def _num_map(m: dict) -> bool:
    return all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in m.values())


def _valid_day(d) -> bool:
    return (isinstance(d, dict) and isinstance(d.get("spent"), (int, float))
            and isinstance(d.get("by_kind"), dict) and _num_map(d["by_kind"]))


def _pct(spent: float, limit: float) -> int:
    return int(min(999, round(100 * spent / limit))) if limit else 0


def estimate_hop_usd(model_id: str, prompt_tokens: int, models: list | None,
                     completion_tokens: int = 2000) -> float:
    """A pre-flight estimate from the catalog's per-1M prices (the discounted
    rate when the account has one); 0 when the model is unknown or free."""
    row = pricing.find_model(model_id, models)
    if not row or row.get("local") or row.get("is_free"):
        return 0.0
    p = row.get("pricing") or {}
    p_in = pricing._dec(p.get("prompt_usd_per_1m_discounted")) or pricing._dec(p.get("prompt_usd_per_1m")) or 0.0
    p_out = pricing._dec(p.get("completion_usd_per_1m_discounted")) or pricing._dec(p.get("completion_usd_per_1m")) or 0.0
    return round((prompt_tokens * p_in + completion_tokens * p_out) / 1e6, 6)
