"""Piece 4: the same tasks at different thinking levels. Per run and per
level: bill (split by part), steps, time, hidden checks, step-ups to a
dearer model. Writes the per-run files and prints the tables.

    python3 analyse.py

Reads each run's events.jsonl (everything the engine told the app window,
including the per-step bill). Sends nothing to any model."""
import json, pathlib, statistics as st, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TOP = ROOT.parent
LEVELS = ("none", "low", "high", "auto")          # auto = the router's own choice (max today)
SHOWN = {"none": "off", "low": "low", "high": "high", "auto": "max (today)"}
CHECK_LEVEL = {"small": "2", "standard": "3"}
PARTS = ("fresh", "cached", "cache_write", "output")


def one(level, run):
    name = run.name
    task = name.split("-")[1]
    ev = [json.loads(l) for l in (run / "events.jsonl").read_text().splitlines() if l.strip()]
    i = next((k for k, e in enumerate(ev) if e["type"] == "turn.started"), None)
    if i is None:
        return None
    sid = ev[i]["session"]
    ev = [e for e in ev[i:] if e.get("session") == sid]
    end = next((e["data"] for e in ev if e["type"] in ("turn.finished", "turn.aborted")), None)
    if end is None or not end.get("bill"):
        return None
    hops = [e["data"] for e in ev if e["type"] == "hop.usage"]
    routed = [e["data"] for e in ev if e["type"] == "routed"]
    b = end["bill"]
    project = ROOT / level / "projects" / name
    chk = subprocess.run([sys.executable, "-B", str(TOP / "checks/level_comparison_check.py"), CHECK_LEVEL[task], str(project),
                          str(ROOT / "0-projects-before" / task)], capture_output=True, text=True)
    (run / "correctness-check.txt").write_text(chk.stdout + chk.stderr)
    verdict = chk.stdout.strip().splitlines()[-1] if chk.stdout.strip() else "no output"
    passed, total = (int(x) for x in verdict.split(": ")[1].split(" checks")[0].split(" of ")) if " of " in verdict else (0, 0)
    lines = [f"{'step':>4} {'model':24s} {'tokens':>7} {'billed':>10} | {'fresh':>6} {'cached':>7} {'output':>6} {'thinking':>8} | {'output $':>9}"]
    for h in hops:
        hb = h["bill"]
        lines.append(f"{h['hop']:>4} {str(h.get('model'))[-24:]:24s} {h['tokens']:>7} {h['cost']:>10.6f} | {hb['fresh_tokens']:>6} {hb['cached_tokens']:>7} "
                     f"{hb['output_tokens']:>6} {hb['thinking_tokens']:>8} | {hb['output_usd']:>9.6f}")
    (run / "bill-step-by-step.txt").write_text("\n".join(lines) + "\n")
    final = [e["data"]["text"] for e in ev if e["type"] == "assistant.message" and (e["data"].get("text") or "").strip()]
    (run / "final-answer.md").write_text(final[-1] if final else "(no final text)")
    diff = subprocess.run(["diff", "-ruN", "-x", "__pycache__", "-x", ".pytest_cache", str(ROOT / "0-projects-before" / task), str(project)],
                          capture_output=True, text=True).stdout
    (run / "what-the-model-changed.diff").write_text(diff.replace(str(ROOT) + "/", "") or "No file was changed.\n")
    return dict(level=level, task=task, run=name, thinking_sent=[r.get("thinking") for r in routed], models=sorted({str(h.get("model")) for h in hops}),
                stepped_up=any(r.get("escalated") for r in routed), steps=end["hops"], tokens=end["tokens"], cost=end["cost"], seconds=end.get("elapsed"),
                fresh_usd=b["fresh_usd"], cached_usd=b["cached_usd"], output_usd=b["output_usd"], thinking_usd=b["thinking_usd"],
                output_tokens=b["output_tokens"], thinking_tokens=b["thinking_tokens"], fresh_tokens=b["fresh_tokens"], cached_tokens=b["cached_tokens"],
                planned=any(e["type"] == "plan.updated" for e in ev), passed=passed, checks=total, aborted=end.get("stubs") is None and "sealed" in end,
                notices=[e["data"].get("message") for e in ev if e["type"] in ("notice", "error")])


rows = []
for level in LEVELS:
    d = ROOT / level / "runs"
    for run in sorted(d.iterdir()) if d.exists() else []:
        if (run / "events.jsonl").exists():
            r = one(level, run)
            if r:
                rows.append(r)
(ROOT / "numbers.json").write_text(json.dumps(rows, indent=1))
for r in sorted(rows, key=lambda r: (r["task"], LEVELS.index(r["level"]), r["run"])):
    print(f"{r['run']:20s} {r['steps']:2d} steps {r['tokens']:7,} tok ${r['cost']:.6f} {r['seconds']:6.1f}s | out {r['output_tokens']:5,} (think {r['thinking_tokens']:5,}) ${r['output_usd']:.6f} "
          f"| fresh ${r['fresh_usd']:.6f} cached ${r['cached_usd']:.6f} | checks {r['passed']}/{r['checks']} | plan {'y' if r['planned'] else 'n'}"
          + (" | STEPPED UP: " + ", ".join(m.split('/')[-1] for m in r['models']) if r["stepped_up"] else "") + (" | ABORTED" if r["aborted"] else ""))
print()
m = lambda rs, k: st.mean(r[k] for r in rs)
for task in ("small", "standard"):
    print(f"{task}: averages per level")
    print(f"  {'level':12s} {'runs':>4} {'billed':>10} {'output $':>10} {'thinking $':>10} {'fresh $':>10} {'cached $':>10} {'steps':>6} {'tokens':>8} {'out tok':>8} {'think tok':>9} {'secs':>6} {'all checks':>10} {'stepped up':>10}")
    for level in LEVELS:
        rs = [r for r in rows if r["task"] == task and r["level"] == level]
        if not rs:
            continue
        print(f"  {SHOWN[level]:12s} {len(rs):>4} {m(rs,'cost'):>10.6f} {m(rs,'output_usd'):>10.6f} {m(rs,'thinking_usd'):>10.6f} {m(rs,'fresh_usd'):>10.6f} {m(rs,'cached_usd'):>10.6f} "
              f"{m(rs,'steps'):>6.1f} {m(rs,'tokens'):>8.0f} {m(rs,'output_tokens'):>8.0f} {m(rs,'thinking_tokens'):>9.0f} {m(rs,'seconds'):>6.1f} "
              f"{sum(1 for r in rs if r['passed'] == r['checks'] and r['checks']):>6}/{len(rs):<3} {sum(1 for r in rs if r['stepped_up']):>10}")
        costs = sorted(r["cost"] for r in rs)
        print(f"  {'':12s}      bills: " + ", ".join(f"${c:.4f}" for c in costs))
    print()
print("billed in total:", round(sum(r["cost"] or 0 for r in rows), 6))
