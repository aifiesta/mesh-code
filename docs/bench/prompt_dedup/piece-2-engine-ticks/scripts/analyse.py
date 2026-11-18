"""Piece 2: per run, what happened with the plan, what it cost, and whether
the work is right. Writes the per-run files and prints a table.

    python3 analyse.py

Reads each run's events.jsonl (everything the engine told the app window),
the chat the app saved under ~/.mesh-code/sessions (to see which tool calls
carried a step number), and the project journal. Sends nothing to any model."""
import hashlib, json, pathlib, statistics as st, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TOP = ROOT.parent
HOME = pathlib.Path.home() / ".mesh-code"
WORK = ("read_file", "write_file", "edit_file", "run_bash", "start_server", "web_search")
CHECK = {"server": lambda proj, run: [sys.executable, "-B", str(TOP / "checks/check.py"), "2", str(proj), str(run)],
         "standard": lambda proj, run: [sys.executable, "-B", str(TOP / "checks/level_comparison_check.py"), "3", str(proj), str(ROOT / "0-projects-before/standard")],
         "complex": lambda proj, run: [sys.executable, "-B", str(TOP / "checks/level_comparison_check.py"), "4", str(proj), str(ROOT / "0-projects-before/complex")]}


def journal_for(project):
    ws = str(pathlib.Path(project).expanduser().resolve())
    digest = hashlib.sha256(ws.encode("utf-8", "replace")).hexdigest()[:16]
    hits = list((HOME / "journal").glob(f"*-{digest}.md"))
    return hits[0].read_text() if hits else ""


def fence(text):
    text = (text or "").rstrip()
    mark = "````" if "```" in text else "```"
    return f"{mark}\n{text}\n{mark}\n"


def conversation(name, events):
    out = [f"# Full conversation: {name}\n",
           "Taken from everything the engine reported to the app window during the run. "
           "The same chat is in Mesh Code under the project of this name. A step number the "
           "model put on a tool call is taken off by the engine before the call is shown, so "
           "it does not appear here; the plan lines show its effect.\n"]
    for e in events:
        t, d = e["type"], e.get("data") or {}
        if t == "turn.started":
            out.append("## You\n\n" + d.get("text", "") + "\n")
        elif t == "routed":
            how = "stepped up to" if d.get("escalated") else "settled back to" if d.get("settled") else "picked"
            out.append(f"> **Router {how}:** {d.get('model')} at {d.get('thinking')} thinking. Task level: {d.get('task')}"
                       + (f". Reason: {d['reason']}" if d.get("reason") else "") + "\n")
        elif t == "hop.started":
            out.append(f"*— step {d.get('hop')} —*\n")
        elif t == "assistant.message" and (d.get("text") or "").strip():
            out.append("## Model\n\n" + d["text"].strip() + "\n")
        elif t == "tool.started":
            args = d.get("args") or {}
            out.append(f"**Tool call:** `{d.get('name')}`\n\n" + fence(args.get("command") or json.dumps(args, indent=1)))
        elif t == "tool.skipped":
            out.append(f"> **Call not run ({d.get('kind')}):** `{d.get('name')}` — {d.get('reason')}\n")
        elif t == "tool.result":
            out.append(("Result:" if d.get("ok") else "Result (failed):") + "\n\n" + fence(str(d.get("result"))[:6000]))
        elif t == "plan.updated":
            mark = {"completed": "x", "in_progress": ">"}
            out.append("> **Plan:** " + "; ".join(f"[{mark.get(s.get('status'), ' ')}] {s.get('title')}" for s in d.get("steps") or []) + "\n")
        elif t in ("notice", "error"):
            out.append(f"> **{t.title()}:** {d.get('message')}\n")
        elif t in ("turn.finished", "turn.aborted"):
            out.append(f"---\n\n**{'Finished' if t == 'turn.finished' else 'Stopped'}:** {d.get('hops')} steps, "
                       f"{d.get('tokens'):,} tokens, ${d.get('cost'):.6f} billed, {d.get('elapsed')} s, last model {d.get('model')}\n")
    return "\n".join(out)


def one(side, run):
    name = run.name
    case = name.split("-")[1]
    ev = [json.loads(l) for l in (run / "events.jsonl").read_text().splitlines() if l.strip()]
    i = next((k for k, e in enumerate(ev) if e["type"] == "turn.started"), None)
    if i is None:
        return None
    sid = ev[i]["session"]
    ev = [e for e in ev[i:] if e.get("session") == sid]
    end = next((e["data"] for e in ev if e["type"] in ("turn.finished", "turn.aborted")), None)
    if end is None:
        return None
    # step by step
    key, last, lastc, steps, lines = 0, 0, 0.0, {}, []
    for e in ev:
        t, d = e["type"], e.get("data") or {}
        if t == "hop.started":
            key = d["hop"]; steps[key] = {"tools": [], "plan": [], "tok": 0, "cost": 0.0}
        elif t == "tool.started":
            a = d.get("args") or {}
            steps[key]["tools"].append(d["name"] + (f"({(a.get('command') or a.get('path') or '')[:50]})" if d["name"] in WORK else ""))
        elif t == "plan.updated":
            steps[key]["plan"].append(f"{d.get('done')}/{d.get('total')}")
        elif t == "status" and key and (d.get("turn_tokens") or 0) > last:
            steps[key]["tok"] += d["turn_tokens"] - last; last = d["turn_tokens"]
            steps[key]["cost"] += (d.get("turn_cost") or 0) - lastc; lastc = d.get("turn_cost") or 0
    created, create_only, tick_only = False, [], []
    answer_step = max(steps) if end.get("stubs") is not None or "plan" in end else None      # the step that gave the final answer
    for k in sorted(steps):
        s = steps[k]
        # A step that only touches the plan is a wasted round trip, except the answer step itself:
        # there the new engine ticks the last item while the model is already answering.
        if s["plan"] and not s["tools"] and k != answer_step:
            (tick_only if created else create_only).append(s)
        if s["plan"]:
            created = True
        lines.append(f"step {k:2d}  {s['tok']:6d} tok  ${s['cost']:.6f}  " + "; ".join(s["tools"] + [f"plan {p}" for p in s["plan"]]))
    planned = created
    # which calls carried a step number: only the saved chat has the model's own arguments
    with_step = without = ticks = nudges = 0
    chat = HOME / "sessions" / f"{sid}.json"
    if chat.exists():
        msgs = json.loads(chat.read_text()).get("messages") or []
        seen_plan = False
        for m in msgs:
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function") or {}
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except ValueError:
                    args = {}
                if fn.get("name") == "create_plan":
                    seen_plan = True
                elif fn.get("name") == "update_step":
                    ticks += 1
                elif fn.get("name") in WORK and seen_plan:
                    if isinstance(args, dict) and args.get("step") is not None:
                        with_step += 1
                    else:
                        without += 1
            if m.get("role") == "tool" and "[plan] You are on step" in str(m.get("content")):
                nudges += 1
    project = ROOT / side / "projects" / name
    chk = subprocess.run(CHECK[case](project, run), capture_output=True, text=True)
    verdict = chk.stdout.strip().splitlines()[-1] if chk.stdout.strip() else "no output"
    (run / "correctness-check.txt").write_text(chk.stdout + chk.stderr)
    (run / "step-by-step.txt").write_text("\n".join(lines) + f"\ntotal {end.get('hops')} steps {end.get('tokens')} tokens ${end.get('cost')} {end.get('elapsed')} s\n")
    (run / "conversation.md").write_text(conversation(name, ev))
    final = [e["data"]["text"] for e in ev if e["type"] == "assistant.message" and (e["data"].get("text") or "").strip()]
    (run / "final-answer.md").write_text(final[-1] if final else "(no final text)")
    before = ROOT / "0-projects-before" / case
    diff = subprocess.run(["diff", "-ruN", "-x", "__pycache__", "-x", ".pytest_cache", str(before), str(project)], capture_output=True, text=True).stdout
    (run / "what-the-model-changed.diff").write_text(diff.replace(str(ROOT) + "/", "") or "No file was changed.\n")
    plan = end.get("plan") or {}
    jr = journal_for(project)
    built = [l for l in jr.splitlines() if l.startswith("- [")]
    return dict(side=side, case=case, run=name, model=(end.get("model") or "").split("/")[-1], steps=end.get("hops"), tokens=end.get("tokens"),
                cost=end.get("cost"), seconds=end.get("elapsed"), planned=planned,
                create_only_steps=len(create_only), tick_only_steps=len(tick_only),
                bookkeeping_tokens=sum(s["tok"] for s in create_only + tick_only), bookkeeping_cost=round(sum(s["cost"] for s in create_only + tick_only), 6),
                tick_only_tokens=sum(s["tok"] for s in tick_only), tick_only_cost=round(sum(s["cost"] for s in tick_only), 6),
                checklist=f"{plan.get('done')}/{plan.get('total')}" if plan else "-", checklist_complete=bool(plan.get("complete")) if plan else None,
                calls_with_step=with_step, calls_without_step=without, tick_calls=ticks, reminders=nudges,
                journal_built=f"{sum(1 for l in built if l.startswith('- [x]'))}/{len(built)}" if built else "-",
                check=verdict.replace("VERDICT ", ""), aborted=end.get("stubs") is None and "sealed" in end)


rows = []
for side in ("old", "new", "new2"):
    d = ROOT / side / "runs"
    for run in sorted(d.iterdir()) if d.exists() else []:
        if (run / "events.jsonl").exists():
            r = one(side, run)
            if r:
                rows.append(r)
(ROOT / "numbers.json").write_text(json.dumps(rows, indent=1))
for r in rows:
    print(f"{r['run']:22s} {r['model'][:22]:22s} {r['steps']:2d} steps {r['tokens']:7d} tok ${r['cost']:.6f} | plan {'yes' if r['planned'] else 'no '} "
          f"create-only {r['create_only_steps']} tick-only {r['tick_only_steps']} ({r['tick_only_tokens']} tok ${r['tick_only_cost']:.4f}) | checklist {r['checklist']:5s} "
          f"| step on {r['calls_with_step']}/{r['calls_with_step'] + r['calls_without_step']} calls, reminders {r['reminders']}, tick calls {r['tick_calls']} | journal {r['journal_built']} | {r['check']}")
print()
for case in ("server", "standard", "complex"):
    for side in ("old", "new", "new2"):
        rs = [r for r in rows if r["case"] == case and r["side"] == side]
        if not rs:
            continue
        pl = [r for r in rs if r["planned"]]
        line = (f"{case:9s} {side}: n={len(rs)} planned {len(pl)}/{len(rs)} | all runs avg {st.mean(r['steps'] for r in rs):.1f} steps {st.mean(r['tokens'] for r in rs):.0f} tok ${st.mean(r['cost'] for r in rs):.6f}")
        if pl:
            line += (f" | planned runs avg {st.mean(r['steps'] for r in pl):.1f} steps {st.mean(r['tokens'] for r in pl):.0f} tok ${st.mean(r['cost'] for r in pl):.6f}, "
                     f"tick-only {st.mean(r['tick_only_steps'] for r in pl):.1f} steps, checklist complete {sum(1 for r in pl if r['checklist_complete'])}/{len(pl)}")
        print(line)
print("billed in total:", round(sum(r["cost"] or 0 for r in rows), 6))
