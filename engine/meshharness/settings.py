"""One declarative description of every setting the engine understands.

Why this exists rather than a switch statement in the transport:

The CLI has 24 slash commands, and a user only discovers them by typing
`/help` and reading a wall of text. Porting that to a GUI by hand-wiring
each control would mean every setting is described in three places — the
widget, the validator, and the help text — which is how a settings screen
drifts out of sync with what the engine actually does.

So the engine publishes this schema, and the frontend RENDERS it. A control
bar, a command palette and a settings sheet are three different views of
the same list. Adding a setting here makes it appear in all of them, with
its own explanation, and validated the same way wherever it is set from.

`why` is not filler. Most of these dials are only worth touching if you
know what they trade away, and that sentence is the difference between a
discoverable feature and a mystery toggle.
"""
from __future__ import annotations

from typing import Any

# Categories, in the order a settings screen should show them.
CATEGORIES = [
    ("model", "Model", "Which model answers, and what it costs"),
    ("routing", "Routing", "Let Mesh choose per prompt, or pin one model"),
    ("behaviour", "Behaviour", "How the assistant works and writes"),
    ("limits", "Limits & recovery", "What happens on long or stuck turns"),
    ("privacy", "Memory & privacy", "What persists between sessions"),
]


def _opt(value, label, blurb=""):
    return {"value": value, "label": label, "blurb": blurb}


SCHEMA: list[dict] = [
    {
        "key": "model",
        "category": "model",
        "type": "model",              # rendered by the catalog picker
        "label": "Model",
        "help": "The model used when routing is off, and the starting point when it is on.",
        "why": "Bigger models are better at multi-step work; smaller ones are far cheaper for edits and questions.",
    },
    {
        "key": "fallback_models",
        "category": "model",
        "type": "model_list",
        "label": "Fallback order",
        "help": "If the primary model fails or is rate-limited, the gateway tries these in order.",
        "why": "Keeps a long run alive when one provider has a bad day.",
    },
    {
        "key": "exclude_models",
        "category": "model",
        "type": "model_list",
        "label": "Never use",
        "help": "Models the router will never pick, even if the table rates them highly.",
        "why": "For models that are wrong for your work, priced badly for you, or that you are not allowed to send data to.",
    },
    {
        "key": "route_mode",
        "category": "routing",
        "type": "enum",
        "label": "Routing",
        "default": "off",
        "options": [
            _opt("off", "Pinned", "Always use the model above."),
            _opt("smart", "Smart (local)", "A bundled table picks per prompt. No extra tokens, no extra network hop."),
            _opt("auto", "Gateway auto", "Mesh's own Auto Router picks. Costs a classifier hop."),
        ],
        "help": "Who chooses the model for each prompt.",
        "why": "Smart routing spends less on easy prompts and reaches for a stronger model on hard ones, deciding locally in microseconds.",
    },
    {
        "key": "route_effort",
        "category": "routing",
        "type": "enum",
        "label": "Effort",
        "default": "auto",
        "depends_on": {"route_mode": "smart"},
        "options": [
            _opt("auto", "Auto", "Detect difficulty from the prompt."),
            _opt("low", "Low", "Favour cheap models."),
            _opt("medium", "Medium", ""),
            _opt("high", "High", ""),
            _opt("xhigh", "Very high", ""),
            _opt("max", "Max", "Always reach for the strongest capable model."),
        ],
        "help": "How hard the router leans on capability rather than price.",
        "why": "Auto is usually right. Force it up for a gnarly refactor, down for a batch of small edits.",
    },
    {
        "key": "route_weights",
        "category": "routing",
        "type": "weights",
        "label": "Balance",
        "default": {"cost": 0.5, "cap": 0.3, "speed": 0.2},
        "depends_on": {"route_mode": "smart"},
        "help": "How the router trades cost against capability and speed.",
        "why": "Weights move the pick along each cohort's efficiency frontier — no setting can select a model the table says cannot do the task.",
    },
    {
        "key": "reasoning_effort",
        "category": "behaviour",
        "type": "enum",
        "label": "Reasoning",
        "default": None,
        "options": [
            _opt(None, "Model default", "Don't send a preference."),
            _opt("none", "Off", ""),
            _opt("low", "Low", ""),
            _opt("medium", "Medium", ""),
            _opt("high", "High", "Think longer before answering."),
        ],
        "help": "Passed to models that support a reasoning budget.",
        "why": "Higher settings cost more tokens and take longer, and help most on planning and debugging.",
    },
    {
        "key": "output_style",
        "category": "behaviour",
        "type": "enum",
        "label": "Writing style",
        "default": "default",
        "options": [
            _opt("default", "Default", "Balanced."),
            _opt("concise", "Concise", "Short answers, minimal preamble."),
            _opt("explanatory", "Explanatory", "Explains the reasoning as it goes."),
            _opt("learning", "Learning", "Teaches while it works."),
        ],
        "help": "Affects prose only — never what the assistant is allowed to do.",
        "why": "Concise is good once you trust it; explanatory is good when reviewing unfamiliar code.",
    },
    {
        "key": "optimize",
        "category": "behaviour",
        "type": "dial",
        "label": "Token savings",
        "default": 0.0,
        "min": 0.0, "max": 0.95, "step": 0.05,
        "beta": True,
        "help": "How aggressively to trim what gets re-sent each turn.",
        "why": "Cuts spend on long sessions. Higher values prune more context, so raise it gradually and watch quality.",
    },
    {
        "key": "system",
        "category": "behaviour",
        "type": "text",
        "label": "System prompt",
        "multiline": True,
        "help": "Prepended to the harness's own instructions, not a replacement for them.",
        "why": "Good place for project conventions the model should always follow.",
    },
    {
        "key": "max_hops",
        "category": "limits",
        "type": "int",
        "label": "Max steps per turn",
        "default": 0,
        "min": 0, "max": 200,
        "zero_label": "Unlimited",
        "help": "Pause the turn after this many tool steps. 0 means no limit.",
        "why": "A safety rail for unattended runs; stall detection stops genuine loops regardless.",
    },
    {
        "key": "stall_policy",
        "category": "limits",
        "type": "enum",
        "label": "If it repeats itself",
        "default": "pause",
        "options": [
            _opt("pause", "Stop and ask", "End the turn so you can redirect it."),
            _opt("keep-going", "Keep nudging", "Never pause — for unattended runs."),
        ],
        "help": "What to do when the model repeats the same action without progress.",
        "why": "Stop and ask is right when you are watching; keep nudging is for long jobs you have walked away from.",
    },
    {
        "key": "auto_compact",
        "category": "limits",
        "type": "bool",
        "label": "Auto-compact history",
        "default": True,
        "help": "Summarise old turns as the conversation approaches the model's context limit.",
        "why": "Without it, a long session eventually fails on context. The full transcript is kept on disk either way.",
    },
    {
        "key": "repo_memory",
        "category": "privacy",
        "type": "bool",
        "label": "Repo memory",
        "default": True,
        "help": "Remember durable facts and file structure for this project between sessions.",
        "why": "Next session starts warm. Stored in ~/.mesh-harness/context/, never inside your repo.",
    },
]

BY_KEY = {s["key"]: s for s in SCHEMA}

_ENUM_ANY = object()


class InvalidSetting(ValueError):
    pass


def coerce(key: str, value: Any) -> Any:
    """Validate and normalise one setting. Raises InvalidSetting.

    Every write path goes through here — the websocket, a palette action, a
    settings field — so an out-of-range dial or a bogus enum can never reach
    the config no matter which surface sent it.
    """
    spec = BY_KEY.get(key)
    if spec is None:
        raise InvalidSetting(f"unknown setting {key!r}")
    kind = spec["type"]

    if kind == "enum":
        allowed = [o["value"] for o in spec["options"]]
        if value not in allowed:
            raise InvalidSetting(
                f"{key} must be one of {[a for a in allowed if a is not None]}")
        return value

    if kind == "bool":
        return bool(value)

    if kind == "int":
        try:
            n = int(value)
        except (TypeError, ValueError):
            raise InvalidSetting(f"{key} must be a whole number")
        lo, hi = spec.get("min", 0), spec.get("max", 10**9)
        if not lo <= n <= hi:
            raise InvalidSetting(f"{key} must be between {lo} and {hi}")
        return n

    if kind == "dial":
        try:
            f = float(value)
        except (TypeError, ValueError):
            raise InvalidSetting(f"{key} must be a number")
        lo, hi = spec.get("min", 0.0), spec.get("max", 1.0)
        if not lo <= f <= hi:
            raise InvalidSetting(f"{key} must be between {lo} and {hi}")
        return round(f, 4)

    if kind == "weights":
        if not isinstance(value, dict):
            raise InvalidSetting("balance must be an object")
        out = {}
        for k in ("cost", "cap", "speed"):
            try:
                out[k] = max(0.0, float(value.get(k, 0)))
            except (TypeError, ValueError):
                raise InvalidSetting(f"balance.{k} must be a number")
        if sum(out.values()) <= 0:
            raise InvalidSetting("at least one balance weight must be above zero")
        return out

    if kind in ("model_list",):
        if value in (None, "", "off"):
            return []
        if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
            raise InvalidSetting(f"{key} must be a list of model ids")
        return [x.strip() for x in value if x.strip()]

    if kind in ("model", "text"):
        if value is None:
            return ""
        if not isinstance(value, str):
            raise InvalidSetting(f"{key} must be text")
        return value.strip() if kind == "model" else value

    raise InvalidSetting(f"unhandled setting type {kind!r}")


def describe(cfg: dict) -> dict:
    """The schema plus current values — everything a UI needs to render."""
    return {
        "categories": [
            {"id": cid, "label": label, "blurb": blurb}
            for cid, label, blurb in CATEGORIES
        ],
        "settings": [
            {**spec, "value": cfg.get(spec["key"], spec.get("default"))}
            for spec in SCHEMA
        ],
    }
