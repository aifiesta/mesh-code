"""After the runs: per run, write the full conversation, the step-by-step
table, what the model changed, the hidden check and the final answer; then
print one line of numbers per run. Sends nothing to any model.

    python3 write_up.py"""
import json, pathlib, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
tasks = {t["name"]: t for t in json.loads((ROOT / "prompts.json").read_text())}


def fence(text, lang=""):
    text = (text or "").rstrip()
    mark = "````" if "```" in text else "```"
    return f"{mark}{lang}\n{text}\n{mark}\n"


def conversation(name, events):
    out = [f"# Full conversation: {name}\n",
           "Taken from everything the engine reported to the app window during the run. "
           "The same chat is in Mesh Code under the project of this name.\n"]
    for e in events:
        t, d = e["type"], e.get("data") or {}
        if t == "turn.started":
            out.append("## You\n\n" + d.get("text", "") + "\n")
        elif t == "routed":
            out.append(f"> **Router picked:** {d.get('model')} at {d.get('thinking')} thinking. Task level: {d.get('task')}\n")
        elif t == "hop.started":
            out.append(f"*— step {d.get('hop')} —*\n")
        elif t == "assistant.message" and (d.get("text") or "").strip():
            out.append("## Model\n\n" + d["text"].strip() + "\n")
        elif t == "tool.started":
            args = d.get("args") or {}
            out.append(f"**Tool call:** `{d.get('name')}`\n\n" + fence(args.get("command") or json.dumps(args, indent=1)))
        elif t == "tool.result":
            out.append(("Result:" if d.get("ok") else "Result (failed):") + "\n\n" + fence(str(d.get("result"))[:6000]))
        elif t == "plan.updated":
            out.append("> **Plan:** " + "; ".join(f"[{'x' if s.get('status') == 'completed' else ' '}] {s.get('title')}" for s in d.get("steps") or []) + "\n")
        elif t == "memory.note":
            out.append(f"> **Note saved for later sessions:** {d.get('note')}\n")
        elif t in ("notice", "error"):
            out.append(f"> **{t.title()}:** {d.get('message')}\n")
        elif t in ("turn.finished", "turn.aborted"):
            out.append(f"---\n\n**{'Finished' if t == 'turn.finished' else 'Stopped'}:** {d.get('hops')} steps, "
                       f"{d.get('tokens'):,} tokens, ${d.get('cost'):.6f} billed, {d.get('elapsed')} s, last model {d.get('model')}\n")
    return "\n".join(out)


rows = []
for engine in ("before", "after"):
    for run in sorted((ROOT / engine / "runs").iterdir()):
        if not (run / "events.jsonl").exists():
            continue
        name = run.name
        case = name.split("-", 1)[1].split("-run")[0]                 # 1-normal, 2-server, 3-shell-memory
        n = int(case[0])
        events = [json.loads(l) for l in (run / "events.jsonl").read_text().splitlines() if l.strip()]
        start = next(i for i, e in enumerate(events) if e["type"] == "turn.started")
        sid = events[start]["session"]
        mine = [e for e in events[start:] if e.get("session") == sid]
        (run / "conversation.md").write_text(conversation(name, mine))
        steps = subprocess.run([sys.executable, str(ROOT / "scripts" / "steps.py"), str(run)], capture_output=True, text=True).stdout
        (run / "step-by-step.txt").write_text(steps)
        before, after = ROOT / "0-projects-before" / case, ROOT / engine / "projects" / name
        diff = subprocess.run(["diff", "-ruN", "-x", "__pycache__", "-x", ".pytest_cache", "-x", "app.db", str(before), str(after)],
                              capture_output=True, text=True).stdout.replace(str(ROOT) + "/", "")
        (run / "what-the-model-changed.diff").write_text(diff or "No file was changed.\n")
        if n == 1:
            chk = subprocess.run([sys.executable, "-B", str(ROOT / "checks" / "level_comparison_check.py"), "3", str(after), str(before)], capture_output=True, text=True)
        else:
            chk = subprocess.run([sys.executable, "-B", str(ROOT / "checks" / "check.py"), str(n), str(after), str(run)], capture_output=True, text=True)
        (run / "correctness-check.txt").write_text(chk.stdout + chk.stderr)
        end = next((e["data"] for e in mine if e["type"] in ("turn.finished", "turn.aborted")), {})
        final = [e["data"]["text"] for e in mine if e["type"] == "assistant.message" and (e["data"].get("text") or "").strip()]
        (run / "final-answer.md").write_text(final[-1] if final else "(no final text)")
        hop, first = 0, None
        for e in mine:                                                # tokens of step 1: the fixed text plus the user's prompt
            if e["type"] == "hop.started": hop = e["data"]["hop"]
            if e["type"] == "status" and hop == 1 and e["data"].get("turn_tokens"): first = e["data"]["turn_tokens"]; break
        names = [e["data"]["name"] for e in mine if e["type"] == "tool.started"]
        rows.append({"engine": engine, "run": name, "case": case, "model": end.get("model"), "steps": end.get("hops"),
                     "tokens": end.get("tokens"), "cost": end.get("cost"), "seconds": end.get("elapsed"),
                     "step_1_tokens": first, "made_a_plan": any(e["type"] == "plan.updated" for e in mine),
                     "tool_calls": len(names), "check": chk.stdout.strip().splitlines()[-1] if chk.stdout.strip() else "no output"})
(ROOT / "numbers.json").write_text(json.dumps(rows, indent=1))
for r in rows:
    print(json.dumps(r))
