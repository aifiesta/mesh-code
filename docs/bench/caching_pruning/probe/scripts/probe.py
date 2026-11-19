"""Caching and pruning probe: replay ONE fixed conversation step by step
through the engine's real request path and record what the gateway bills.

Nothing here changes the product. The history is made up (fixed assistant
tool calls and fixed tool results cut from the engine's own source files), so
the only thing that differs between variants is where cache marks go and
whether old tool results are cut. Replies are capped at a few tokens.

  python probe.py run MODEL VARIANT STEPS [LABEL]
  python probe.py ttl MODEL VARIANT DELAYS_SECONDS(comma)    # one entry per delay
  python probe.py refresh MODEL VARIANT FIRST_GAP SECOND_GAP

Variants:
  none      no marks, nothing cut (what the app sends today, dial 0)
  dial0.1   the app's Token savings dial at 0.1 (marks: system, second-to-last)
  dial0.3   the dial at 0.3 (same marks, old tool results cut every step)
  last      mark on the last message only
  sys+last  marks on the last system message and the last message
  parts     like `last`, with the mark inside a content part instead of on the message
  last1h    like `last`, asking for the one-hour lifetime
  stable+last  marks on the FIRST system message and the last message
Env: MESH_ENGINE = path to the engine folder; PROBE_OUT = folder for results;
     PROBE_CEILING = stop before a run when the folder's total passes this (USD).
"""
import json
import os
import sys
import time
import uuid
from pathlib import Path

ENGINE = Path(os.environ["MESH_ENGINE"]).resolve()
sys.path.insert(0, str(ENGINE))
from meshharness.core import pricing, tools  # noqa: E402
from meshharness.core.client import stream_chat  # noqa: E402
from meshharness.core.config import DEFAULT_CONFIG  # noqa: E402

OUT = Path(os.environ.get("PROBE_OUT", ".")).resolve()
CEILING = float(os.environ.get("PROBE_CEILING", "0.85"))

# (file under engine/meshharness/core, characters taken) per step. Big and
# small on purpose: the dial only cuts results over 800 characters.
STEPS = [("client.py", 6000), ("config.py", 300), ("router.py", 5000),
         ("compact.py", 4000), ("plan.py", 200), ("tools.py", 7000),
         ("pricing.py", 3000), ("memory.py", 6000), ("optimize.py", 5000),
         ("journal.py", 4000)]


def cfg_for(model: str, dial: float) -> dict:
    cfg = dict(DEFAULT_CONFIG)
    cfg["api_key"] = (Path.home() / ".mesh-code" / "credentials").read_text().strip()
    cfg["model"] = model
    cfg["route_mode"] = "off"
    cfg["optimize"] = dial
    cfg["max_tokens"] = int(os.environ.get("PROBE_MAX_TOKENS", "16"))  # replies are not what is measured
    return cfg


def conversation(nonce: str, steps: int) -> list:
    """System (two messages), the user's task, then `steps - 1` finished
    tool rounds. Step N of a run sends the first N - 1 rounds."""
    msgs = tools.build_system_messages(dict(DEFAULT_CONFIG), "/tmp/caching-probe")
    msgs[0] = {**msgs[0], "content": f"[probe {nonce}]\n" + msgs[0]["content"]}
    msgs.append({"role": "user", "content": "Read the engine's core files one at a "
                 "time and tell me how a request is built and billed."})
    core = ENGINE / "meshharness" / "core"
    for i, (name, chars) in enumerate(STEPS[:max(steps - 1, 0)], start=1):
        body = (core / name).read_text()[:chars]
        msgs.append({"role": "assistant", "content": f"Reading {name}.",
                     "tool_calls": [{"id": f"call_{i}", "type": "function",
                                     "function": {"name": "read_file",
                                                  "arguments": json.dumps({"path": f"core/{name}"})}}]})
        msgs.append({"role": "tool", "tool_call_id": f"call_{i}", "content": body})
    return msgs


def mark(msgs: list, variant: str) -> list:
    out = [dict(m) for m in msgs]
    if variant in ("last", "sys+last"):
        out[-1]["cache_control"] = {"type": "ephemeral"}
    if variant == "sys+last":
        out[1]["cache_control"] = {"type": "ephemeral"}
    if variant == "last1h":
        out[-1]["cache_control"] = {"type": "ephemeral", "ttl": "1h"}
    if variant == "stable+last":
        out[0]["cache_control"] = {"type": "ephemeral"}
        out[-1]["cache_control"] = {"type": "ephemeral"}
    if variant == "parts":
        last = out[-1]
        last["content"] = [{"type": "text", "text": last["content"],
                            "cache_control": {"type": "ephemeral"}}]
    return out


def spent() -> float:
    """Billed so far, summed from every record in raw/ (safe with parallel runs)."""
    total = 0.0
    for f in (OUT / "raw").glob("*.jsonl"):
        for row in f.read_text().splitlines():
            try:
                total += float(json.loads(row).get("billed_usd") or 0)
            except Exception:
                pass
    return total


def send(model: str, variant: str, msgs: list) -> dict:
    dial = {"dial0.1": 0.1, "dial0.3": 0.3}.get(variant, 0.0)
    cfg = cfg_for(model, dial)
    meta = {}
    for item in stream_chat(mark(msgs, variant), cfg, tools.TOOLS):
        if isinstance(item, dict) and ("usage" in item or "error" in item or "request_ids" in item):
            meta = item
    usage = meta.get("usage") or {}
    time.sleep(1.5)                                  # let the gateway settle the bill
    billed, bill = pricing.fetch_request_bill(cfg, meta.get("request_ids") or [], usage, timeout=8.0)
    cost = billed if billed is not None else float(meta.get("cost") or 0)
    plan = meta.get("optimize_plan") or {}
    return {"model": meta.get("model") or model, "usage": usage, "tokens": pricing.usage_tokens(usage),
            "billed_usd": cost, "bill": pricing.round_bill(bill) if bill else None,
            "error": meta.get("error"), "levers": plan.get("levers_applied"),
            "degraded": plan.get("degraded"), "request_ids": meta.get("request_ids"),
            "meta_keys": sorted(meta.keys()), "finish": meta.get("finish_reason")}


def line(step, r) -> str:
    t = r["tokens"]
    return (f"  step {step:>2}: fresh {t['fresh']:>6}  cached {t['cached']:>6}  written {t['cache_write']:>6}"
            f"  out {t['output']:>3}  ${r['billed_usd']:.6f}" + (f"  ERROR {r['error']}" if r["error"] else "")
            + (f"  DEGRADED {r['degraded']}" if r.get("degraded") else ""))


def save(name: str, record: dict) -> None:
    (OUT / "raw").mkdir(parents=True, exist_ok=True)
    with open(OUT / "raw" / f"{name}.jsonl", "a") as f:
        f.write(json.dumps(record) + "\n")


def guard() -> None:
    if spent() >= CEILING:
        sys.exit(f"ceiling reached: ${spent():.4f} of ${CEILING}")


def run(model: str, variant: str, steps: int, label: str = "") -> None:
    guard()
    nonce = uuid.uuid4().hex[:10]
    name = f"run-{model.split('/')[-1]}-{variant}{'-' + label if label else ''}"
    print(f"{name}  (nonce {nonce})")
    total = 0.0
    for step in range(1, steps + 1):
        r = send(model, variant, conversation(nonce, step))
        total += r["billed_usd"]
        save(name, {"step": step, "nonce": nonce, "variant": variant, **r})
        print(line(step, r), flush=True)
        time.sleep(float(os.environ.get("PROBE_GAP", "2")))
    print(f"  total ${total:.6f}   (all probes so far ${spent():.4f})", flush=True)


def ttl(model: str, variant: str, delays: list) -> None:
    """One cache entry per delay, all written now; each read exactly once."""
    guard()
    name = f"ttl-{model.split('/')[-1]}-{variant}"
    entries = []
    for d in delays:
        nonce = uuid.uuid4().hex[:10]
        r = send(model, variant, conversation(nonce, 4))
        save(name, {"phase": "write", "delay": d, "nonce": nonce, **r})
        print(f"write for +{d}s" + line(0, r), flush=True)
        entries.append((d, nonce, time.time()))
    for d, nonce, t0 in sorted(entries):
        time.sleep(max(0, t0 + d - time.time()))
        r = send(model, variant, conversation(nonce, 4))
        save(name, {"phase": "read", "delay": d, "nonce": nonce, **r})
        print(f"read at +{d}s" + line(0, r), flush=True)


def refresh(model: str, variant: str, first: int, second: int) -> None:
    """Does reading an entry restart its clock? write, read at +first, read
    again `second` later (first + second is past the lifetime)."""
    guard()
    name = f"refresh-{model.split('/')[-1]}-{variant}"
    nonce = uuid.uuid4().hex[:10]
    for phase, wait in (("write", 0), (f"read +{first}s", first), (f"read +{first + second}s", second)):
        time.sleep(wait)
        r = send(model, variant, conversation(nonce, 4))
        save(name, {"phase": phase, "nonce": nonce, **r})
        print(phase + line(0, r), flush=True)


def shared(model: str) -> None:
    """Is the stable start (tool list + first system message) shared between
    two different chats in two different projects? Mark on the first system
    message and on the last message; nothing unique before the project text."""
    guard()
    name = f"shared-{model.split('/')[-1]}"
    stamp = uuid.uuid4().hex[:10]
    for n, project in enumerate(("alpha", "beta", "gamma"), start=1):
        msgs = tools.build_system_messages(dict(DEFAULT_CONFIG), f"/tmp/caching-probe-{project}-{stamp}")
        msgs.append({"role": "user", "content": f"({stamp}) Chat {n}: what is in this project?"})
        msgs[0] = {**msgs[0], "cache_control": {"type": "ephemeral"}}
        msgs[-1] = {**msgs[-1], "cache_control": {"type": "ephemeral"}}
        r = send(model, "none", msgs)
        save(name, {"chat": n, **r})
        print(f"chat {n}" + line(1, r), flush=True)
        time.sleep(4)


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "run":
        run(sys.argv[2], sys.argv[3], int(sys.argv[4]), sys.argv[5] if len(sys.argv) > 5 else "")
    elif cmd == "ttl":
        ttl(sys.argv[2], sys.argv[3], [int(x) for x in sys.argv[4].split(",")])
    elif cmd == "shared":
        shared(sys.argv[2])
    elif cmd == "refresh":
        refresh(sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5]))
