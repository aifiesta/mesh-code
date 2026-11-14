"""Phase 3 benchmark: 8 tasks, one setup (A=old, B=new). Real API calls.
Writes nothing to ~/.mesh-code. Usage: bench.py A|B OUTDIR [task numbers...]"""
import json, shutil, subprocess, sys, threading, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import fixture

setup, out = sys.argv[1], Path(sys.argv[2])
only = {int(x) for x in sys.argv[3:]}
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
    return subprocess.run(cmd, cwd=ws, capture_output=True, text=True, timeout=120)


def tests_pass(ws):
    return sh(ws, PY, "-m", "pytest", "-q", "tests").returncode == 0


def replace(ws, rel, old, new):
    p = ws / rel
    assert old in p.read_text(), (rel, old)
    p.write_text(p.read_text().replace(old, new))


def break_merge(ws):
    replace(ws, "inventory/store.py", ".qty += item.qty", ".qty = item.qty")


def unguard_avg(ws):
    replace(ws, "inventory/report.py", "    if shown:\n        lines.append(f\"avg", "    if True:\n        lines.append(f\"avg")


def snapshot(ws, prefix):
    return {p: p.read_text() for p in ws.rglob("*.py") if str(p.relative_to(ws)).startswith(prefix)}


def report_out(ws, *extra):
    return sh(ws, PY, "-m", "inventory.cli", "stock.csv", *extra).stdout


ORIGINAL_REPORT = None

TASKS = {
    1: ("Explain what inventory/store.py does and how `python -m inventory.cli stock.csv` "
        "flows through the code. Don't change any files.", None,
        lambda ws, ans, ctx: "from_csv" in ans and "build_report" in ans),
    2: ("Run the test suite and summarise any failures. Don't change any files.", break_merge,
        lambda ws, ans, ctx: "test_add_merges_qty" in ans and snapshot(ws, "") == ctx),
    3: ("A test is failing. Fix the code, not the tests.", break_merge,
        lambda ws, ans, ctx: tests_pass(ws) and snapshot(ws, "tests") == ctx),
    4: ("Add a function parse_quantity(s) in a new file inventory/validate.py: it takes a string, "
        "returns it as a positive int, and raises ValueError for anything else (empty, non-numeric, "
        "zero, negative). Add unit tests in tests/test_validate.py and run them.", None,
        lambda ws, ans, ctx: (ws / "tests/test_validate.py").exists() and tests_pass(ws) and sh(
            ws, PY, "-c", "from inventory.validate import parse_quantity as p\n"
            "assert p('7')==7\nfor b in ['', 'x', '0', '-3', '2.5']:\n"
            "  try: p(b)\n  except ValueError: pass\n  else: raise SystemExit(b)").returncode == 0),
    5: ("Rename the function total_value to inventory_value everywhere it is defined or used, "
        "then run the tests.", None,
        lambda ws, ans, ctx: tests_pass(ws) and not sh(ws, "grep", "-rn", "total_value", "--include=*.py", ".").stdout
        and "def inventory_value" in (ws / "inventory/models.py").read_text()),
    6: ("Running the report on an empty stock file crashes:\n\n```\nTraceback (most recent call last):\n"
        "  File \"inventory/cli.py\", line 13, in main\n    print(build_report(store))\n"
        "  File \"inventory/report.py\", line 31, in build_report\n"
        "    lines.append(f\"avg price: {average_price(shown):.2f}\")\n"
        "  File \"inventory/report.py\", line 6, in average_price\n"
        "    return sum(i.price for i in items) / len(items)\nZeroDivisionError: division by zero\n```\n\n"
        "Find and fix the bug.", unguard_avg,
        lambda ws, ans, ctx: tests_pass(ws) and sh(
            ws, PY, "-c", "from inventory.report import build_report\nfrom inventory.store import Store\n"
            "build_report(Store())").returncode == 0),
    7: ("Add a --min-qty option to the CLI that hides items with a quantity below it (default 0, "
        "so output is unchanged without it). Wire it through to the report and document it in README.md.", None,
        lambda ws, ans, ctx: "--min-qty" in (ws / "README.md").read_text() and tests_pass(ws)
        and "B2" not in report_out(ws, "--min-qty", "5") and "A1" in report_out(ws, "--min-qty", "5")
        and report_out(ws) == ctx),
    8: ("Refactor build_report in inventory/report.py into smaller functions without changing its "
        "output, then run the tests.", None,
        lambda ws, ans, ctx: tests_pass(ws) and report_out(ws) == ctx
        and (ws / "inventory/report.py").read_text().count("\ndef ") >= 4),
}


def run(n):
    prompt, prep, check = TASKS[n]
    ws = out / f"ws{n}"
    shutil.rmtree(ws, ignore_errors=True)
    fixture.write(ws)
    if prep:
        prep(ws)
    ctx = {2: lambda: snapshot(ws, ""), 3: lambda: snapshot(ws, "tests"),
           7: lambda: report_out(ws), 8: lambda: report_out(ws)}.get(n, lambda: None)()

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
        elif t in ("turn.finished", "turn.aborted"):
            rec.update(status=t, cost=d.get("cost"), tokens=d.get("tokens"), hops=d.get("hops"))
            if d.get("model") and d["model"] not in rec["models"]:
                rec["models"].append(d["model"])
            done.set()
        elif t == "error":
            rec.setdefault("errors", []).append(str(d)[:300])
    cfg = dict(base_cfg)
    sess = S.Session(f"bench{setup}{n}", ws, cfg, emit, mode=S.Mode.AUTO)
    sess.catalog = catalog
    sess.start()
    t0 = time.time()
    sess.submit(prompt)
    if not done.wait(420):
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
