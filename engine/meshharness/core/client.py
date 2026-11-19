"""Streaming OpenAI-compatible HTTP client for Mesh API."""
import json
import re
import time
from typing import Iterable, Optional

import httpx

from . import caching
from .optimize import prepare


def _is_complete_json(s: str) -> bool:
    if not s.strip():
        return False
    try:
        json.loads(s)
        return True
    except json.JSONDecodeError:
        return False


class ToolCallAccumulator:
    """Accumulate streamed tool_call deltas into complete calls.

    OpenAI-spec providers key every delta by `index`, but Mesh fronts many
    providers and not all of them play by the book. Observed misbehaviors,
    each of which used to surface as a doomed call like
    `write_file: (missing path) (0 chars)`:

      - deltas with no `index` at all — the old `tc.get("index", 0)` merged
        parallel calls into one bucket, concatenating their JSON arguments
        into garbage like `{"path":"a"}{"path":"b"}`
      - argument fragments arriving under a different index than the call's
        name — a named call with empty args plus an orphan holding the args
      - missing `id`s — replaying the assistant message with `"id": ""`
        breaks the tool-result pairing on strict providers next hop
      - nameless buckets that can't be executed or replayed

    Routing rules: an explicit `index` always wins; a known `id` continues
    its call; a new `id` starts a new call; a keyless/indexless fragment
    belongs to the call currently in flight. `finalize()` then repairs
    orphans, drops the unexecutable, and synthesizes missing ids.
    """

    def __init__(self):
        self._by_index: dict = {}
        self._order: list = []    # buckets in arrival order
        self._current = None      # last bucket touched — target for bare fragments
        self.dropped = 0          # buckets finalize() had to discard (forensics)

    def add(self, tc: dict) -> None:
        idx = tc.get("index")
        tc_id = tc.get("id") or ""
        fn = tc.get("function") or {}
        if isinstance(idx, int):
            bucket = self._by_index.get(idx)
            if bucket is None:
                bucket = self._new_bucket(idx)
        elif tc_id:
            bucket = next((b for b in self._order if b["id"] == tc_id), None)
            if bucket is None:  # new id, no index: a new call is starting
                bucket = self._new_bucket(self._next_free_index())
        else:  # continuation fragment — belongs to the call in flight
            bucket = self._current or self._new_bucket(0)
        if tc_id:
            bucket["id"] = tc_id
        if fn.get("name"):
            bucket["name"] = fn["name"]
        if fn.get("arguments"):
            bucket["arguments"] += fn["arguments"]
        self._current = bucket

    def _new_bucket(self, idx: int) -> dict:
        b = {"id": "", "name": "", "arguments": "", "_idx": idx}
        self._by_index[idx] = b
        self._order.append(b)
        return b

    def _next_free_index(self) -> int:
        i = len(self._by_index)
        while i in self._by_index:
            i += 1
        return i

    def finalize(self) -> list:
        """Return completed calls, repairing what can be repaired.

        Two passes so a donor fragment at a LOWER index than its named call
        is not lost (the old single-pass walk processed it before any named
        call existed and silently dropped it — seen live as a named call
        with 0-char args):
        1. collect named buckets;
        2. merge every nameless bucket that carries arguments (id'd or not)
           into the nearest-by-index named call whose args do NOT already
           parse. Merge direction is acceptance-driven: try target+donor,
           then donor+target (lower-index donors may need prepending); if
           neither parses, append anyway — the cli-layer feedback loop
           handles what's left. A call whose args already parse is never
           touched — never corrupt a good call to rescue a broken one.

        Whatever is still discarded is counted in self.dropped (surfaced as
        meta['accum_dropped'] for forensics). Missing ids get a
        deterministic `call_<n>` so the assistant/tool pairing survives.
        """
        buckets = sorted(self._order, key=lambda b: b["_idx"])
        named = [b for b in buckets if b["name"]]
        donors = [b for b in buckets if not b["name"] and b["arguments"]]
        self.dropped = 0
        for o in donors:
            candidates = [c for c in named if not _is_complete_json(c["arguments"])]
            if not candidates:
                self.dropped += 1
                continue
            target = min(candidates, key=lambda c: abs(c["_idx"] - o["_idx"]))
            if _is_complete_json(target["arguments"] + o["arguments"]):
                target["arguments"] += o["arguments"]
            elif _is_complete_json(o["arguments"] + target["arguments"]):
                target["arguments"] = o["arguments"] + target["arguments"]
            else:
                target["arguments"] += o["arguments"]
        self.dropped += sum(
            1 for b in buckets if not b["name"] and not b["arguments"] and b["id"]
        )
        for n, c in enumerate(named):
            if not c["id"]:
                c["id"] = f"call_{n}"
            c.pop("_idx", None)
        return named


def build_payload(messages: list, cfg: dict, tools: Optional[list] = None) -> dict:
    """Build the /chat/completions request body from session config.

    Pure function (unit-testable without a network). Mesh extensions:
      - auto_route  -> model:"auto" (gateway Auto Router picks per prompt)
      - fallback_models -> `models` ordered fallback list
      - reasoning_effort (our setting) -> sent as `reasoning: {"effort": …}`
        when set ("none" is a real level; None means "don't send"). The
        gateway's older top-level `reasoning_effort` field is deprecated and
        refuses anything above "high"; `reasoning.effort` takes every level
        up to "max" (checked live 2026-11-17, docs/bench/model_scoring).
    """
    payload: dict = {
        "model": "auto" if cfg.get("auto_route") else cfg["model"],
        "messages": messages,
        "stream": True,
    }
    if cfg.get("fallback_models"):
        payload["models"] = list(cfg["fallback_models"])
    if cfg.get("reasoning_effort"):
        payload["reasoning"] = {"effort": cfg["reasoning_effort"]}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    return payload


# Thinking levels the settings offer, weakest to strongest.
REASONING_LEVELS = ("none", "low", "medium", "high", "xhigh", "max")

# The field named outright, in the spellings providers and the gateway use
# (two are a validation error's location list, ["body", "reasoning", "effort"],
# as re-serialised JSON and as raw body text).
_LEVEL_FIELD = ("reasoning.effort", "reasoning_effort", "reasoning effort",
                '"reasoning", "effort"', '"reasoning","effort"',
                "thinking.budget", "thinking_budget", "budget_tokens",
                "thinking.type")
# ...or the feature as a word of its own (NOT inside a model id such as
# "grok-4.1-fast-non-reasoning") next to a refusal phrase.
_LEVEL_WORD = re.compile(r"(?<![\w\-/.])(reasoning|thinking|effort)(?![\w\-])")
_REFUSAL = ("not supported", "unsupported", "does not support", "doesn't support",
            "not allowed", "invalid value", "unknown parameter", "unrecognized")
_ACCEPTED_AFTER = ("supported values", "input should be", "must be one of",
                   "expected one of", "allowed values")
_LEVEL_TOKEN = re.compile(r"\b(none|low|medium|high|xhigh|max)\b")


def reasoning_refusal(err) -> "dict | None":
    """Is this gateway error a model refusing the thinking level?

    Returns None when it is not, else {"accepted": [...]} — the levels the
    error itself says the model does take (empty when it does not say).

    The readable `message` often does not name the cause ("Provider returned
    error"); the evidence sits deeper in the error object — `param:
    reasoning.effort` from one provider, a raw upstream string naming
    `reasoning_effort` from another — so look at the whole thing. `err` may be
    the parsed error object or the raw text of an HTTP error body.
    """
    if isinstance(err, str):
        blob = err
    else:
        try:
            blob = json.dumps(err)
        except (TypeError, ValueError):
            blob = str(err)
    blob = blob.lower()
    named = any(k in blob for k in _LEVEL_FIELD)
    if not named and not (_LEVEL_WORD.search(blob)
                          and any(p in blob for p in _REFUSAL)):
        return None
    accepted: list = []
    for marker in _ACCEPTED_AFTER:
        at = blob.find(marker)
        if at >= 0:
            for lv in _LEVEL_TOKEN.findall(blob[at:at + 400]):
                if lv not in accepted:
                    accepted.append(lv)
            break
    return {"accepted": [lv for lv in REASONING_LEVELS if lv in accepted]}


def is_reasoning_rejection(err) -> bool:
    return reasoning_refusal(err) is not None


def nearest_level(wanted: str, accepted, refused=()) -> "str | None":
    """The accepted level closest to the one asked for, or None.

    Max on a model that tops out at High should run at High, and Off on a
    model that cannot switch thinking off should run at Low — not at the
    model's own default, which can be a long way from either. A tie goes to
    the lower, cheaper level.
    """
    if wanted not in REASONING_LEVELS:
        return None
    want = REASONING_LEVELS.index(wanted)
    pool = [lv for lv in REASONING_LEVELS
            if lv in (accepted or ()) and lv != wanted and lv not in refused]
    if not pool:
        return None
    return min(pool, key=lambda lv: (abs(REASONING_LEVELS.index(lv) - want),
                                     REASONING_LEVELS.index(lv)))


def _drop_empty_assistant(messages: list) -> list:
    """Remove assistant messages that carry neither text nor tool_calls. One
    such message — appended after an earlier empty/failed response — otherwise
    POISONS every later turn: the Bedrock/Anthropic backend rejects an empty
    text block with HTTP 200 + an in-band ValidationException ("text content
    blocks must be non-empty"), which the user experiences as the CLI silently
    hanging (?→? tok forever). Consecutive user messages ARE accepted by the
    gateway, so dropping these is safe."""
    def _blank(c) -> bool:
        # content is normally str or None here; a list (multimodal blocks) is
        # never "blank" — and calling .strip() on it would crash the stream.
        return c is None or (isinstance(c, str) and not c.strip())
    return [
        m for m in messages
        if not (
            m.get("role") == "assistant"
            and _blank(m.get("content"))
            and not m.get("tool_calls")
        )
    ]


def stream_chat(
    messages: list,
    cfg: dict,
    tools: Optional[list] = None,
) -> Iterable:
    """Yield content deltas, then a final dict with usage/cost/model/tool_calls.

    Mesh API is OpenAI-compatible:
      - `cost` arrives in the final SSE chunk alongside `usage`.
      - `tool_calls` arrive as deltas indexed by position; we accumulate them
        and surface as the meta dict's `tool_calls` field.

    A model that only caches when asked gets the two cache marks (see
    caching.py). When the `optimize` dial is set (BETA), the request is also
    rewritten by the phase 1 lever stack in optimize.py before sending, and
    the plan rides on the final meta dict as `optimize_plan`. If the gateway
    rejects a request changed in either way, we retry the raw request once,
    so neither can be the reason a turn fails.
    """
    url = f"{cfg['base_url']}/chat/completions"
    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
    }
    # Strip any empty assistant message BEFORE both the raw payload and the
    # optimize path — one poisons every subsequent turn (see the helper).
    messages = _drop_empty_assistant(messages)
    payload = build_payload(messages, cfg, tools)

    plan: dict = {}
    attempts = [payload]
    # Cache marks, on a copy: the raw payload and the stored history never
    # carry one. Not when the gateway picks the model ("auto"): which model
    # will answer is not known here.
    marked = messages
    if caching.asked_for() and caching.needs_marks(payload["model"]):
        marked = caching.mark(messages)
    dial = float(cfg.get("optimize") or 0)
    if dial > 0:
        # Pass the model string actually being sent ("auto" falls back to
        # optimize's default cache-minimum heuristic — harmless). Marks set
        # above count as the client's own, so prepare() adds none.
        opt_messages, extra, plan = prepare(
            marked, payload["model"], dial, has_tools=bool(tools)
        )
        if plan.get("levers_applied"):
            optimized = {**payload, **extra, "messages": opt_messages}
            attempts = [optimized, payload]  # raw payload is the fallback
    elif marked is not messages:
        attempts = [{**payload, "messages": marked}, payload]

    # A caller-forced output budget wins over every other lever — it exists
    # because the gateway already rejected this request for asking too much
    # (input + max_tokens > context window). Applied last, to every attempt,
    # so optimize's own max_tokens default can't re-trigger the same 400.
    forced_max = cfg.get("max_tokens")
    if forced_max:
        attempts = [{**body, "max_tokens": int(forced_max)} for body in attempts]

    last_meta: dict = {}
    last_model: str = ""
    accum = ToolCallAccumulator()
    dropped_chunks = 0   # SSE data lines that failed to json-parse (forensics —
    dropped_sample = ""  # nonzero implicates the gateway relay, not the model)
    progress_tool = ""   # spinner feed: which tool's arguments are streaming
    progress_chars = 0   # ...and how many argument chars have arrived so far
    request_ids: list = []  # every attempt's x-request-id (billing lookup)

    for attempt_index, body in enumerate(attempts):
        is_last_attempt = attempt_index == len(attempts) - 1
        retry_raw = False  # set if an in-band error should degrade to the raw request
        # Fresh accumulator + progress per attempt. A raw retry after an
        # optimized attempt streamed some content/tool-call deltas used to
        # reuse these: buf concatenated both attempts' text, and accum merged
        # attempt-1 fragments with attempt-2 deltas by index into invalid JSON.
        accum = ToolCallAccumulator()
        progress_tool = ""
        progress_chars = 0
        with httpx.stream("POST", url, json=body, headers=headers, timeout=120) as r:
            if r.status_code >= 400:
                r.read()  # so e.response.text works in the caller
                if not is_last_attempt:
                    # Optimized request rejected; degrade to the raw request.
                    plan = {
                        "dial": dial,
                        "levers_applied": [],
                        "degraded": f"gateway returned {r.status_code}, sent raw request",
                    }
                    continue
            r.raise_for_status()
            # The consumer closes this from its own thread on interrupt; that is
            # the only way to unblock iter_lines() while the wire is silent.
            yield {"stream_response": r}
            # When auto-routed, the gateway names the concrete model it
            # picked in this header; SSE chunks' `model` agrees, but the
            # header is the authoritative belt-and-braces.
            resolved = r.headers.get("x-resolved-model-id")
            if resolved:
                last_model = resolved
            # The gateway's own id for this request — the key that unlocks
            # its authoritative cost via POST /v1/usage/events.
            rid = r.headers.get("x-request-id")
            if rid:
                last_meta["request_id"] = rid
                request_ids.append(rid)
            for line in r.iter_lines():
                if not line or not line.startswith("data: "):
                    continue
                data = line[6:]
                if data.strip() == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    dropped_chunks += 1
                    if not dropped_sample:
                        dropped_sample = data[:200]
                    continue

                # In-band error: the gateway can return HTTP 200 and then stream
                # an {"error": …} chunk (validation / context / rate-limit). It
                # has no `choices`/`usage`, so it used to be silently dropped —
                # surfacing it is what turns a silent "hang" into a real message.
                err = obj.get("error")
                if err:
                    msg = err.get("message") if isinstance(err, dict) else str(err)
                    if not is_last_attempt:
                        # Optimized attempt errored in-band (HTTP 200 + {"error"})
                        # — degrade to the raw request, same as an HTTP 4xx would.
                        # The optimize beta must never be the reason a turn fails.
                        plan = {
                            "dial": dial,
                            "levers_applied": [],
                            "degraded": "gateway in-band error on optimized request, sent raw",
                        }
                        retry_raw = True
                    else:
                        last_meta["error"] = msg
                        refusal = reasoning_refusal(err)
                        if refusal is not None:
                            last_meta["reasoning_rejected"] = refusal
                    break

                if obj.get("model"):
                    last_model = obj["model"]

                choices = obj.get("choices") or []
                if choices:
                    delta = choices[0].get("delta", {})

                    content = delta.get("content")
                    if content:
                        yield content

                    tc_deltas = delta.get("tool_calls") or []
                    for tc in tc_deltas:
                        accum.add(tc)
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            progress_tool = fn["name"]
                        progress_chars += len(fn.get("arguments") or "")
                    if tc_deltas:
                        # Spinner feed: an 8KB write_file argument used to
                        # stream in dead silence. render_stream pops these —
                        # they never reach the meta dict.
                        yield {"stream_progress": {
                            "tool": progress_tool or None,
                            "chars": progress_chars,
                        }}

                usage = obj.get("usage")
                cost = obj.get("cost")
                if usage or cost:
                    last_meta.update({"usage": usage, "cost": cost})
        if retry_raw:
            # Discard whatever the aborted optimized attempt streamed so the
            # caller doesn't keep its partial text or half tool call.
            yield {"stream_reset": True}
            continue  # optimized attempt errored in-band — try the raw request
        break  # this attempt streamed successfully

    if last_model:
        last_meta["model"] = last_model
    if request_ids:
        last_meta["request_ids"] = request_ids
    if dropped_chunks:
        last_meta["dropped_chunks"] = dropped_chunks
        last_meta["dropped_sample"] = dropped_sample
    tool_calls = accum.finalize()
    if accum.dropped:
        last_meta["accum_dropped"] = accum.dropped
    if tool_calls:
        last_meta["tool_calls"] = tool_calls
    if plan:
        last_meta["optimize_plan"] = plan
    if last_meta:
        yield last_meta


def complete_chat(messages: list, cfg: dict, tools: "Optional[list]" = None,
                  max_tokens: "int | None" = None) -> tuple:
    """Non-streaming fallback for one hop. Returns (reply_text, meta).

    Why this exists: Mesh's own retry + provider fallback is documented to
    apply to NON-streaming chat completions only. This CLI streams
    everything, so a run of streaming failures gets zero server-side
    resilience — falling back to a single blocking request is what buys it
    (and it is also how Claude Code recovers: `didFallBackToNonStreaming`).

    Same meta shape as stream_chat's final yield, so callers need no special
    casing: {usage, cost, model, tool_calls?, error?, elapsed}.
    """
    url = f"{cfg['base_url'].rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
    }
    # Sent without cache marks on purpose: this is the last resort after
    # streaming kept failing, and it has no second attempt to fall back to.
    payload = build_payload(_drop_empty_assistant(messages), cfg, tools)
    payload["stream"] = False
    if max_tokens:
        payload["max_tokens"] = int(max_tokens)

    started = time.time()
    r = httpx.post(url, json=payload, headers=headers, timeout=300)
    r.raise_for_status()
    data = r.json()
    meta: dict = {"elapsed": time.time() - started, "non_streaming": True}
    if isinstance(data, dict) and data.get("error"):
        err = data["error"]
        meta["error"] = err.get("message") if isinstance(err, dict) else str(err)
        refusal = reasoning_refusal(err)
        if refusal is not None:
            meta["reasoning_rejected"] = refusal
        return "", meta

    choices = (data or {}).get("choices") or []
    msg = (choices[0] or {}).get("message", {}) if choices else {}
    reply = msg.get("content") or ""
    raw_calls = msg.get("tool_calls") or []
    calls = []
    for i, tc in enumerate(raw_calls):
        fn = tc.get("function") or {}
        calls.append({
            "id": tc.get("id") or f"call_{i}",
            "name": fn.get("name") or "",
            "arguments": fn.get("arguments") or "",
        })
    if calls:
        meta["tool_calls"] = calls
    if data.get("usage"):
        meta["usage"] = data["usage"]
    if data.get("cost") is not None:
        meta["cost"] = data["cost"]
    meta["model"] = (
        r.headers.get("x-resolved-model-id") or data.get("model") or ""
    )
    rid = r.headers.get("x-request-id")
    if rid:
        meta["request_id"] = rid
        meta["request_ids"] = [rid]
    return reply, meta
