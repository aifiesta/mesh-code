"""Benchmark on a bigger repo: a copy of the Mesh Code engine (~8.6k lines, 73 tests).
Real API calls; writes nothing to ~/.mesh-code. Usage: big.py A|B CLEAN_COPY OUTDIR [tasks...]"""
import json, re, shutil, subprocess, sys, threading, time
from pathlib import Path

setup, clean, out = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
only = {int(x) for x in sys.argv[4:]}
out.mkdir(parents=True, exist_ok=True)
PY = sys.executable

from meshharness.core import config as C, journal as J
C.save_config = lambda cfg: None
base_cfg = C.load_config()
for name in ("SESSIONS_DIR", "TRANSCRIPTS_DIR"):
    setattr(C, name, out / name.lower())
C.USAGE_FILE = out / "usage.json"
J.JOURNAL_DIR = out / "journal"
SETUPS = {
    "A": dict(route_mode="off", optimize=0.0, model="anthropic/claude-sonnet-4.5"),
    "B": dict(route_mode="smart", optimize=0.1),
}
base_cfg.update(repo_memory=False, monthly_budget=0, **SETUPS[setup])

import httpx
import meshharness.session as S
from meshharness.protocol import Decision
catalog = httpx.get(base_cfg["base_url"].rstrip("/") + "/models",
                    headers={"Authorization": f"Bearer {base_cfg['api_key']}"}, timeout=60).json()


def sh(ws, *cmd):
    return subprocess.run(cmd, cwd=ws, capture_output=True, text=True, timeout=300)


def tests_pass(ws):
    return sh(ws, PY, "-m", "pytest", "-q", "-p", "no:warnings").returncode == 0


def test_count(ws):
    out = sh(ws, PY, "-m", "pytest", "--co", "-q", "-o", "addopts=").stdout
    return sum("::" in line for line in out.splitlines())


def replace(ws, rel, old, new):
    p = ws / rel
    assert old in p.read_text(), (rel, old)
    p.write_text(p.read_text().replace(old, new))


def month_bug(ws):
    replace(ws, "meshharness/core/config.py",
            '    month = usage.get("month") if isinstance(usage, dict) else None',
            '    month = usage["month"]')


def classify_bug(ws):
    replace(ws, "meshharness/core/optimize.py",
            '        sample += (content if isinstance(content, str) else json.dumps(content)) + "\\n"',
            '        sample += content + "\\n"')


def snapshot(ws, sub):
    return {str(p): p.read_text() for p in (ws / sub).rglob("*.py")}


PREPARE_PROBE = """
import json
from meshharness.core.optimize import prepare
cases = [
  ([{"role":"system","content":"s"*9000},{"role":"user","content":"hi"}], "anthropic/claude-sonnet-4.5", 0.1, True, 0),
  ([{"role":"system","content":"s"},{"role":"user","content":"refactor this"},{"role":"assistant","content":None,"tool_calls":[]},
    {"role":"tool","content":"t"*3000},{"role":"user","content":"x"*9000},{"role":"assistant","content":"ok"},{"role":"user","content":"go"}],
   "openai/gpt-5-mini", 0.3, True, 1700),
  ([{"role":"user","content":"hello"}], "x/y", 0.1, False, 0),
  ([{"role":"user","content":"hello"}], "x/y", 0, False, 0),
]
print(json.dumps([prepare(m, mo, d, t, tools_tokens=tt) for m, mo, d, t, tt in cases], sort_keys=True))
"""


def prepare_out(ws):
    r = sh(ws, PY, "-c", PREPARE_PROBE)
    return r.stdout if r.returncode == 0 else "ERR " + r.stderr[-300:]


TRACEBACK = """```
Traceback (most recent call last):
  File "meshharness/session.py", line 870, in _stream_hop
    for ev in stream_chat(self.state["messages"], self.cfg, tools=tools, models=self.catalog):
  File "meshharness/core/client.py", line 209, in stream_chat
    messages, extra, plan = prepare(
  File "meshharness/core/optimize.py", line 138, in prepare
    plan["classification"] = _classify(out, has_tools)
  File "meshharness/core/optimize.py", line 104, in _classify
    sample += content + "\\n"
TypeError: unsupported operand type(s) for +: 'NoneType' and 'str'
```"""

TASKS = {
    1: ("Explain how a user prompt travels from the websocket in meshharness/server.py to the model "
        "call and back to the UI. Name the key functions. Don't change any files.", None, None,
        lambda ws, ans, ctx: "submit" in ans and "stream_chat" in ans),
    2: ("Run the test suite (`python -m pytest`) and summarise the failures and their likely cause. "
        "Don't change any files.", month_bug, lambda ws: snapshot(ws, "."),
        lambda ws, ans, ctx: "KeyError" in ans and "month" in ans and snapshot(ws, ".") == ctx),
    3: ("Tests are failing (`python -m pytest`). Fix the code, not the tests.", month_bug,
        lambda ws: snapshot(ws, "tests"),
        lambda ws, ans, ctx: tests_pass(ws) and snapshot(ws, "tests") == ctx),
    4: ("Add a setting `read_limit_chars` (integer, default 24000, min 1000, max 200000) to the settings "
        "schema in meshharness/settings.py, make the read_file tool use it instead of the fixed READ_LIMIT, "
        "and add a test for it. Run the tests.", None, test_count,
        lambda ws, ans, ctx: tests_pass(ws) and test_count(ws) > ctx
        and "read_limit_chars" in (ws / "meshharness/settings.py").read_text()
        and "read_limit_chars" in (ws / "meshharness/core/tools.py").read_text()),
    5: ("Rename the helper `_est_tokens` in meshharness/core/optimize.py to `estimate_tokens` (it is "
        "public now) and update every caller. Run the tests.", None, None,
        lambda ws, ans, ctx: tests_pass(ws)
        and not sh(ws, "grep", "-rnw", "_est_tokens", "--include=*.py", ".").stdout
        and "def estimate_tokens" in (ws / "meshharness/core/optimize.py").read_text()),
    6: ("A turn crashed right after a tool call with this traceback:\n\n" + TRACEBACK
        + "\n\nFind and fix the bug.", classify_bug, None,
        lambda ws, ans, ctx: tests_pass(ws) and not prepare_out(ws).startswith("ERR")),
    7: ("Refactor `prepare` in meshharness/core/optimize.py into smaller functions, one per lever, "
        "without changing its behaviour. Run the tests.", None, prepare_out,
        lambda ws, ans, ctx: tests_pass(ws) and prepare_out(ws) == ctx
        and (ws / "meshharness/core/optimize.py").read_text().count("\ndef ") >= 11),  # 9 defs before
}


def run(n):
    prompt, prep, before, check = TASKS[n]
    ws = out / f"ws{n}"
    shutil.rmtree(ws, ignore_errors=True)
    shutil.copytree(clean, ws)
    if prep:
        prep(ws)
    ctx = before(ws) if before else None

    done, rec = threading.Event(), {"task": n, "models": [], "answer": ""}
    def emit(ev):
        t, d = ev.get("type"), ev.get("data") or {}
        if t == "routed" and d.get("model") not in rec["models"]:
            rec["models"].append(d.get("model"))
        elif t == "assistant.message":
            rec["answer"] += (d.get("text") or "") + "\n"
        elif t == "tool.proposed":
            threading.Timer(0.1, sess.respond_approval, (d["token"], Decision.ALLOW)).start()
        elif t == "ask.user":
            threading.Timer(0.1, sess.respond_ask, (d["token"], None)).start()
        elif t == "compacted":
            rec["compactions"] = rec.get("compactions", 0) + 1
        elif t in ("turn.finished", "turn.aborted"):
            rec.update(status=t, cost=d.get("cost"), tokens=d.get("tokens"), hops=d.get("hops"))
            done.set()
        elif t == "error":
            rec.setdefault("errors", []).append(str(d)[:300])
    sess = S.Session(f"big{setup}{n}", ws, dict(base_cfg), emit, mode=S.Mode.AUTO)
    sess.catalog = catalog
    sess.start()
    t0 = time.time()
    sess.submit(prompt)
    if not done.wait(900):
        rec["status"] = "timeout"
    rec["elapsed"] = round(time.time() - t0, 1)
    try:
        rec["ok"] = bool(check(ws, rec["answer"], ctx))
    except Exception as e:
        rec["ok"], rec["check_error"] = False, repr(e)
    sess.close()
    print(json.dumps({k: rec[k] for k in rec if k != "answer"}), flush=True)
    return rec


results = [run(n) for n in sorted(TASKS) if not only or n in only]
(out / f"results_{setup}.json").write_text(json.dumps(results, indent=1))
