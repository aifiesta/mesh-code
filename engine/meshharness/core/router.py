"""Smart routing — a thin, table-driven model picker.

The CLI ships a routing table (opaque per-model scores per task cohort,
compiled elsewhere) and picks a concrete model locally in ~microseconds:
classify the prompt into a cohort → filter to feasible models → score the
cohort's candidate list with the user's weights (cost/capability/speed) →
pick. No classifier tokens are ever billed and no network hop is added —
the two costs the gateway's `model:"auto"` path pays on every request.

Everything here fails OPEN: no table, unknown cohort, empty candidates,
any exception → the caller keeps its pinned model or gateway auto-routing.
A routing layer must never be able to break a request.

The methodology that produces the table is deliberately not in this repo;
the table is data. Users steer with `/route weights cost=.5 cap=.3 speed=.2`
— weights choose a point along each cohort's efficiency frontier, so no
weight setting can select a strictly-dominated model.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

# Default weights: the user-facing contract. cap="auto" means the capability
# weight is DRIVEN BY PROMPT DIFFICULTY (easy prompts barely pay for
# capability, hard prompts pay a lot) — the user tunes only cost vs speed.
# An explicit numeric cap remains a power-user override.
DEFAULT_WEIGHTS = {"cost": 0.5, "cap": 0.3, "speed": 0.2}
# Effort levels: how hard the router should lean into capability for this
# prompt. "auto" detects low/mid/high from the prompt; the user can force
# any level with /route effort — same vocabulary as reasoning effort.
EFFORT_LEVELS = ("auto", "low", "medium", "high", "xhigh", "max")
_EFFORT_ALIAS = {"medium": "mid", "med": "mid"}
# Runtime quality floor: weights choose along the frontier of COMPETENT
# models. A candidate under this cohort score is dropped whenever anything
# better survives — no speed/cost setting may select a model the table says
# can't do the task (belt-and-braces with the compiler's own floor; also
# protects against stale bundled tables).
QUALITY_FLOOR = 45

_TABLE_CACHE: "dict | None" = None


# ---------------------------------------------------------------------------
# Table loading
# ---------------------------------------------------------------------------

def load_table(path: "str | None" = None) -> "dict | None":
    """Bundled table (ships with the package); None when absent/corrupt."""
    global _TABLE_CACHE
    if _TABLE_CACHE is not None and path is None:
        return _TABLE_CACHE
    try:
        p = Path(path) if path else Path(__file__).parent / "routing_table.json"
        table = json.loads(p.read_text())
        if not isinstance(table, dict) or "models" not in table:
            return None
        if path is None:
            _TABLE_CACHE = table
        return table
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Cohort classification — local signals only, zero cost, deterministic
# ---------------------------------------------------------------------------

_CODE_RE = re.compile(
    r"```|\bdef \w+\(|\bfunction\s+\w+\(|\bclass \w+|\bimport \w+|</?\w+>|"
    r"\btraceback\b|\bstack trace\b|\bcompil(?:e|er|ation)\b|\bsyntax error\b|"
    r"\b(?:refactor|debug|unit test|regex|bug|lint)\b|"
    # Build/implement verbs paired with an engineering noun: "implement a
    # scheduler, prove correctness" is CODING work, but the bare word
    # "prove" would otherwise drag it into reasoning-math.
    r"\b(?:implement|build|scaffold|port|migrate)\b.{0,40}"
    r"\b(?:class|module|function|service|api|endpoint|parser|cache|scheduler"
    r"|queue|server|client|component|schema|pipeline|library|cli|handler)\b|\.(?:py|js|ts|go|rs|java|c|cpp|rb|sh)\b",
    re.I)
_EXTRACT_RE = re.compile(
    r"\b(?:extract|parse|json|csv|structured|schema|fields?|scrape)\b", re.I)
_MATH_RE = re.compile(
    r"\b(?:solve|prove|calculate|equation|integral|derivative|probability|theorem)\b|"
    r"[∑∫√π]|\b\d+\s*[+\-*/^]\s*\d+\b", re.I)
# "writing" = prose artifacts. A bare "write ..." (write a file, write a
# script) is NOT prose — it needs the artifact noun nearby, else it falls
# through to code/agentic/chat where it belongs.
_WRITE_RE = re.compile(
    r"\b(?:blog|essay|story|poem|caption|newsletter|tagline|slogan|headline"
    r"|article|tweet|linkedin post|cover letter|micro-?story|blurb)\b"
    r"|\b(?:write|draft|compose|rewrite)\b.{0,40}\b(?:blog|essay|story|poem|post"
    r"|email|copy|article|bio|speech|lyrics)\b", re.I)
_RESEARCH_RE = re.compile(
    r"\b(?:latest|news|current|today|recent|search the web|cite|sources?|202\d)\b", re.I)
_CLASSIFY_RE = re.compile(
    r"\b(?:classify|categorize|label|sentiment|triage|route this|yes or no)\b", re.I)


def classify(text: str, *, has_image: bool = False, has_tools: bool = False,
             history_chars: int = 0) -> tuple:
    """(cohort, confidence 0-1). Order matters: strongest signals first."""
    t = text or ""
    if has_image:
        return "vision", 0.95
    if history_chars > 120_000 or len(t) > 60_000:
        return "long-context", 0.9
    if _CODE_RE.search(t):
        # in a tool-calling session, code work IS agentic work
        return ("agentic" if has_tools else "coding"), 0.8
    if _EXTRACT_RE.search(t):
        return "extraction", 0.7
    if _MATH_RE.search(t):
        return "reasoning-math", 0.7
    if _CLASSIFY_RE.search(t):
        return "cheap-bulk", 0.7
    if _RESEARCH_RE.search(t):
        return "web-research", 0.6
    if _WRITE_RE.search(t):
        return "writing", 0.6
    if has_tools:
        return "agentic", 0.5
    return "chat", 0.4


# ---------------------------------------------------------------------------
# Difficulty — a crude marginal-gain proxy
# ---------------------------------------------------------------------------
# The most consistent routing-research finding (RouteLLM -> 2026): predict
# whether the expensive model is WORTH IT for this prompt, not just what kind
# of prompt it is. v1 is deliberately crude — deterministic surface signals
# mapped to a weight adjustment — but it breaks the "easy and hard prompts
# get identical picks" tie, which is the industry's biggest measured gap
# (RouterArena: every router under-uses cheap models on easy prompts).

_HARD_RE = re.compile(
    r"\b(?:architecture|design(?:\s+a|\s+an|\s+the)?|refactor|optimi[sz]e|prove|"
    r"derive|multi-?step|end-?to-?end|production|concurren\w*|distributed|"
    r"race condition|deadlock|migrate|benchmark|edge cases?|trade-?offs?|"
    r"scalab\w+|formal|rigorous|comprehensive|in depth|thorough)\b", re.I)
_EASY_RE = re.compile(
    r"^(?:what(?:'s| is| are)?|who|when|where|define|meaning of|translate|"
    r"convert|list|name)\b", re.I)
_CONSTRAINT_RE = re.compile(
    r"\b(?:must|should|ensure|exactly|at least|no more than|without using|"
    r"step[- ]by[- ]step)\b", re.I)


# Replies that answer WITHIN an ongoing task rather than starting a new one.
# Length alone is NOT the test: "write an essay on LLMs" is 22 chars and is a
# brand-new task, but a <25-char rule inherited the previous turn's cohort and
# routed it as agentic (seen live). Match the shape of a continuation instead.
_CONTINUATION_RE = re.compile(
    r"^(y|n|ok|okay|yes|yeah|no|nope|sure|go|go on|go ahead|do it|do that|"
    r"continue|carry on|keep going|next|proceed|again|more|retry|resume|"
    r"stop|wait|thanks|thank you|please|fix it|same|both|either|neither|"
    r"\d+|[^\w]*)$", re.IGNORECASE)


def is_continuation(text: str) -> bool:
    """True when *text* reads as an answer inside a task, not a new task.

    Pure and side-effect free so the CLI seam stays testable.
    """
    t = " ".join((text or "").strip().split()).rstrip(".!?,")
    if not t:
        return True
    if len(t) > 24:          # long enough to carry its own instruction
        return False
    return bool(_CONTINUATION_RE.match(t))


def estimate_difficulty(text: str) -> str:
    """"low" | "mid" | "high" from surface signals. Deterministic."""
    t = (text or "").strip()
    score = 0
    if len(t) > 400:
        score += 1
    if len(t) > 1200:
        score += 1
    score += min(3, len(_HARD_RE.findall(t)))
    if len(_CONSTRAINT_RE.findall(t)) >= 3:
        score += 1
    if t.count("```") >= 2:
        score += 1
    if _EASY_RE.search(t) and len(t) < 90:
        score -= 2
    elif len(t) < 40:
        score -= 1
    if score <= 0:
        return "low"
    return "high" if score >= 3 else "mid"


def difficulty_adjust(weights: "dict | None", difficulty: str) -> dict:
    """Tilt the user's weights by predicted difficulty, then renormalize.

    Easy prompt: capability matters less, cost more — route DOWN with
    confidence. Hard prompt: capability dominates — the marginal gain of a
    frontier model is real. The user's weights stay the baseline; this is a
    per-prompt tilt, never a persisted change.
    """
    w = normalize_weights(weights)
    difficulty = _EFFORT_ALIAS.get(difficulty, difficulty)
    if difficulty == "low":
        w = {"cost": w["cost"] * 1.5, "cap": w["cap"] * 0.55,
             "speed": w["speed"] * 1.2}
    elif difficulty == "high":
        # Strong tilt: the cost axis spans the full 0-100 normalized range,
        # so a timid cap multiplier can't overcome a cheap model's built-in
        # ~100-point cost advantage. x3.0 makes capability decisive at
        # default weights while a deliberate cost=0.9 user still routes
        # cheap — their stated preference survives the tilt.
        w = {"cost": w["cost"] * 0.35, "cap": w["cap"] * 3.0,
             "speed": w["speed"] * 0.75}
    elif difficulty == "xhigh":
        w = {"cost": w["cost"] * 0.15, "cap": w["cap"] * 5.0,
             "speed": w["speed"] * 0.55}
    elif difficulty == "max":
        # Capability all but decides alone — cost/speed only break ties.
        w = {"cost": w["cost"] * 0.04, "cap": w["cap"] * 12.0,
             "speed": w["speed"] * 0.30}
    return normalize_weights(w)


# ---------------------------------------------------------------------------
# Picking
# ---------------------------------------------------------------------------

def _blend_price(row: dict, cohort: str) -> "float | None":
    p = (row or {}).get("pricing") or {}
    try:
        i = float(p.get("prompt_usd_per_1m") or 0)
        o = float(p.get("completion_usd_per_1m") or 0)
    except (TypeError, ValueError):
        return None
    if i == 0 and o == 0:
        return None
    wi = 0.85 if cohort in ("agentic", "coding", "long-context") else 0.75
    return wi * i + (1 - wi) * o


def normalize_weights(w: "dict | None") -> dict:
    """Normalize cost/cap/speed to sum 1. Garbage falls back to defaults."""
    out = dict(DEFAULT_WEIGHTS)
    for k in out:
        try:
            out[k] = max(0.0, float((w or {}).get(k, out[k])))
        except (TypeError, ValueError):
            pass
    total = sum(out.values()) or 1.0
    return {k: v / total for k, v in out.items()}


ESCALATION_LADDER = ("low", "mid", "high", "xhigh", "max")


def escalate(level: str, steps: int = 1) -> str:
    """Next level up the effort ladder — the cascade's escalation step."""
    lvl = _EFFORT_ALIAS.get(level, level)
    try:
        i = ESCALATION_LADDER.index(lvl)
    except ValueError:
        i = 1
    return ESCALATION_LADDER[min(len(ESCALATION_LADDER) - 1, i + steps)]


def de_escalate(level: str, floor: str = "low") -> str:
    """One rung DOWN, never below `floor`.

    A turn that opens hard often becomes routine (design the thing, then
    write six boilerplate files). Without this, one escalation taxes the
    entire rest of the turn at frontier prices.
    """
    lvl = _EFFORT_ALIAS.get(level, level)
    fl = _EFFORT_ALIAS.get(floor, floor)
    try:
        i = ESCALATION_LADDER.index(lvl)
        f = ESCALATION_LADDER.index(fl)
    except ValueError:
        return lvl
    return ESCALATION_LADDER[max(f, i - 1)]


def effective_weights(w: "dict | None", level: str) -> dict:
    """The weights a pick actually uses: user weights tilted by effort."""
    return difficulty_adjust(w, _EFFORT_ALIAS.get(level, level))


def pick(cohort: str, weights: "dict | None", table: "dict | None",
         catalog: "list | None", *, needs_tools: bool = False,
         needs_ctx: int = 0, incumbent: "str | None" = None,
         exclude: "set | None" = None,
         incumbent_bonus: float = 0.0) -> "dict | None":
    """Choose a model for `cohort`. Returns {model, cohort, ranked, sticky}
    or None (caller falls back). Pure — no I/O, no mutation."""
    try:
        if not table or not catalog:
            return None
        frontier = (table.get("frontiers") or {}).get(cohort) or []
        models = table.get("models") or {}
        cat_by_id = {m.get("id"): m for m in catalog}
        w = normalize_weights(weights)

        exclude = exclude or set()
        if incumbent in exclude:
            incumbent = None
        cands = []
        for mid in frontier:
            if mid in exclude:
                continue  # session-blacklisted: it already failed us live
            row = models.get(mid)
            cat = cat_by_id.get(mid)
            if not row or not cat:
                continue  # table/catalog drift — skip, never crash
            caps = row.get("caps") or {}
            if needs_tools and not caps.get("tools"):
                continue
            if needs_ctx and (row.get("ctx") or 0) < needs_ctx:
                continue
            q = (row.get("scores") or {}).get(cohort)
            cost = _blend_price(cat, cohort)
            if q is None or cost is None:
                continue
            cands.append((mid, float(q), cost, float(row.get("speed") or 30)))
        qualified = [c for c in cands if c[1] >= QUALITY_FLOOR]
        if qualified:
            cands = qualified
        if not cands:
            # The fail-safe default obeys the SAME feasibility rules — a
            # default that can't hold tools or fit the context is worse
            # than falling back to the caller's pinned model.
            default = (table.get("defaults") or {}).get(cohort)
            if not default or default in exclude:
                return None
            drow = models.get(default) or {}
            dcaps = drow.get("caps") or {}
            if needs_tools and not dcaps.get("tools"):
                return None
            if needs_ctx and (drow.get("ctx") or 0) < needs_ctx:
                return None
            return {"model": default, "cohort": cohort, "ranked": [],
                    "sticky": False}

        max_cost = max(c for _, _, c, _ in cands)
        denom = math.log10(max_cost + 1) or 1.0

        def score(t):
            mid, q, cost, sp = t
            cost_n = 100 * (1 - math.log10(cost + 1) / denom)
            base = w["cost"] * cost_n + w["cap"] * q + w["speed"] * sp
            # Switching models discards the provider's prompt cache and
            # re-sends the whole history. The incumbent therefore carries a
            # bonus proportional to how much cached context is at stake —
            # a marginal score win shouldn't pay a real cache bill.
            if incumbent_bonus and mid == incumbent:
                base += incumbent_bonus
            return base

        ranked = sorted(cands, key=score, reverse=True)
        top = [t[0] for t in ranked[:3]]
        # Session stickiness: keep the incumbent while it's still top-3 —
        # mid-conversation model flapping costs prompt-cache hits and sanity.
        if incumbent and incumbent in top:
            return {"model": incumbent, "cohort": cohort, "sticky": True,
                    "ranked": _ranked_view(ranked, score)}
        return {"model": ranked[0][0], "cohort": cohort, "sticky": False,
                "ranked": _ranked_view(ranked, score)}
    except Exception:
        return None  # fail open, always


def _ranked_view(ranked: list, score) -> list:
    return [{"model": t[0], "score": round(score(t), 1), "cap": round(t[1]),
             "cost": round(t[2], 2), "speed": round(t[3])} for t in ranked[:3]]


# ---------------------------------------------------------------------------
# Scored picking — a model AND a thinking level, by expected cost to finish
# ---------------------------------------------------------------------------
# For coding work the pick comes from `model_scores.json` (see scores.py):
# one entry per model and thinking level, each with a measured chance of
# finishing an everyday task and a hard one, and what an attempt costs in
# dollars and minutes. The pick is the entry that is cheapest to FINISH with:
#
#     expected cost = cost of the attempt
#                   + minutes of waiting x value of a minute
#                   + chance it fails x what a rescue costs
#
# Everything is in dollars, so nothing arbitrary is added together. The
# chance of failure uses score minus margin, so thin evidence ranks lower by
# itself. A rescue is priced as running the strongest available entry.
# Other kinds of prompt (vision, writing, research…) are not covered by these
# scores and stay on the table above. `choose()` is the one entry point.

SCORED_COHORTS = ("agentic", "coding")
TIME_VALUE = 0.05        # dollars per minute of waiting, at the default speed weight
SCORED_FLOOR = 0.6       # never pick under 60% of the best chance available
STICKY_MARGIN = 0.10     # keep the serving model while it is within 10% of the best
_LEVEL_ORDER = ("default", "none", "low", "medium", "high", "xhigh", "max")


def _band(difficulty: str) -> str:
    d = _EFFORT_ALIAS.get(difficulty, difficulty)
    return d if d in ESCALATION_LADDER else "mid"


def _chance(e: dict, band: str, cautious: bool = True) -> float:
    """Chance (0-1) this entry finishes a task of this difficulty. Easy work
    is judged on the everyday score, hard work on the hard score, the middle
    on both."""
    def part(k):
        return (e[k]["score"] - (e[k]["margin"] if cautious else 0)) / 100.0
    if band == "low":
        s = part("everyday")
    elif band == "mid":
        s = (part("everyday") + part("hard")) / 2
    else:
        s = part("hard")
    return max(0.0, min(1.0, s))


def _slider_scale(weights: "dict | None") -> dict:
    """The user's three weights as multipliers on the three terms: 1.0 each at
    the defaults, so the defaults mean plain dollars."""
    w = normalize_weights(weights)
    return {k: w[k] / DEFAULT_WEIGHTS[k] for k in DEFAULT_WEIGHTS}


def _expected(e: dict, band: str, k: dict, rescue: float) -> dict:
    attempt = k["cost"] * e["cost_per_task"]
    wait = k["speed"] * TIME_VALUE * e["minutes_per_task"]
    # one rung past "high" leans harder on capability before "max" decides alone
    risk = k["cap"] * (2.0 if band == "xhigh" else 1.0) * (1.0 - _chance(e, band)) * rescue
    return {"total": attempt + wait + risk, "attempt": attempt, "wait": wait, "risk": risk}


def _live_entries(doc, catalog, exclude, needs_tools, needs_ctx, level) -> list:
    """Entries for models the gateway offers right now, priced at today's
    rates and able to do the job at hand."""
    from . import scores as _scores
    cat = {m.get("id"): m for m in catalog or [] if isinstance(m, dict)}
    out = []
    for e in _scores.entries(doc, available=set(cat), exclude=exclude or ()):
        row = cat[e["model"]]
        if needs_tools and row.get("supports_tools") is False:
            continue
        ctx = row.get("context_length") or row.get("context_window") or 0
        if needs_ctx and ctx and ctx < needs_ctx:
            continue
        # The file's cost was measured at the file's token prices; if the
        # gateway's price has moved since, move the cost with it.
        filed = (doc.get("models") or {}).get(e["model"]) or {}
        then = _blend(filed.get("price_in_per_1m"), filed.get("price_out_per_1m"))
        now = _blend_price(row, "agentic")
        if then and now:
            e["cost_per_task"] *= max(0.25, min(4.0, now / then))
        out.append(e)
    if level:
        # The user fixed the thinking level: each model is judged by its entry
        # at that level, or the nearest level it was tested at, and the user's
        # level is what gets sent.
        want = _LEVEL_ORDER.index(level) if level in _LEVEL_ORDER else None
        best: dict = {}
        for e in out:
            d = abs(_LEVEL_ORDER.index(e["level"]) - want) if want is not None else 0
            if e["model"] not in best or d < best[e["model"]][0]:
                best[e["model"]] = (d, e)
        out = [dict(e, effort=level, level=level) for _, e in best.values()]
    return out


def _blend(i, o) -> "float | None":
    try:
        i, o = float(i or 0), float(o or 0)
    except (TypeError, ValueError):
        return None
    return (0.85 * i + 0.15 * o) or None


def pick_scored(difficulty: str, weights: "dict | None", doc: "dict | None",
                catalog: "list | None", *, needs_tools: bool = False,
                needs_ctx: int = 0, incumbent: "str | None" = None,
                exclude: "set | None" = None, incumbent_bonus: float = 0.0,
                failed: "tuple | None" = None,
                level: "str | None" = None) -> "dict | None":
    """Choose a model and thinking level for coding work, or None when the
    scores cover nothing the gateway offers (the caller falls back).

    `failed` = (model, level) that was just serving and visibly struggled:
    only entries clearly stronger than it are considered — their cautious
    chance must beat its full score. `level` = a thinking level the user
    fixed. Pure — no I/O, no mutation of its inputs."""
    try:
        band = _band(difficulty)
        ents = _live_entries(doc, catalog, exclude, needs_tools, needs_ctx, level)
        if not ents:
            return None
        k = _slider_scale(weights)
        strongest = max(ents, key=lambda e: (_chance(e, "high"), -e["cost_per_task"]))
        rescue = strongest["cost_per_task"] + TIME_VALUE * strongest["minutes_per_task"]

        cands = ents
        if failed:
            was = next((e for e in ents if (e["model"], e["level"]) == tuple(failed)), None)
            cands = [e for e in ents if (e["model"], e["level"]) != tuple(failed)]
            if was is not None:
                bar = _chance(was, band, cautious=False)
                stronger = [e for e in cands if _chance(e, band) > bar]
                # nothing clearly stronger: the strongest that is left
                cands, band = (stronger, band) if stronger else (cands, "max")
            if not cands:
                return None

        if band == "max":
            ranked = sorted(cands, key=lambda e: (-_chance(e, "high"), e["cost_per_task"]))
            chosen, sticky = ranked[0], False
            costs = {id(e): _expected(e, "high", k, rescue) for e in ranked}
        else:
            best = max(_chance(e, band) for e in cands)
            able = [e for e in cands if _chance(e, band) >= SCORED_FLOOR * best] or cands
            costs = {id(e): _expected(e, band, k, rescue) for e in able}
            ranked = sorted(able, key=lambda e: costs[id(e)]["total"])
            chosen, sticky = ranked[0], False
            # Switching models throws away the provider's prompt cache and
            # re-sends the whole history, so the serving model keeps the turn
            # while its best entry is close to the best there is — closer
            # still counts the more context is at stake.
            if incumbent and incumbent != chosen["model"]:
                mine = next((e for e in ranked if e["model"] == incumbent), None)
                slack = 1.0 + STICKY_MARGIN + max(0.0, incumbent_bonus) / 100.0
                if mine and costs[id(mine)]["total"] <= costs[id(chosen)]["total"] * slack:
                    chosen, sticky = mine, True

        def view(e):
            c = costs[id(e)]
            return {"model": e["model"], "level": e["level"],
                    "chance": round(_chance(e, "high" if band == "max" else band) * 100),
                    "cost": round(e["cost_per_task"], 2),
                    "minutes": round(e["minutes_per_task"], 1),
                    "expected": round(c["total"], 2)}
        top = [chosen] + [e for e in ranked if e is not chosen]
        return {"model": chosen["model"], "level": chosen["level"],
                "effort": chosen["effort"], "scored": True, "sticky": sticky,
                "cohort": "agentic", "ranked": [view(e) for e in top[:5]]}
    except Exception:
        return None  # fail open, always


def choose(cohort: str, difficulty: str, weights: "dict | None",
           table: "dict | None", catalog: "list | None", *,
           needs_tools: bool = False, needs_ctx: int = 0,
           incumbent: "str | None" = None, exclude: "set | None" = None,
           incumbent_bonus: float = 0.0, failed: "tuple | None" = None,
           level: "str | None" = None, scores_doc: "dict | None" = None) -> "dict | None":
    """The router's one entry point. Coding work is picked from the per-level
    scores; anything they do not cover — another kind of prompt, or an account
    that offers none of the scored models — falls back to the routing table,
    which picks a model only."""
    if cohort in SCORED_COHORTS:
        if scores_doc is None:
            from . import scores as _scores
            scores_doc = _scores.load()
        got = pick_scored(difficulty, weights, scores_doc, catalog,
                          needs_tools=needs_tools, needs_ctx=needs_ctx,
                          incumbent=incumbent, exclude=exclude,
                          incumbent_bonus=incumbent_bonus, failed=failed, level=level)
        if got:
            got["cohort"] = cohort
            return got
    return pick(cohort, effective_weights(weights, difficulty), table, catalog,
                needs_tools=needs_tools, needs_ctx=needs_ctx, incumbent=incumbent,
                exclude=exclude, incumbent_bonus=incumbent_bonus)


# ---------------------------------------------------------------------------
# Reply verification — the cheap "was that answer any good?" check
# ---------------------------------------------------------------------------
# A predictive router picks a model; a verifier notices when the pick was
# wrong and the answer proves it. Deterministic and free: no judge model, no
# extra request. Only clear-cut failures count — a false positive escalates
# a perfectly good answer to an expensive model, which is worse than missing
# a weak one.

_REFUSAL_RE = re.compile(
    r"\b(?:i (?:don'?t|do not) have (?:the )?(?:tools?|access|ability|capability)"
    r"|i'?m (?:sorry|afraid)[, ].{0,40}\b(?:can'?t|cannot|unable)"
    r"|i (?:can'?t|cannot) (?:help with|assist with|do) that"
    r"|as an ai(?: language)? model[, ]"
    r"|i (?:am|'m) (?:unable|not able) to)\b", re.I)

# "I created/wrote/updated the file" — a claim that MUST be backed by a tool
# call. Said without one, the model is hallucinating work it never did.
_CLAIM_RE = re.compile(
    r"\b(?:i(?:'?ve| have)?\s+(?:created|wrote|written|added|updated|saved|"
    r"generated|implemented|installed|ran|executed)"
    r"|(?:file|script|module|directory|folder)\s+(?:has been|was)\s+"
    r"(?:created|written|saved|updated))\b", re.I)

_ACTION_ASK_RE = re.compile(
    r"\b(?:create|write|make|build|add|implement|run|execute|install|fix|"
    r"update|delete|rename|refactor|generate)\b", re.I)


def verify_reply(reply: str, *, tool_calls_this_turn: int,
                 user_asked_action: bool) -> "str | None":
    """Return a short reason when the reply looks like a routing failure.

    Two clear-cut signals only:
      refusal    — the model declined or claimed it lacks tools it was given
      hallucination — it claims to have DONE work while calling no tool
    Anything else returns None (accept the answer).
    """
    text = (reply or "").strip()
    if not text:
        return None                      # empty is handled by the retry ladder
    if _REFUSAL_RE.search(text):
        return "the model refused or claimed it lacks tools"
    if (user_asked_action and tool_calls_this_turn == 0
            and _CLAIM_RE.search(text)):
        return "the model claimed work it never performed"
    return None


def asks_for_action(text: str) -> bool:
    """True when the user's message requests real work (not a question)."""
    return bool(_ACTION_ASK_RE.search(text or ""))
