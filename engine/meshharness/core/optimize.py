"""Mesh Optimize (BETA) — gateway-level token savings, applied client-side.

BETA FEATURE. Off by default. Enable with `/optimize 0.3` or the `optimize`
config key. Behavior, savings math, and the lever stack may change between
releases. Set `/optimize 0` to bypass everything.

Python port of the phase 1 levers from the mesh-optimize reference
implementation (https://github.com/raushan-aifiesta/mesh-optimize):

  dial 0+    cache_control breakpoint injection on stable prefixes,
             max_tokens defaults per task class

Every value above 0 does the same thing. The reference implementation also
cut old tool results to 400 characters on every request from dial 0.2 up.
That lever was removed on 19 November 2026 (docs/bench/caching_pruning):

  - It changed text the provider had already cached, on every step, so the
    cache was not read. Bills went UP: 8 to 9% on DeepSeek with 23% fewer
    tokens sent, 65% on Claude Haiku 5.5.
  - It kept only the last 4 messages whole, so when a model read five or
    more files in one step the first ones were cut before the model had
    seen them, and it read them all again.

Hard rules carried over from the reference implementation:
  - deterministic: same input always produces the same output
  - never touches content inside a client-set cache breakpoint
  - savings are reported honestly or not at all
  - the original message list is never mutated
"""
import copy
import json
import re

# tokens, chars/4 estimate. Below the per-model minimum a cache_control
# marker silently does nothing, so we do not inject one.
_CACHE_MINIMUMS = [
    (re.compile(r"fable"), 2048),
    (re.compile(r"sonnet-4-6"), 2048),
    (re.compile(r"opus"), 4096),
    (re.compile(r"haiku-4-5"), 4096),
]
_DEFAULT_CACHE_MINIMUM = 2048


def survives_pruning(content_chars: int, dial: float) -> bool:
    """True if a `role=="tool"` result of this size is NEVER shortened by
    prepare() at this dial. Always true now: prepare() no longer shortens
    anything (see the module note).

    LOAD-BEARING for memory.dedupe_read: the read-dedupe stub claims "the
    content is already in your context", which is only honest if nothing
    removed it from the wire. This helper stays HERE, next to prepare(), so
    that anyone who adds a lever that shortens or drops messages has to
    come back and make it say so — see the guard test in
    tests/test_no_per_step_cut.py.
    """
    return True


_CODE_RE = re.compile(r"```|(?:\bfunction\b|\bclass\b|\bimport\b|\bdef\b)\s")
_COMPLEX_RE = re.compile(
    r"\b(refactor|implement|debug|architect|migrate|optimi[sz]e|analy[sz]e)\b",
    re.IGNORECASE,
)

_MAX_TOKENS_DEFAULTS = {
    # Backstop against runaway generation — NOT a hard product cap. Kept high
    # enough not to truncate a normal long answer: the old 1024 clipped a
    # ~750-word "routine" reply with finish_reason=length, and there is no
    # retry on a length-truncated 200. You only pay for tokens actually
    # generated, so a high ceiling costs nothing when the reply is short.
    "routine": 4096,
    "standard": 4096,
    "complex": 8192,
    "agentic": 8192,
}


def normalize_model(model: str) -> str:
    """anthropic/claude-opus-4.8 -> claude-opus-4-8 (bare, dashed)."""
    bare = (model or "").lower().rsplit("/", 1)[-1]
    return bare.replace(".", "-")


def _cache_minimum(model: str) -> int:
    bare = normalize_model(model)
    for pattern, minimum in _CACHE_MINIMUMS:
        if pattern.search(bare):
            return minimum
    return _DEFAULT_CACHE_MINIMUM


def _est_tokens(value) -> int:
    if value is None:
        return 0
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
    return -(-len(text) // 4)  # ceil division


def _msg_tokens(message: dict) -> int:
    return _est_tokens(message.get("content")) + 4


def _classify(messages: list, has_tools: bool) -> str:
    depth = len(messages)
    sample = ""
    for message in messages[-6:]:
        content = message.get("content")
        sample += (content if isinstance(content, str) else json.dumps(content)) + "\n"
    if has_tools and depth > 6:
        return "agentic"
    if has_tools:
        return "complex"
    if _CODE_RE.search(sample) or _COMPLEX_RE.search(sample):
        return "complex"
    if sum(_msg_tokens(m) for m in messages[-6:]) < 150 and depth <= 4:
        return "routine"
    return "standard"


def _has_client_breakpoints(messages: list) -> bool:
    return any("cache_control" in m for m in messages)


def prepare(messages: list, model: str, dial: float, has_tools: bool) -> tuple:
    """Apply the lever stack for the given dial. BETA.

    Returns (optimized_messages, extra_payload, plan). The input list is
    never mutated. dial 0 returns everything untouched.
    """
    plan = {
        "dial": dial,
        "classification": "standard",
        "levers_applied": [],
        "tokens_pruned_est": 0,
        "audit": [],
    }
    if not dial or dial <= 0:
        return messages, {}, plan

    out = copy.deepcopy(messages)
    plan["classification"] = _classify(out, has_tools)

    # lever: cache_control injection (dial 0+). Skips entirely when the
    # client placed its own breakpoints.
    if not _has_client_breakpoints(out):
        minimum = _cache_minimum(model)
        applied = False
        if out and out[0].get("role") == "system":
            first_tokens = _est_tokens(out[0].get("content"))
            if first_tokens >= minimum:
                out[0]["cache_control"] = {"type": "ephemeral"}
                plan["audit"].append({
                    "lever": "cache_injection",
                    "action": f"breakpoint on system message (~{first_tokens} tok)",
                })
                applied = True
        if len(out) >= 3:
            prefix_tokens = sum(_msg_tokens(m) for m in out[:-1])
            anchor = out[-2]
            if prefix_tokens >= minimum and "cache_control" not in anchor:
                anchor["cache_control"] = {"type": "ephemeral"}
                plan["audit"].append({
                    "lever": "cache_injection",
                    "action": f"breakpoint on history (~{prefix_tokens} tok prefix)",
                })
                applied = True
        if applied:
            plan["levers_applied"].append("cache_injection")

    # lever: max_tokens default per task class (dial 0+). A backstop against
    # runaway generation, applied only because the CLI does not set one.
    extra = {"max_tokens": _MAX_TOKENS_DEFAULTS[plan["classification"]]}
    plan["levers_applied"].append("max_tokens_default")
    plan["audit"].append({
        "lever": "max_tokens_default",
        "action": f"max_tokens={extra['max_tokens']} for {plan['classification']} task",
    })

    return out, extra, plan


def savings_line(plan: dict, usage: dict) -> str:
    """One-line honest savings summary for the post-turn status line.

    Only reports what is measurable: pruned tokens (chars/4 estimate) and
    cache fields when the gateway surfaces them in usage. No counterfactual
    guessing.
    """
    if not plan or not plan.get("levers_applied"):
        return ""
    parts = []
    pruned = plan.get("tokens_pruned_est", 0)
    if pruned:
        parts.append(f"~{pruned} tok pruned")
    usage = usage or {}
    # explicit anthropic-style field first, OpenAI convention as fallback
    cache_read = (
        usage.get("cache_read_input_tokens")
        or (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
        or 0
    )
    if cache_read:
        parts.append(f"{cache_read} tok from cache (90% off)")
    if "cache_injection" in plan["levers_applied"] and not cache_read:
        parts.append("cache breakpoints set")
    detail = ", ".join(parts) if parts else ", ".join(plan["levers_applied"])
    return f"⚡ optimize beta (dial {plan['dial']}): {detail}"
