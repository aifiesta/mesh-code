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
    ("local", "Local model", "A coding model that runs on this machine — free, offline, private while it answers"),
    ("routing", "Policy", "Who writes the code, and when the cloud steps in"),
    ("budget", "Budget", "What the cloud may spend; work on this machine is always free"),
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
        "label": "Pinned model",
        "help": "The model used when the policy is Pinned.",
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
        "help": "Models the gateway's auto routing must never pick.",
        "why": "For models that are wrong for your work, priced badly for you, or that you are not allowed to send data to.",
    },
    {
        "key": "local_enabled",
        "category": "local",
        "type": "bool",
        "label": "Use a local model",
        "default": True,
        "help": "Run the recommended model on this machine for coding turns.",
        "why": "Off hides local models and never starts the server; everything goes to the cloud.",
    },
    {
        "key": "local_auto_setup",
        "category": "local",
        "type": "bool",
        "label": "Download automatically on first run",
        "default": True,
        "help": "Fetch the recommended model without asking when it is 8 GB or less and runs on the GPU (or on the CPU with Use even when slow on); otherwise ask.",
        "why": "A first run that just works, at the cost of a few GB of disk you were not asked about.",
    },
    {
        "key": "local_model",
        "category": "local",
        "type": "local_model",
        "label": "Local model",
        "default": "",
        "help": "Which downloaded model local/auto resolves to; only installed models are accepted. Empty means the tier recommendation.",
        "why": "Pick a bigger model if you have the memory, a smaller one if the fan bothers you.",
    },
    {
        "key": "local_fallback_model",
        "category": "local",
        "type": "model",
        "label": "Until it's ready",
        "default": "anthropic/claude-sonnet-4.5",
        "help": "Cloud model used while the local one downloads, or if it fails to start.",
        "why": "The download takes minutes; this is what answers meanwhile.",
    },
    {
        "key": "local_context",
        "category": "local",
        "type": "enum",
        "label": "Context window",
        "default": "auto",
        "options": [
            _opt("auto", "Auto", "The largest that keeps memory free."),
            _opt("16k", "16k", ""), _opt("32k", "32k", ""), _opt("64k", "64k", ""),
            _opt("128k", "128k", ""), _opt("256k", "256k", ""),
        ],
        "help": "Bigger windows use more memory. Auto keeps the configured amount free.",
        "why": "Long files need a big window; a laptop needs its memory back.",
    },
    {
        "key": "local_gpu",
        "category": "local",
        "type": "enum",
        "label": "GPU",
        "default": "auto",
        "options": [
            _opt("auto", "Auto", "Use the GPU when it clearly helps."),
            _opt("on", "On", "Offload every layer, even when memory is unknown."),
            _opt("off", "Off", "CPU only."),
        ],
        "help": "Whether model layers are placed on the GPU.",
        "why": "Auto is right on Apple Silicon; force it on for a discrete card the probe cannot size.",
    },
    {
        "key": "local_keep_free_gb",
        "category": "local",
        "type": "int",
        "label": "Memory to keep free",
        "default": 4,
        "min": 2, "max": 32,
        "help": "Gigabytes of RAM the local model must leave untouched.",
        "why": "Lower it for a bigger model; raise it if the rest of the machine starts swapping.",
    },
    {
        "key": "local_idle_unload_min",
        "category": "local",
        "type": "int",
        "label": "Unload after idle",
        "default": 20,
        "min": 0, "max": 240,
        "zero_label": "Never",
        "help": "Minutes of silence before the model is unloaded from memory.",
        "why": "Unloading gives the memory back; reloading costs a few seconds on the next turn.",
    },
    {
        "key": "local_use_when_slow",
        "category": "local",
        "type": "bool",
        "label": "Use even when slow",
        "default": False,
        "help": "Run the local model on machines where it would be slow (no GPU, little memory).",
        "why": "Free and kept on this machine, but a turn may take minutes; off means the cloud fallback answers.",
    },
    {
        "key": "policy",
        "category": "routing",
        "type": "enum",
        "label": "Who writes",
        "default": "local-first",
        "options": [
            _opt("local-first", "Local first", "The model on this machine writes; a strong cloud model takes over when it is stuck."),
            _opt("cloud-auto", "Gateway auto", "Mesh's Auto Router picks a cloud model per prompt."),
            _opt("pinned", "Pinned", "Always the pinned model."),
        ],
        "help": "Local first is free for most of the work; the cloud is called only when it earns its keep.",
        "why": "A local model handles edits and runs; a strong model is worth paying for when the local one is stuck.",
    },
    {
        "key": "escalation_model",
        "category": "routing",
        "type": "model",
        "label": "Escalate to",
        "default": "",
        "depends_on": {"policy": "local-first"},
        "help": "The strong model that takes over a stuck turn. Empty means the shipped chain (Sonnet 5.5 first).",
        "why": "Picked from the Artificial Analysis coding-agent index; the shipped chain is updated with the app.",
    },
    {
        "key": "escalation_fallbacks",
        "category": "routing",
        "type": "model_list",
        "label": "Escalation fallbacks",
        "default": [],
        "depends_on": {"policy": "local-first"},
        "help": "Tried in order when the escalation model fails; the shipped chain follows these.",
        "why": "Provider outages fail over server-side without ending the turn.",
    },
    {
        "key": "escalation_effort",
        "category": "routing",
        "type": "enum",
        "label": "Escalation effort",
        "default": "high",
        "depends_on": {"policy": "local-first"},
        "options": [
            _opt("medium", "Medium", ""),
            _opt("high", "High", "The default for a stuck turn."),
            _opt("xhigh", "Very high", ""),
            _opt("max", "Max", "The model thinks as long as it likes."),
        ],
        "help": "Reasoning budget for the escalated model.",
        "why": "Higher costs more tokens per turn; Harder uses the next level up for that one escalation.",
    },
    {
        "key": "auto_escalate",
        "category": "routing",
        "type": "bool",
        "label": "Escalate on its own",
        "default": True,
        "depends_on": {"policy": "local-first"},
        "help": "Hand a stuck turn to the strong model without asking: repeated actions, failed tool batches, a refusal.",
        "why": "Off means the Escalate button is the only way up; nothing is spent unasked.",
    },
    {
        "key": "escalation_sticky",
        "category": "routing",
        "type": "enum",
        "label": "After escalating",
        "default": "turn",
        "depends_on": {"policy": "local-first"},
        "options": [
            _opt("turn", "Back to local next turn", ""),
            _opt("session", "Stay escalated this session", "Until you de-escalate."),
        ],
        "help": "Whether the next turn starts local again.",
        "why": "A hard stretch of work may want the strong model for several turns.",
    },
    {
        "key": "weight_profile",
        "category": "routing",
        "type": "enum",
        "label": "Balance",
        "default": "balanced",
        "depends_on": {"policy": "cloud-auto"},
        "options": [
            _opt("quality_first", "Quality first", ""),
            _opt("balanced", "Balanced", ""),
            _opt("cost_first", "Cost first", ""),
            _opt("latency_first", "Latency first", ""),
        ],
        "help": "Mesh's weight profile for its Auto Router.",
        "why": "The router trades quality, price and speed per prompt along this profile.",
    },
    {
        "key": "monthly_budget_usd",
        "category": "budget",
        "type": "money",
        "label": "Monthly limit",
        "default": 10.0,
        "min": 0.0, "max": 10000.0,
        "zero_label": "No limit",
        "help": "Cloud spend this app allows per calendar month. Today's share is what is left this month divided by the days left; past it, cloud work pauses until midnight unless you allow more from the usage ring.",
        "why": "Ten dollars covers a month of six-hour days when the local model writes and the cloud only rescues stuck turns.",
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
        "why": "Next session starts warm. Stored in ~/.mesh-code/context/, never inside your repo.",
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

    if kind == "money":
        try:
            f = float(value)
        except (TypeError, ValueError):
            raise InvalidSetting(f"{key} must be a number")
        lo, hi = spec.get("min", 0.0), spec.get("max", 10**6)
        if not lo <= f <= hi:
            raise InvalidSetting(f"{key} must be between {lo} and {hi}")
        return round(f, 2)

    if kind == "int":
        try:
            n = int(value)
        except (TypeError, ValueError):
            raise InvalidSetting(f"{key} must be a whole number")
        lo, hi = spec.get("min", 0), spec.get("max", 10**9)
        if not lo <= n <= hi:
            raise InvalidSetting(f"{key} must be between {lo} and {hi}")
        return n

    if kind in ("model_list",):
        if value in (None, "", "off"):
            return []
        if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
            raise InvalidSetting(f"{key} must be a list of model ids")
        return [x.strip() for x in value if x.strip()]

    if kind in ("model", "text", "local_model"):
        if value is None:
            return ""
        if not isinstance(value, str):
            raise InvalidSetting(f"{key} must be text")
        return value.strip() if kind in ("model", "local_model") else value

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
