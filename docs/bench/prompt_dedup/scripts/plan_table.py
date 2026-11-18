"""Every run of the server case, in every window: which prompt it got, whether
the model made a plan, and what it cost.   python3 plan_table.py"""
import json, pathlib, statistics as st
ROOT = pathlib.Path(__file__).resolve().parent.parent
rows = []
for where in ("before", "after", "diagnosis"):
    for run in sorted((ROOT / where / "runs").glob("*-2-server*")):
        ev = [json.loads(l) for l in (run / "events.jsonl").read_text().splitlines() if l.strip()]
        i = next(k for k, e in enumerate(ev) if e["type"] == "turn.started"); sid = ev[i]["session"]
        ev = [e for e in ev[i:] if e.get("session") == sid]
        end = next((e["data"] for e in ev if e["type"] in ("turn.finished", "turn.aborted")), None)
        calls = [e["data"]["name"] for e in ev if e["type"] == "tool.started"]
        plan = any(e["type"] == "plan.updated" for e in ev)
        manual = run / "end-read-from-the-app.json"                 # a run whose log stopped early
        note = ""
        if manual.exists():
            m = json.loads(manual.read_text()); end, plan, note = m, m["plan"], " (log incomplete, figures from the app)"
            calls = [c for c in m["calls"] if c not in ("create_plan", "update_step", "remember")]
        if not end: continue
        hop, first = 0, None
        for e in ev:
            if e["type"] == "hop.started": hop = e["data"]["hop"]
            if e["type"] == "status" and hop == 1 and e["data"].get("turn_tokens"): first = e["data"]["turn_tokens"]; break
        variant = run.name.split("-2-server")[0]
        text = {"before": "old", "old": "old", "after": "new", "new": "new"}.get(variant, variant)
        rows.append(dict(window=where, run=run.name, prompt=text, plan=plan, steps=end["hops"], tokens=end["tokens"], cost=end["cost"], step1=first,
                         server_tool="start_server" in calls, nothing_after_start=("start_server" in calls and calls[-1] == "start_server"), note=note))
for r in rows:
    print(f"{r['window']:9s} {r['run']:30s} prompt={r['prompt']:12s} step1={r['step1']}  {'PLAN   ' if r['plan'] else 'no plan'} {r['steps']:2d} steps {r['tokens']:6d} tok ${r['cost']:.6f}"
          f"  server tool: {'yes' if r['server_tool'] else 'NO'}, nothing after start: {'yes' if r['nothing_after_start'] else 'NO'}{r['note']}")
def line(label, rs):
    if rs: print(f"{label:38s} n={len(rs):2d}  planned {sum(r['plan'] for r in rs)}/{len(rs)}  avg {st.mean(r['steps'] for r in rs):4.1f} steps  {st.mean(r['tokens'] for r in rs):7.0f} tok  ${st.mean(r['cost'] for r in rs):.6f}")
print()
for p in ("old", "new"):
    line(f"{p} prompt, all windows", [r for r in rows if r["prompt"] == p])
    for w in ("before", "after", "diagnosis"):
        line(f"   {p} prompt, {w} window", [r for r in rows if r["prompt"] == p and r["window"] == w])
for p in ("only-memory", "only-shell", "only-servers"):
    line(f"{p} (old prompt, one removal)", [r for r in rows if r["prompt"] == p])
print()
line("runs that made a plan", [r for r in rows if r["plan"]]); line("runs that did not", [r for r in rows if not r["plan"]])
for p in ("old", "new"):
    line(f"   {p} prompt, planned", [r for r in rows if r["prompt"] == p and r["plan"]]); line(f"   {p} prompt, did not", [r for r in rows if r["prompt"] == p and not r["plan"]])
print("total billed, server case:", round(sum(r["cost"] for r in rows), 6))
(ROOT / "diagnosis" / "server-case-all-runs.json").write_text(json.dumps(rows, indent=1))
