"""Piece 5: is the first step of a chat cached now? Per run: what step 1 was
billed (fresh / cached input), the whole bill, and the hidden checks.

    python3 analyse.py

Reads each run's events.jsonl (everything the engine told the app window,
including the per-step bill). Sends nothing to any model."""
import json, pathlib, statistics as st, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TOP = ROOT.parent
CHECK_LEVEL = {"small": "2", "standard": "3"}
P4 = ("fresh", "cached", "cache_write", "output")


def one(side, run):
    name = run.name
    task = name.split("-")[1]
    ev = [json.loads(l) for l in (run / "events.jsonl").read_text().splitlines() if l.strip()]
    i = next((k for k, e in enumerate(ev) if e["type"] == "turn.started"), None)
    if i is None:
        return None
    started = ev[i]["ts"]
    sid = ev[i]["session"]
    ev = [e for e in ev[i:] if e.get("session") == sid]
    end = next((e["data"] for e in ev if e["type"] in ("turn.finished", "turn.aborted")), None)
    hops = [e["data"] for e in ev if e["type"] == "hop.usage"]
    if end is None or not hops:
        return None
    project = ROOT / side / "projects" / name
    chk = subprocess.run([sys.executable, "-B", str(TOP / "checks/level_comparison_check.py"), CHECK_LEVEL[task], str(project),
                          str(ROOT / "0-projects-before" / task)], capture_output=True, text=True)
    (run / "correctness-check.txt").write_text(chk.stdout + chk.stderr)
    verdict = chk.stdout.strip().splitlines()[-1] if chk.stdout.strip() else "no output"
    passed, total = (int(x) for x in verdict.split(": ")[1].split(" checks")[0].split(" of ")) if " of " in verdict else (0, 0)
    lines = [f"{'step':>4} {'tokens':>7} {'billed':>10} | {'fresh':>6} {'cached':>7} {'output':>6} | {'fresh $':>9} {'cached $':>9} {'output $':>9}"]
    for h in hops:
        hb = h["bill"]
        lines.append(f"{h['hop']:>4} {h['tokens']:>7} {h['cost']:>10.6f} | {hb['fresh_tokens']:>6} {hb['cached_tokens']:>7} {hb['output_tokens']:>6} | "
                     f"{hb['fresh_usd']:>9.6f} {hb['cached_usd']:>9.6f} {hb['output_usd']:>9.6f}")
    (run / "bill-step-by-step.txt").write_text("\n".join(lines) + "\n")
    final = [e["data"]["text"] for e in ev if e["type"] == "assistant.message" and (e["data"].get("text") or "").strip()]
    (run / "final-answer.md").write_text(final[-1] if final else "(no final text)")
    diff = subprocess.run(["diff", "-ruN", "-x", "__pycache__", "-x", ".pytest_cache", str(ROOT / "0-projects-before" / task), str(project)],
                          capture_output=True, text=True).stdout
    (run / "what-the-model-changed.diff").write_text(diff.replace(str(ROOT) + "/", "") or "No file was changed.\n")
    b1, b = hops[0]["bill"], end["bill"]
    return dict(side=side, task=task, run=name, started=started, model=str(end.get("model")).split("/")[-1], steps=end["hops"], tokens=end["tokens"], cost=end["cost"],
                step1_cost=hops[0]["cost"], step1_fresh=b1["fresh_tokens"], step1_cached=b1["cached_tokens"], step1_input=b1["fresh_tokens"] + b1["cached_tokens"] + b1["cache_write_tokens"],
                fresh_usd=b["fresh_usd"], cached_usd=b["cached_usd"], output_usd=b["output_usd"], passed=passed, checks=total,
                stepped_up=any(e["type"] == "routed" and e["data"].get("escalated") for e in ev))


rows = []
for side in ("old", "new"):
    d = ROOT / side / "runs"
    for run in sorted(d.iterdir()) if d.exists() else []:
        if (run / "events.jsonl").exists():
            r = one(side, run)
            if r:
                rows.append(r)
rows.sort(key=lambda r: r["started"])
t0 = rows[0]["started"] if rows else 0
(ROOT / "numbers.json").write_text(json.dumps(rows, indent=1))
print("in the order they ran:")
for r in rows:
    print(f"  +{(r['started'] - t0) / 60:5.1f} min  {r['run']:20s} step 1: {r['step1_cached']:5d} of {r['step1_input']:5d} input tokens cached, ${r['step1_cost']:.6f} | "
          f"whole run: {r['steps']:2d} steps ${r['cost']:.6f} | checks {r['passed']}/{r['checks']}" + (" | STEPPED UP" if r["stepped_up"] else ""))
print()
m = lambda rs, k: st.mean(r[k] for r in rs)
for task in ("small", "standard"):
    for side in ("old", "new"):
        rs = [r for r in rows if r["task"] == task and r["side"] == side]
        if not rs:
            continue
        print(f"{task:9s} {side}: {len(rs)} runs | step 1 avg ${m(rs,'step1_cost'):.6f}, cached {m(rs,'step1_cached'):.0f} of {m(rs,'step1_input'):.0f} | bill avg ${m(rs,'cost'):.6f} "
              f"(fresh ${m(rs,'fresh_usd'):.6f}, cached ${m(rs,'cached_usd'):.6f}, output ${m(rs,'output_usd'):.6f}) | steps {m(rs,'steps'):.1f} | all checks {sum(1 for r in rs if r['passed'] == r['checks'] and r['checks'])}/{len(rs)}")
        if side == "new":
            warm = [r for r in rs if r["step1_cached"] > 0]
            if warm and len(warm) < len(rs):
                print(f"{'':9s}      of those, {len(warm)} with step 1 cached: step 1 avg ${m(warm,'step1_cost'):.6f}, bill avg ${m(warm,'cost'):.6f}")
print("billed in total:", round(sum(r["cost"] or 0 for r in rows), 6))
