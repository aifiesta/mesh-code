"""Client-side cost: the gateway's billed figure first, catalog estimate second.

The gateway does **not** return a `cost` field on chat completions — not in
the SSE stream, not on the non-streaming body, on either API version
(2026-08 and 2026-09; re-verified live 2026-09-22). What it DOES do is bill
every request immediately and expose the figure keyed by the `x-request-id`
it returned: `GET /v1/usage/requests/{request_id}` answers with `cost_usd`
~250ms after the stream ends, streaming included. That is the authoritative
number and `fetch_request_cost` fetches it per hop.

When that lookup fails (offline, timeout, a request the gateway has not
settled yet) we still have everything needed to compute a close estimate:
`usage` comes back on every request, and `GET /v1/models` carries full
per-dimension pricing including the user's own `discount_pct`. Estimates
are labelled "~" in the UI because the gateway never confirmed them; the
arithmetic is the same the dashboard bills on: token counts × the model's
own per-1M rates, with cached prompt tokens at the cache-read rate.
"""
from __future__ import annotations


def _dec(value) -> "float | None":
    """Catalog prices are decimal STRINGS ("5.00000000") or null."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def find_model(model_id: str, models: "list | None") -> "dict | None":
    """Catalog row for a model id, tolerating the resolved-id suffix.

    The `model` echoed back is often the upstream's own build string
    ("gpt-4o-mini-2024-07-18") rather than the catalog id
    ("openai/gpt-4o-mini"), so fall back to a suffix match on the last path
    segment before giving up.
    """
    if not model_id or not models:
        return None
    for m in models:
        if m.get("id") == model_id:
            return m
    tail = model_id.rsplit("/", 1)[-1]
    best = None
    for m in models:
        mid = m.get("id") or ""
        mtail = mid.rsplit("/", 1)[-1]
        if mtail == tail:
            return m
        # "gpt-4o-mini-2024-07-18" starts with "gpt-4o-mini" — but only at a
        # SEPARATOR boundary. A bare startswith matched "gpt-4o-2024-08-06"
        # against "gpt-4" and billed gpt-4o at gpt-4's rate; requiring the
        # next char to be "-"/"." stops the shorter sibling from winning.
        if (mtail and (tail == mtail
                       or tail.startswith(mtail + "-")
                       or tail.startswith(mtail + "."))
                and (best is None or len(mtail) > len(best[0]))):
            best = (mtail, m)
    return best[1] if best else None


def estimate_cost(model_id: str, usage: dict,
                  models: "list | None") -> "float | None":
    """USD for one request, or None when it can't be computed honestly.

    None (rather than 0.0) whenever the model isn't in the catalog or has no
    token pricing — a confident $0.000000 would be a lie, and the caller
    omits the segment instead.
    """
    if not usage:
        return None
    row = find_model(model_id, models)
    if not row:
        return None
    pricing = row.get("pricing") or {}
    prompt_rate = _dec(pricing.get("prompt_usd_per_1m"))
    completion_rate = _dec(pricing.get("completion_usd_per_1m"))
    if prompt_rate is None and completion_rate is None:
        # Non-token pricing (per-image, per-video) — not a chat cost.
        return None
    prompt_rate = prompt_rate or 0.0
    completion_rate = completion_rate or 0.0

    try:
        prompt_t = int(usage.get("prompt_tokens") or 0)
        completion_t = int(usage.get("completion_tokens") or 0)
    except (TypeError, ValueError):
        return None

    # Cached prompt tokens bill at the cache-read rate when the model has
    # one; they are a SUBSET of prompt_tokens, so discount the difference
    # rather than adding on top.
    cached_t = 0
    details = usage.get("prompt_tokens_details") or {}
    try:
        cached_t = int(details.get("cached_tokens") or 0)
    except (TypeError, ValueError):
        cached_t = 0
    cached_t = max(0, min(cached_t, prompt_t))
    cache_rate = _dec(pricing.get("cache_read_input_usd_per_1m"))

    fresh_t = prompt_t - cached_t
    cost = (fresh_t * prompt_rate) / 1_000_000
    cost += (cached_t * (cache_rate if cache_rate is not None else prompt_rate)) / 1_000_000
    cost += (completion_t * completion_rate) / 1_000_000

    # Auto-routing bills the classifier too — the gateway reports its tokens
    # separately, and they are NOT included in prompt_tokens.
    for key, rate in (("classifier_prompt_tokens", prompt_rate),
                      ("classifier_completion_tokens", completion_rate)):
        try:
            extra = int(usage.get(key) or 0)
        except (TypeError, ValueError):
            extra = 0
        if extra > 0:
            cost += (extra * rate) / 1_000_000

    discount = _dec(pricing.get("discount_pct"))
    if discount:
        cost *= max(0.0, 1.0 - discount / 100.0)
    return cost


def _fetch_request_events(cfg: dict, request_ids: list,
                          timeout: float = 3.0) -> list:
    """The billing record of each settled request id (those with a cost)."""
    if not request_ids:
        return []
    try:
        import httpx
    except Exception:
        return []
    base = cfg["base_url"].rstrip("/")
    headers = {"Authorization": f"Bearer {cfg['api_key']}"}
    events = []
    for rid in request_ids:
        if not rid:
            continue
        try:
            r = httpx.get(f"{base}/usage/requests/{rid}", headers=headers,
                          timeout=timeout)
            if r.status_code >= 400:
                continue
            ev = (r.json() or {}).get("event") or {}
            if ev.get("cost_usd") is None:
                continue
            float(ev["cost_usd"])
            events.append(ev)
        except Exception:
            continue
    return events


def fetch_request_cost(cfg: dict, request_ids: list,
                       timeout: float = 3.0) -> "float | None":
    """Billed USD for one hop from GET /usage/requests/{id}, summed over ids.

    A hop can carry several ids (optimized attempt degraded to raw); every
    accepted attempt was billed. None only when no id could be settled.
    """
    events = _fetch_request_events(cfg, request_ids, timeout)
    return sum(float(ev["cost_usd"]) for ev in events) if events else None


# ---------------------------------------------------------------------------
# Where a bill goes.
#
# Total tokens and total dollars hide the thing that decides what is worth
# optimizing: input the provider has cached costs a few percent of fresh
# input (DeepSeek: $0.006 against $0.20 per 1M), output costs several times
# input, and thinking is billed as output. A run can halve its tokens and
# lose a fifth of its bill. So every step reports the split.
#
# A "bill" is a flat dict so two of them add up key by key:
#   *_tokens / *_usd for fresh (input billed in full), cached (input read
#   from the provider's cache), cache_write (input written to it, usually
#   dearer than fresh), output (everything the model wrote, thinking
#   included), thinking (the part of output that was reasoning — a SHARE of
#   output, never added on top), other_usd (anything billed that is not
#   tokens, e.g. a tool fee), and `estimated` (the split was computed from
#   catalog rates, not read from the gateway's bill).

BILL_PARTS = ("fresh", "cached", "cache_write", "output", "thinking")


def empty_bill() -> dict:
    bill = {f"{part}_{unit}": 0 for part in BILL_PARTS for unit in ("tokens", "usd")}
    for part in BILL_PARTS:
        bill[f"{part}_usd"] = 0.0
    bill["other_usd"] = 0.0
    bill["estimated"] = False
    return bill


def add_bill(total: dict, part: "dict | None") -> dict:
    """total += part, in place. Returns total."""
    if not part:
        return total
    for key, value in part.items():
        if key == "estimated":
            total["estimated"] = bool(total.get("estimated")) or bool(value)
        elif isinstance(value, (int, float)):
            total[key] = total.get(key, 0) + value
    return total


def round_bill(bill: dict) -> dict:
    """A copy fit for an event: dollars to 8 places, tokens whole."""
    return {k: (v if isinstance(v, bool) else round(v, 8) if k.endswith("_usd") else int(v))
            for k, v in bill.items()}


def bill_total(bill: dict) -> float:
    """Dollars in the bill. Thinking is inside output, so it is not added."""
    return sum(float(bill.get(f"{p}_usd") or 0) for p in ("fresh", "cached", "cache_write", "output")) \
        + float(bill.get("other_usd") or 0)


def _int(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def usage_tokens(usage: "dict | None") -> dict:
    """Token counts by part from a chat completion's `usage`. Cached and
    cache-written tokens are SUBSETS of prompt_tokens; thinking is a subset
    of completion_tokens."""
    usage = usage or {}
    prompt, completion = _int(usage.get("prompt_tokens")), _int(usage.get("completion_tokens"))
    details = usage.get("prompt_tokens_details") or {}
    cached = min(_int(details.get("cached_tokens")), prompt)
    written = sum(_int(v) for k, v in details.items() if str(k).startswith("cache_write"))
    written = min(written, prompt - cached)
    thinking = min(_int((usage.get("completion_tokens_details") or {}).get("reasoning_tokens")), completion)
    return {"fresh": prompt - cached - written, "cached": cached, "cache_write": written,
            "output": completion, "thinking": thinking}


def _line_part(kind) -> "str | None":
    kind = str(kind or "").lower()
    if kind == "input":
        return "fresh"
    if kind.startswith("cache_read") or kind == "cached_input":
        return "cached"
    if kind.startswith("cache_write") or kind.startswith("cache_creation"):
        return "cache_write"
    if kind == "output":
        return "output"
    return None


def bill_from_events(events: list, usage: "dict | None" = None) -> "dict | None":
    """The split as the gateway BILLED it, from each request's
    `cost_breakdown.lines`. Every part is scaled so the parts add up to the
    billed figure exactly (an account discount or rounding is spread over
    the lines in proportion). None when no event carries a breakdown."""
    bill, seen = empty_bill(), False
    for ev in events or []:
        lines = ((ev.get("cost_breakdown") or {}).get("lines")) or []
        if not lines:
            continue
        try:
            cost = float(ev["cost_usd"])
        except (KeyError, TypeError, ValueError):
            continue
        charged = [l for l in lines if l.get("charged") is not False]
        listed = sum(_dec(l.get("amount_usd")) or 0.0 for l in charged)
        scale = cost / listed if listed > 0 else 0.0
        for line in charged:
            part = _line_part(line.get("kind"))
            usd = (_dec(line.get("amount_usd")) or 0.0) * scale
            if part is None:
                bill["other_usd"] += usd
            else:
                bill[f"{part}_usd"] += usd
                bill[f"{part}_tokens"] += _int(line.get("tokens"))
        if listed <= 0:
            bill["other_usd"] += cost       # billed, but not through any line
        seen = True
    if not seen:
        return None
    _set_thinking(bill, usage)
    return bill


def _set_thinking(bill: dict, usage: "dict | None") -> None:
    """Thinking is billed as output; show how much of output it was."""
    thinking = usage_tokens(usage)["thinking"]
    out = bill["output_tokens"]
    thinking = min(thinking, out)
    bill["thinking_tokens"] = thinking
    bill["thinking_usd"] = bill["output_usd"] * thinking / out if out else 0.0


def bill_from_usage(model_id: str, usage: "dict | None", models: "list | None",
                    cost: float = 0.0) -> dict:
    """The split ESTIMATED from token counts and the catalog's rates, for a
    step the gateway gave no breakdown for. When the step's total is known
    (`cost`), the parts are scaled to add up to it; marked estimated either
    way. Tokens are always real."""
    bill = empty_bill()
    bill["estimated"] = True
    tokens = usage_tokens(usage)
    for part in ("fresh", "cached", "cache_write", "output"):
        bill[f"{part}_tokens"] = tokens[part]
    pricing = (find_model(model_id, models) or {}).get("pricing") or {}
    prompt_rate = _dec(pricing.get("prompt_usd_per_1m")) or 0.0
    out_rate = _dec(pricing.get("completion_usd_per_1m")) or 0.0
    read_rate = _dec(pricing.get("cache_read_input_usd_per_1m"))
    write_rate = _dec(pricing.get("cache_write_input_usd_per_1m"))
    rates = {"fresh": prompt_rate, "output": out_rate,
             "cached": read_rate if read_rate is not None else prompt_rate,
             "cache_write": write_rate if write_rate is not None else prompt_rate}
    listed = {part: tokens[part] * rates[part] / 1_000_000 for part in rates}
    total = sum(listed.values())
    scale = (cost / total) if (cost and cost > 0 and total > 0) else 1.0
    for part, usd in listed.items():
        bill[f"{part}_usd"] = usd * scale
    if cost and cost > 0 and total <= 0:
        bill["other_usd"] = cost            # billed, but the catalog has no rates to split it
    _set_thinking(bill, usage)
    return bill


def fetch_request_bill(cfg: dict, request_ids: list, usage: "dict | None" = None,
                       timeout: float = 3.0) -> tuple:
    """(billed USD or None, bill or None) for one hop, from one lookup per
    request id. The bill is None when the gateway settled the request but
    sent no breakdown."""
    events = _fetch_request_events(cfg, request_ids, timeout)
    if not events:
        return None, None
    return sum(float(ev["cost_usd"]) for ev in events), bill_from_events(events, usage)


def fetch_actual_costs(cfg: dict, request_ids: list, since: str,
                       timeout: float = 6.0) -> "dict | None":
    """Authoritative per-request cost from POST /v1/usage/events.

    The gateway bills each request and exposes the figure keyed by the
    `x-request-id` it returned — available immediately, streaming included
    (verified live: 0s lag). We ask for everything since the turn started
    and keep only the ids we actually issued, so another process using the
    same key can't inflate this turn's number.

    Quirk worth knowing: the schema marks `org_id` required, but the docs
    state it is *ignored* for API-key callers — an empty string satisfies
    validation and the scope stays server-derived to this key alone.

    Returns {request_id: float} (possibly partial), or None on any failure —
    the caller then keeps its computed estimate.
    """
    if not request_ids:
        return None
    try:
        import httpx
        r = httpx.post(
            f"{cfg['base_url'].rstrip('/')}/usage/events",
            headers={"Authorization": f"Bearer {cfg['api_key']}",
                     "Content-Type": "application/json"},
            json={"org_id": "", "since": since, "limit": 200},
            timeout=timeout,
        )
        if r.status_code >= 400:
            return None
        wanted = set(request_ids)
        out = {}
        for ev in (r.json() or {}).get("events") or []:
            rid = ev.get("request_id")
            if rid in wanted and ev.get("cost_usd") is not None:
                try:
                    out[rid] = float(ev["cost_usd"])
                except (TypeError, ValueError):
                    pass
        return out or None
    except Exception:
        return None
