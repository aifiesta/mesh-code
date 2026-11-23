"""Which tier answers a hop: the local writer, a cloud fallback, the gateway's
own router, a pinned model, or the escalation chain. Tier is per turn; the
session loop asks `hop_cfg` before every request and reports what happened."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

BUNDLED = Path(__file__).with_name("policy.json")

POLICIES = ("local-first", "cloud-auto", "pinned")
STICKY = ("turn", "session")
EFFORTS = ("medium", "high", "xhigh", "max")


class Tier(str, Enum):
    LOCAL = "local"
    CLOUD_FALLBACK = "cloud_fallback"
    CLOUD_AUTO = "cloud_auto"
    PINNED = "pinned"
    ESCALATED = "escalated"


def load(path: Path = BUNDLED) -> dict:
    doc = json.loads(path.read_text(encoding="utf-8"))
    for role in ("audit", "escalation"):
        if not doc.get(role, {}).get("models"):
            raise ValueError(f"policy.json: {role}.models is empty")
    return doc


def chain(doc: dict, role: str, cfg: dict) -> list[str]:
    """The ordered candidates for a role: the user's pick first, then the
    user's fallbacks, then the shipped chain, minus exclusions, deduplicated."""
    excluded = set(cfg.get("exclude_models") or [])
    picked = [cfg.get(f"{role}_model") or ""] + list(cfg.get(f"{role}_fallbacks") or [])
    out: list[str] = []
    for m in picked + list(doc[role]["models"]):
        if m and m not in excluded and m not in out:
            out.append(m)
    return out


@dataclass
class Escalation:
    model: str
    reason: str
    explicit: bool
    hops: int = 0
    tokens: int = 0
    harder: bool = False


@dataclass
class PolicyController:
    cfg: dict
    doc: dict = field(default_factory=load)
    tier: Tier = Tier.LOCAL
    escalation: Escalation | None = None
    pending_escalation: Escalation | None = None
    sticky_escalation: Escalation | None = None
    turn_escalations: int = 0

    @property
    def policy(self) -> str:
        p = self.cfg.get("policy", "local-first")
        return p if p in POLICIES else "local-first"

    def begin_turn(self) -> Tier:
        self.turn_escalations = 0
        if self.policy != "local-first":
            self.pending_escalation = self.sticky_escalation = None
        pending = self.pending_escalation or self.sticky_escalation
        self.pending_escalation = None
        if pending:
            self.escalation = Escalation(model=pending.model, reason=pending.reason,
                                         explicit=pending.explicit, harder=pending.harder)
            self.tier = Tier.ESCALATED
            if self.cfg.get("escalation_sticky", "turn") == "session":
                self.sticky_escalation = Escalation(model=pending.model, reason=pending.reason,
                                                    explicit=pending.explicit)   # Harder is for one escalation
        else:
            self.escalation = None
            self.tier = self._base_tier()
        return self.tier

    def _base_tier(self) -> Tier:
        # The fallback tier is observed by the session, never chosen here.
        return {"local-first": Tier.LOCAL, "cloud-auto": Tier.CLOUD_AUTO,
                "pinned": Tier.PINNED}[self.policy]

    def hop_cfg(self, base: dict) -> dict:
        """cfg for one request under the current tier. `model` is what the
        session resolves (a local id falls back to the cloud on its own)."""
        cfg = dict(base)
        cfg["auto_route"] = False
        if self.tier == Tier.ESCALATED and self.escalation:
            others = [m for m in chain(self.doc, "escalation", self.cfg) if m != self.escalation.model]
            cfg["model"] = self.escalation.model
            cfg["models"] = others[:3]      # the tier's own chain, even when empty
            cfg["reasoning_effort"] = self.effort(harder=self.escalation.harder)
        elif self.tier == Tier.CLOUD_AUTO:
            cfg["model"] = "auto"
            cfg["auto_route"] = True
            cfg["weight_profile"] = self.cfg.get("weight_profile") or "balanced"
        elif self.tier in (Tier.LOCAL, Tier.CLOUD_FALLBACK):
            cfg["model"] = "local/auto"
        else:
            cfg["model"] = self.cfg.get("model") or "local/auto"
        return cfg

    def effort(self, *, harder: bool = False) -> str:
        """The configured escalation effort; Harder is one level above it, capped."""
        base = self.cfg.get("escalation_effort") or self.doc["escalation"].get("reasoning_effort") or "high"
        if base not in EFFORTS:
            base = "high"
        if not harder:
            return base
        return EFFORTS[min(EFFORTS.index(base) + 1, len(EFFORTS) - 1)]

    def request_escalation(self, reason: str, *, explicit: bool = False, harder: bool = False) -> bool:
        """Move the rest of this turn to the escalation chain. Implicit
        requests need `auto_escalate` and happen once per turn."""
        if self.policy != "local-first" or self.tier == Tier.ESCALATED:
            return False
        if not explicit and (not self.cfg.get("auto_escalate", True) or self.turn_escalations >= 1):
            return False
        models = chain(self.doc, "escalation", self.cfg)
        if not models:
            return False
        self.escalation = Escalation(model=models[0], reason=reason, explicit=explicit, harder=harder)
        self.tier = Tier.ESCALATED
        self.turn_escalations += 1
        if self.cfg.get("escalation_sticky", "turn") == "session":
            self.sticky_escalation = Escalation(model=models[0], reason=reason, explicit=explicit)
        return True

    def escalate_next_turn(self, *, harder: bool = False) -> str | None:
        """Escalate was pressed while idle: the next turn starts escalated."""
        models = chain(self.doc, "escalation", self.cfg)
        if self.policy != "local-first" or not models:
            return None
        self.pending_escalation = Escalation(model=models[0], reason="requested", explicit=True, harder=harder)
        return models[0]

    def hand_back(self) -> None:
        """This turn goes back to the base tier; a session-sticky escalation
        survives for the next one."""
        self.escalation = None
        self.tier = self._base_tier()

    def deescalate(self) -> bool:
        changed = self.tier == Tier.ESCALATED or self.pending_escalation is not None or self.sticky_escalation is not None
        self.pending_escalation = None
        self.sticky_escalation = None
        if self.tier == Tier.ESCALATED:
            self.escalation = None
            self.tier = self._base_tier()
        return changed

    def after_hop(self, tokens: int) -> str | None:
        """Account a finished hop; returns the hand-back reason when the
        escalation has used its share of the turn."""
        if self.tier != Tier.ESCALATED or not self.escalation:
            return None
        e = self.escalation
        e.hops += 1
        e.tokens += int(tokens or 0)
        lim = self.doc["escalation"]
        if e.hops >= int(lim.get("max_hops", 8)):
            why = f"after {e.hops} hops"
        elif e.tokens >= int(lim.get("max_tokens", 300000)):
            why = f"after {e.tokens // 1000}k tokens"
        else:
            return None
        self.escalation = None
        self.tier = self._base_tier()
        return why

    def end_turn(self) -> None:
        if self.cfg.get("escalation_sticky", "turn") != "session":
            self.sticky_escalation = None
        self.escalation = None
        self.tier = self._base_tier()

    def describe(self) -> dict:
        e = self.escalation or self.pending_escalation or self.sticky_escalation
        return {
            "policy": self.policy,
            "tier": self.tier.value,
            "escalation_model": chain(self.doc, "escalation", self.cfg)[:1][0] if chain(self.doc, "escalation", self.cfg) else None,
            "audit_model": chain(self.doc, "audit", self.cfg)[:1][0] if chain(self.doc, "audit", self.cfg) else None,
            "escalated": e.model if e else None,
            "escalated_reason": e.reason if e else None,
            "escalation_pending": self.pending_escalation is not None or self.sticky_escalation is not None,
        }
