"""Which thinking levels does Mesh accept for each model in the scoring file?

Sends one tiny request per model, level and request form, shaped the way the
engine sends a turn (streaming, with a tool defined). Two forms are tried:

    old   reasoning_effort: "high"          what the engine sends today; the
                                            gateway allows none/low/medium/high
    new   reasoning: {"effort": "xhigh"}    the gateway's current field; allows
                                            none/minimal/low/medium/high/xhigh/max

Makes real, billed API calls and stops starting new ones once the spend
reaches the ceiling.

    cd docs/bench/model_scoring
    PYTHONPATH=../../../engine python level_check.py SCORES_JSON OUTDIR [CEILING_USD]

Set ONLY=id1,id2 to check a few models. Only the API key and base URL are
read from the real config. Nothing is written outside OUTDIR. Output:
calls.jsonl (every request and reply), levels.csv (one row per model, form
and level) and a printed log.

"Accepted" means the gateway returned an answer. It does not prove the level
changed how much the model thought: the gateway's own list of settings it
dropped, and the reasoning-token counts per level, are recorded for that."""
import csv, json, os, sys, threading, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from meshharness.core import config as C
from meshharness.core.client import build_payload
from meshharness.core.pricing import estimate_cost, fetch_request_cost

C.save_config = lambda cfg: None          # never write the user's config
cfg = C.load_config()
BASE, KEY = cfg["base_url"].rstrip("/"), cfg["api_key"]
HEAD = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}

scores = json.loads(Path(sys.argv[1]).read_text())
out = Path(sys.argv[2]); out.mkdir(parents=True, exist_ok=True)
CEILING = float(sys.argv[3]) if len(sys.argv) > 3 else 2.0

PROBES = ([("none", None)]                                   # nothing sent
          + [("old", l) for l in ("none", "low", "medium", "high")]
          + [("new", l) for l in ("none", "minimal", "low", "medium", "high", "xhigh", "max")])
# needs a few steps of working, so the amount of thinking can differ by level; the reply stays short
PROMPT = ("How many whole numbers from 1 to 1000 are divisible by none of 6, 10 and 15? "
          "Reply with the number only.")
ANSWER = "734"
TOOLS = [{"type": "function", "function": {
    "name": "note", "description": "Save a short note. Not needed for this question.",
    "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}}]
PER_CALL_SECONDS = 90

catalog = httpx.get(f"{BASE}/models", headers=HEAD, timeout=60).json()
catalog = catalog.get("data") if isinstance(catalog, dict) else catalog

spent, lock, stop = 0.0, threading.Lock(), threading.Event()
log = (out / "calls.jsonl").open("a")


def call(model, form, level, tools=True):
    """One request. Returns a flat record; never raises."""
    body = build_payload([{"role": "user", "content": PROMPT}],
                         {"model": model, "reasoning_effort": level if form == "old" else None},
                         TOOLS if tools else None)
    if form == "new":
        body["reasoning"] = {"effort": level}
    rec = dict(model=model, form=form, level=level or "(not sent)", tools=tools, status=None,
               error="", answer="", reasoning_chars=0, tool_called=False, usage=None,
               dropped=None, served_by="", request_id="", seconds=None)
    t0 = time.time()
    try:
        with httpx.stream("POST", f"{BASE}/chat/completions", json=body, headers=HEAD,
                          timeout=httpx.Timeout(PER_CALL_SECONDS, connect=20)) as r:
            rec["status"] = r.status_code
            rec["served_by"] = r.headers.get("x-resolved-model-id", "")
            rec["request_id"] = r.headers.get("x-request-id", "")
            if r.status_code >= 400:
                rec["error"] = r.read().decode("utf-8", "replace")[:600]
            else:
                for line in r.iter_lines():
                    if time.time() - t0 > PER_CALL_SECONDS:
                        rec["error"] = f"stopped after {PER_CALL_SECONDS}s"
                        break
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        ch = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if ch.get("error"):
                        rec["error"] = json.dumps(ch["error"])[:600]
                    if ch.get("usage"):
                        rec["usage"] = ch["usage"]
                    if ch.get("dropped_parameters"):
                        rec["dropped"] = ch["dropped_parameters"]
                    if ch.get("model"):
                        rec["served_by"] = rec["served_by"] or ch["model"]
                    for c in ch.get("choices") or []:
                        d = c.get("delta") or {}
                        rec["answer"] += d.get("content") or ""
                        rec["reasoning_chars"] += len(d.get("reasoning_content") or d.get("reasoning") or "")
                        rec["tool_called"] = rec["tool_called"] or bool(d.get("tool_calls"))
    except Exception as e:                                  # network trouble is a result too
        rec["error"] = f"{type(e).__name__}: {e}"[:600]
    rec["seconds"] = round(time.time() - t0, 1)

    # what it cost: the gateway's billed figure, else an estimate from the token counts
    billed = None
    if rec["request_id"] and (rec["status"] or 500) < 400:
        for _ in range(4):
            time.sleep(0.6)
            billed = fetch_request_cost(cfg, [rec["request_id"]])
            if billed is not None:
                break
    if billed is not None:
        rec["cost_usd"], rec["cost_from"] = billed, "billed"
    else:
        est = estimate_cost(model, rec["usage"] or {}, catalog)
        rec["cost_usd"], rec["cost_from"] = float(est or 0), "estimate" if est else "none"
    global spent
    with lock:
        spent += rec["cost_usd"]
        if spent >= CEILING:
            stop.set()
        log.write(json.dumps({**rec, "request": {k: v for k, v in body.items() if k != "messages"}}) + "\n")
        log.flush()
    return rec


def verdict(rec):
    if rec["error"].startswith("stopped after"):
        return "timed out"
    if rec["status"] is None:
        return "no reply"
    if rec["status"] >= 400 or (rec["error"] and not rec["answer"].strip() and not rec["tool_called"]):
        return "rejected"
    return "accepted"


def check_model(model):
    rows = []
    for form, level in PROBES:
        if stop.is_set():
            rows.append(dict(model=model, form=form, level=level or "(not sent)", verdict="not run (ceiling)"))
            continue
        rec = call(model, form, level)
        v = verdict(rec)
        without = ""
        if v == "rejected" and not stop.is_set():           # is the tool list the reason?
            without = verdict(call(model, form, level, tools=False))
        u = rec.get("usage") or {}
        det = u.get("completion_tokens_details") or {}
        dropped = "; ".join(f"{d.get('name')}: {d.get('reason')}" for d in rec["dropped"] or [])
        rows.append(dict(model=model, form=form, level=rec["level"], verdict=v, without_tools=without,
                         dropped_by_gateway=dropped, correct=ANSWER in rec["answer"],
                         output_tokens=u.get("completion_tokens"),
                         reasoning_tokens=det.get("reasoning_tokens", u.get("reasoning_tokens")),
                         reasoning_chars=rec["reasoning_chars"], input_tokens=u.get("prompt_tokens"),
                         cost_usd=rec["cost_usd"], cost_from=rec["cost_from"], seconds=rec["seconds"],
                         served_by=rec["served_by"], http=rec["status"], error=rec["error"][:200]))
        print(f"{model:36s} {form:4s} {rec['level']:11s} {v:9s} out={u.get('completion_tokens')} "
              f"think={rows[-1]['reasoning_tokens']} ${rec['cost_usd']:.5f} {rec['seconds']}s "
              f"{('dropped ' + dropped) if dropped else ''} {rec['error'][:80]}", flush=True)
    return rows


known = {m.get("id") for m in catalog}
models = list(scores["models"])
if os.environ.get("ONLY"):
    models = [m for m in models if m in os.environ["ONLY"].split(",")]
missing = [m for m in models if m not in known]
print(f"{len(models)} models to check; not in Mesh's list today: {missing or 'none'}")
print(f"spending ceiling ${CEILING:.2f}\n")

with ThreadPoolExecutor(max_workers=6) as pool:
    results = list(pool.map(check_model, [m for m in models if m in known]))

rows = [r for rs in results for r in rs]
cols = ["model", "form", "level", "verdict", "without_tools", "dropped_by_gateway", "correct",
        "output_tokens", "reasoning_tokens", "reasoning_chars", "input_tokens", "cost_usd",
        "cost_from", "seconds", "served_by", "http", "error"]
with (out / "levels.csv").open("w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rows)
print(f"\nspent ${spent:.4f} of ${CEILING:.2f}; {len(rows)} rows -> {out / 'levels.csv'}")
