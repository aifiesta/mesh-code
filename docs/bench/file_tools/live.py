"""Live check of the file tools: one real model, one task, through whichever
engine is on PYTHONPATH. Makes real, billed API calls.

    cd docs/bench/file_tools
    PYTHONPATH=../../../engine python live.py LABEL MODEL SCENARIO OUTDIR

SCENARIO is one of: find, lock, lockread, edit, fail. To compare against an
older engine, extract it (`git archive <rev> engine/meshharness | tar -x -C
/tmp/old`) and run the same command with PYTHONPATH=/tmp/old/engine.

Only the API key is read from the real config; every file the engine would
write (sessions, usage, journal, transcripts) goes under OUTDIR. `python`
on PATH must have pytest, because the edit and fail tasks run the tests."""
import json, shutil, subprocess, sys, threading, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import fixture

label, model, scenario, out = sys.argv[1], sys.argv[2], sys.argv[3], Path(sys.argv[4])
out.mkdir(parents=True, exist_ok=True)

from meshharness.core import config as C, journal as J
C.save_config = lambda cfg: None
cfg = C.load_config()
for name in ("SESSIONS_DIR", "TRANSCRIPTS_DIR"):
    setattr(C, name, out / name.lower())
C.USAGE_FILE = out / "usage.json"
J.JOURNAL_DIR = out / "journal"
cfg.update(repo_memory=False, route_mode="off", model=model, optimize=0.0,
           auto_route=False, fallback_models=[], max_hops=25, auto_compact=True,
           output_style="default")
cfg.pop("monthly_budget", None)

import httpx
import meshharness.session as S
from meshharness.protocol import Decision

catalog = httpx.get(cfg["base_url"].rstrip("/") + "/models",
                    headers={"Authorization": f"Bearer {cfg['api_key']}"}, timeout=60).json()
catalog = catalog.get("data") if isinstance(catalog, dict) else catalog

SCENARIOS = {
    "find": ("In orders/engine.py, what does compute_late_fee return when days_late is "
             "negative, and what is the maximum fee it can charge? Do not change any files.",
             lambda ws, ans: ("0" in ans) and ("25" in ans or "quarter" in ans.lower())),
    "lock": ("Which exact version of lodash is this project locked to? Do not change any files.",
             lambda ws, ans: "4.17.21" in ans),
    "lockread": ("Open package-lock.json and tell me its lockfileVersion. Do not change any files.",
                 lambda ws, ans: "3" in ans),
    "edit": ("In orders/engine.py, change compute_late_fee so the fee is capped at 20% of the "
             "order total instead of 25%, update the comment above the cap to match, update "
             "tests/test_fee.py so the capped test expects the new value, then run the tests.",
             lambda ws, ans: "0.2" in (ws / "orders/engine.py").read_text().split("def compute_late_fee")[1][:500]
             and subprocess.run([sys.executable, "-m", "pytest", "-q", "tests"], cwd=ws,
                                capture_output=True).returncode == 0
             and (ws / "orders/engine.py").read_text().count("\ndef ") == 180),
    "fail": ("Run the test suite with `python -m pytest -v` and tell me exactly which tests fail "
             "and why, including the values in the failing assertion. Do not change any files.",
             lambda ws, ans: "test_fee_is_capped" in ans and "30" in ans),
}
prompt, check = SCENARIOS[scenario]
ws = out / f"ws-{label}-{scenario}"
shutil.rmtree(ws, ignore_errors=True)
fixture.write(ws)
if scenario == "fail":
    fixture.noisy_tests(ws)

done = threading.Event()
rec = {"label": label, "model": model, "scenario": scenario, "answer": "", "calls": []}
by_id = {}

def emit(ev):
    t, d = ev.get("type"), ev.get("data") or {}
    if t == "assistant.message":
        rec["answer"] += (d.get("text") or "") + "\n"
    elif t == "tool.proposed":
        threading.Timer(0.1, sess.respond_approval, (d["token"], Decision.ALLOW)).start()
    elif t == "ask.user":
        threading.Timer(0.1, sess.respond_ask, (d["token"], None)).start()
    elif t == "tool.started":
        by_id[d.get("call_id")] = {"call": d.get("summary", "")[:150]}
        rec["calls"].append(by_id[d.get("call_id")])
    elif t == "tool.skipped":
        rec["calls"].append({"call": f"{d.get('name')} SKIPPED ({d.get('kind')})"})
    elif t == "tool.result":
        c = by_id.get(d.get("call_id"))
        if c is not None:
            c["ok"] = d.get("ok")
            c["result_chars"] = d.get("bytes") or len(d.get("result") or "")
            if d.get("ok") is False or d.get("name") == "edit_file":
                c["result"] = (d.get("result") or "")[:400]
    elif t in ("turn.finished", "turn.aborted"):
        rec.update(status=t, cost=d.get("cost"), tokens=d.get("tokens"), hops=d.get("hops"))
        done.set()
    elif t == "error":
        rec.setdefault("errors", []).append(str(d)[:300])

sess = S.Session(f"live-{label}-{scenario}", ws, cfg, emit, mode=S.Mode.BYPASS)
sess.catalog = catalog
sess.start()
t0 = time.time()
sess.submit(prompt)
if not done.wait(420):
    rec["status"] = "timeout"
rec["elapsed"] = round(time.time() - t0, 1)

msgs = sess.state["messages"]
rec["output_chars_in_tool_args"] = sum(
    len(tc["function"].get("arguments") or "") for m in msgs for tc in (m.get("tool_calls") or []))
rec["tool_result_chars"] = sum(len(str(m.get("content") or "")) for m in msgs if m.get("role") == "tool")
rec["history_chars_at_end"] = sum(len(json.dumps(m)) for m in msgs)
try:
    rec["ok"] = bool(check(ws, rec["answer"]))
except Exception as e:
    rec["ok"], rec["check_error"] = False, repr(e)
sess.close()

stem = f"{label}-{scenario}-{model.replace('/', '_')}"
(out / f"{stem}.json").write_text(json.dumps(rec, indent=1))
# The whole conversation (minus the system prompt), so a run can be read back
# step by step later without paying for it again.
(out / f"{stem}-chat.json").write_text(json.dumps(
    [m for m in msgs if m.get("role") != "system"], indent=1, default=str))
print(f"\n=== {label} | {scenario} | {model} ===")
print(f"status={rec.get('status')} ok={rec['ok']} hops={rec.get('hops')} tokens={rec.get('tokens')} "
      f"cost=${rec.get('cost')} elapsed={rec['elapsed']}s")
print(f"tool-call argument chars (model output)={rec['output_chars_in_tool_args']}  "
      f"tool-result chars={rec['tool_result_chars']}  history chars at end={rec['history_chars_at_end']}")
for c in rec["calls"]:
    print("  -", c["call"], "| ok" if c.get("ok") else "| FAILED" if c.get("ok") is False else "",
          f"| {c.get('result_chars', '?')} chars back")
    if c.get("result") and (c.get("ok") is False):
        print("      ", c["result"].replace("\n", " ")[:300])
if rec.get("errors"):
    print("errors:", rec["errors"])
print("answer:", rec["answer"].strip().replace("\n", " ")[:400])
