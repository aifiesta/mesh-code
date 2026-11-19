"""Live test of removing the every-step cut: what each run was billed, how
many steps it took, how often the same file was read again, and whether the
result is right.

    python3 analyse.py

Reads each run's events.jsonl (everything the engine told the app window,
including the gateway's bill for every step). Sends nothing to any model."""
import collections, json, pathlib, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
HOME = pathlib.Path.home()


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
    if not hops:
        return None
    project, before = ROOT / side / "projects" / name, ROOT / "0-projects-before" / task
    lines = [f"{'step':>4} {'billed':>10} | {'full price':>10} {'from cache':>10} {'to cache':>9} {'output':>6}"]
    for h in hops:
        b = h["bill"]
        lines.append(f"{h['hop']:>4} {h['cost']:>10.6f} | {b['fresh_tokens']:>10} {b['cached_tokens']:>10} {b['cache_write_tokens']:>9} {b['output_tokens']:>6}")
    (run / "bill-step-by-step.txt").write_text("\n".join(lines) + "\n")
    reads = collections.Counter(e["data"]["args"].get("path") for e in ev
                                if e["type"] == "tool.started" and e["data"].get("name") == "read_file")
    (run / "files-read.txt").write_text("".join(f"{n} time(s)  {p}\n" for p, n in sorted(reads.items())) or "no file was read with read_file\n")
    final = [e["data"]["text"] for e in ev if e["type"] == "assistant.message" and (e["data"].get("text") or "").strip()]
    (run / "final-answer.md").write_text(final[-1] if final else "(no final text)")
    diff = subprocess.run(["diff", "-ruN", "-x", "__pycache__", "-x", ".pytest_cache", str(before), str(project)],
                          capture_output=True, text=True).stdout
    (run / "what-the-model-changed.diff").write_text(diff.replace(str(ROOT) + "/", "") or "No file was changed.\n")
    if task == "standard":
        chk = subprocess.run([sys.executable, "-B", str(ROOT / "checks/level_comparison_check.py"), "3", str(project), str(before)],
                             capture_output=True, text=True)
        (run / "correctness-check.txt").write_text((chk.stdout + chk.stderr).replace(str(ROOT) + "/", ""))
        verdict = chk.stdout.strip().splitlines()[-1] if chk.stdout.strip() else "no output"
        passed, total = (int(x) for x in verdict.split(": ")[1].split(" checks")[0].split(" of ")) if " of " in verdict else (0, 0)
        result = f"{passed}/{total} hidden checks"
    else:                                   # a read-only task: the answer must cover all six modules and change nothing
        text = final[-1] if final else ""
        covered = sum(1 for m in ("attachments", "journal", "permissions", "plan", "scores", "styles") if m in text)
        result = f"{covered}/6 modules in the answer, " + ("no file changed" if not diff else "FILES CHANGED")
    tot = lambda k: sum(h["bill"][k] for h in hops)
    r = dict(side=side, task=task, run=name, started=started, model=str(hops[-1].get("model") or (end or {}).get("model")).split("/")[-1],
             finished=bool(end), steps=len(hops), cost=round(sum(h["cost"] for h in hops), 6),
             input_tokens=tot("fresh_tokens") + tot("cached_tokens") + tot("cache_write_tokens"),
             full_price_tokens=tot("fresh_tokens"), from_cache_tokens=tot("cached_tokens"), to_cache_tokens=tot("cache_write_tokens"),
             output_tokens=tot("output_tokens"), file_reads=sum(reads.values()), files_read_more_than_once=sum(1 for n in reads.values() if n > 1),
             result=result)
    if not end:      # the test's time limit passed while the provider was slow; the chat finished in the app afterwards
        s = json.loads((run / "summary.json").read_text())
        saved = HOME / ".mesh-code" / "sessions" / f"{s['session']}.json"
        if saved.exists():
            d = json.loads(saved.read_text())
            r["note"] = (f"time limit of the test reached after {len(hops)} steps (one step took over 4 minutes at the provider); "
                         f"the chat finished in the app afterwards, whole chat ${d.get('session_cost')}")
    return r


rows = []
for side in ("cut", "nocut"):
    d = ROOT / side / "runs"
    for run in sorted(d.iterdir()) if d.exists() else []:
        if (run / "events.jsonl").exists():
            r = one(side, run)
            if r:
                rows.append(r)
rows.sort(key=lambda r: r["started"])
(ROOT / "numbers.json").write_text(json.dumps(rows, indent=1))
for r in rows:
    print(f"{r['run']:30s} {r['steps']:2d} steps ${r['cost']:.6f} | input {r['input_tokens']:6d}: full {r['full_price_tokens']:6d} cache {r['from_cache_tokens']:6d} "
          f"written {r['to_cache_tokens']:6d} | out {r['output_tokens']:5d} | reads {r['file_reads']:2d}, again {r['files_read_more_than_once']} | {r['result']}"
          + (" | " + r["note"] if r.get("note") else ""))
print("billed in total (steps the test saw):", round(sum(r["cost"] for r in rows), 6))
