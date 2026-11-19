"""Live test of caching on Claude models: what each run was billed, step by
step, and whether the result passes the hidden checks.

    python3 analyse.py

Reads each run's events.jsonl (everything the engine told the app window,
including the gateway's bill for every step). Sends nothing to any model."""
import json, pathlib, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
CHECK_LEVEL = {"small": "2", "standard": "3"}


def one(side, run):
    name = run.name
    task = name.split("-")[1]
    ev = [json.loads(l) for l in (run / "events.jsonl").read_text().splitlines() if l.strip()]
    i = next((k for k, e in enumerate(ev) if e["type"] == "turn.started"), None)
    if i is None:
        return None
    started, sid = ev[i]["ts"], ev[i]["session"]
    ev = [e for e in ev[i:] if e.get("session") == sid]
    end = next((e["data"] for e in ev if e["type"] in ("turn.finished", "turn.aborted")), None)
    hops = [e["data"] for e in ev if e["type"] == "hop.usage"]
    if end is None or not hops:
        return None
    project = ROOT / side / "projects" / name
    before = ROOT / "0-projects-before" / task
    chk = subprocess.run([sys.executable, "-B", str(ROOT / "checks/level_comparison_check.py"), CHECK_LEVEL[task],
                          str(project), str(before)], capture_output=True, text=True)
    (run / "correctness-check.txt").write_text((chk.stdout + chk.stderr).replace(str(ROOT) + "/", ""))
    verdict = chk.stdout.strip().splitlines()[-1] if chk.stdout.strip() else "no output"
    passed, total = (int(x) for x in verdict.split(": ")[1].split(" checks")[0].split(" of ")) if " of " in verdict else (0, 0)
    lines = [f"{'step':>4} {'billed':>10} | {'full price':>10} {'from cache':>10} {'to cache':>9} {'output':>6} | "
             f"{'full $':>9} {'cache $':>9} {'write $':>9} {'output $':>9}"]
    for h in hops:
        b = h["bill"]
        lines.append(f"{h['hop']:>4} {h['cost']:>10.6f} | {b['fresh_tokens']:>10} {b['cached_tokens']:>10} {b['cache_write_tokens']:>9} "
                     f"{b['output_tokens']:>6} | {b['fresh_usd']:>9.6f} {b['cached_usd']:>9.6f} {b['cache_write_usd']:>9.6f} {b['output_usd']:>9.6f}")
    (run / "bill-step-by-step.txt").write_text("\n".join(lines) + "\n")
    final = [e["data"]["text"] for e in ev if e["type"] == "assistant.message" and (e["data"].get("text") or "").strip()]
    (run / "final-answer.md").write_text(final[-1] if final else "(no final text)")
    diff = subprocess.run(["diff", "-ruN", "-x", "__pycache__", "-x", ".pytest_cache", str(before), str(project)],
                          capture_output=True, text=True).stdout
    (run / "what-the-model-changed.diff").write_text(diff.replace(str(ROOT) + "/", "") or "No file was changed.\n")
    b, b1 = end["bill"], hops[0]["bill"]
    inp = lambda x: x["fresh_tokens"] + x["cached_tokens"] + x["cache_write_tokens"]
    return dict(side=side, task=task, run=name, started=started, model=str(end.get("model")).split("/")[-1],
                steps=end["hops"], tokens=end["tokens"], cost=end["cost"],
                input_tokens=inp(b), full_price_tokens=b["fresh_tokens"], from_cache_tokens=b["cached_tokens"],
                to_cache_tokens=b["cache_write_tokens"], output_tokens=b["output_tokens"],
                input_usd=round(b["fresh_usd"] + b["cached_usd"] + b["cache_write_usd"], 6), output_usd=b["output_usd"],
                step1_from_cache=b1["cached_tokens"], step1_input=inp(b1), step1_cost=hops[0]["cost"],
                estimated=bool(b.get("estimated")), passed=passed, checks=total)


rows = []
for side in ("off", "on"):
    d = ROOT / side / "runs"
    for run in sorted(d.iterdir()) if d.exists() else []:
        if (run / "events.jsonl").exists():
            r = one(side, run)
            if r:
                rows.append(r)
rows.sort(key=lambda r: r["started"])
(ROOT / "numbers.json").write_text(json.dumps(rows, indent=1))
t0 = rows[0]["started"]
for r in rows:
    print(f"+{(r['started'] - t0) / 60:5.1f} min {r['run']:28s} {r['steps']} steps | input {r['input_tokens']:6d}: full {r['full_price_tokens']:6d}, "
          f"from cache {r['from_cache_tokens']:6d}, to cache {r['to_cache_tokens']:6d} | input ${r['input_usd']:.6f} output ${r['output_usd']:.6f} "
          f"total ${r['cost']:.6f} | step 1 from cache {r['step1_from_cache']} of {r['step1_input']} | checks {r['passed']}/{r['checks']}"
          + (" | ESTIMATED" if r["estimated"] else ""))
print("billed in total:", round(sum(r["cost"] for r in rows), 6))
