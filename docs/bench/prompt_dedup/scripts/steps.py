"""Per-step view of one run: what was called in each step and the tokens the
step used (from the running total the engine reports after every step).

    python3 steps.py RUN_DIR"""
import json, sys, pathlib
run = pathlib.Path(sys.argv[1])
ev = [json.loads(l) for l in (run / "events.jsonl").read_text().splitlines() if l.strip()]
start = next(i for i, e in enumerate(ev) if e["type"] == "turn.started")
sid = ev[start]["session"]
ev = [e for e in ev[start:] if e.get("session") == sid]
hop, last, rows = 0, 0, {}
for e in ev:
    t, d = e["type"], e.get("data") or {}
    if t == "hop.started":
        hop = d["hop"]; rows[hop] = {"calls": [], "tokens": 0, "cost": 0}
    elif t == "tool.started":
        a = d.get("args") or {}
        s = d["name"]
        if d["name"] == "run_bash": s += f"({(a.get('command') or '')[:70]!r}" + (f", timeout={a['timeout']}" if a.get("timeout") else "") + ")"
        elif d["name"] in ("read_file", "write_file", "edit_file"): s += f"({a.get('path')}" + (f" {a.get('offset')}+{a.get('limit')}" if a.get("offset") else "") + ")"
        elif d["name"] == "start_server": s += f"({a.get('command')!r})"
        elif d["name"] == "remember": s += f"({a.get('note')!r})"
        rows[hop]["calls"].append(s)
    elif t == "plan.updated":
        rows[hop]["calls"].append("plan " + (d.get("summary") or ""))
    elif t == "status" and hop and d.get("turn_tokens"):
        if d["turn_tokens"] > last:
            rows[hop]["tokens"] += d["turn_tokens"] - last; last = d["turn_tokens"]
            rows[hop]["cost"] = d.get("turn_cost")
    elif t == "approval.requested" or t == "approval":
        rows[hop]["calls"].append("APPROVAL ASKED")
for h, r in rows.items():
    print(f"step {h:2d}  {r['tokens']:6d} tok  running ${r['cost'] or 0:.6f}  " + "; ".join(r["calls"]))
end = next((e["data"] for e in ev if e["type"] in ("turn.finished", "turn.aborted")), {})
print("total", end.get("hops"), "steps", end.get("tokens"), "tokens", end.get("cost"), "dollars", end.get("elapsed"), "s")
final = [e["data"]["text"] for e in ev if e["type"] == "assistant.message" and (e["data"].get("text") or "").strip()]
print("final answer:", (final[-1] if final else "")[:700])
