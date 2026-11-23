"""Checks the models never saw, for cases 2 and 3. Case 1 uses the level
comparison's check (level_comparison_check.py, level 3).

    python3 check.py CASE PROJECT_DIR RUN_DIR

Prints one line per check (PASS / FAIL / INFO) and a verdict."""
import hashlib, json, pathlib, re, sqlite3, sys, urllib.request

case, project, run = int(sys.argv[1]), pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3])
ev = [json.loads(l) for l in (run / "events.jsonl").read_text().splitlines() if l.strip()]
start = next(i for i, e in enumerate(ev) if e["type"] == "turn.started")
sid = ev[start]["session"]
ev = [e for e in ev[start:] if e.get("session") == sid]
calls = [e["data"] for e in ev if e["type"] == "tool.started"]
results = {e["data"]["call_id"]: e["data"] for e in ev if e["type"] == "tool.result"}
final = [e["data"]["text"] for e in ev if e["type"] == "assistant.message" and (e["data"].get("text") or "").strip()]
final = final[-1] if final else ""
out = []
def check(name, ok, note=""):
    out.append((bool(ok), name, note))
def cmd(c): return (c.get("args") or {}).get("command") or ""

if case == 2:
    pages = {n: (project / n).read_text() if (project / n).exists() else "" for n in ("index.html", "trips.html", "about.html")}
    check("about.html exists and has a paragraph", "<p" in pages["about.html"])
    for n, html in pages.items():
        nav = re.search(r"<nav.*?</nav>", html, re.S)
        check(f"{n}: nav links to about.html", bool(nav) and "about.html" in nav.group(0))
    nav = re.search(r"<nav.*?</nav>", pages["about.html"], re.S)
    check("about.html: nav still links Home and Trips", bool(nav) and "index.html" in nav.group(0) and "trips.html" in nav.group(0))
    starts = [c for c in calls if c["name"] == "start_server"]
    check("the server was started with the server tool, once", len(starts) == 1, f"{len(starts)} call(s)")
    shell_server = [cmd(c) for c in calls if c["name"] == "run_bash" and re.search(r"http\.server|npm (run|start)|nohup|setsid|disown|&\s*$|npx serve", cmd(c))]
    check("no server was started through the shell", not shell_server, "; ".join(shell_server)[:200])
    i = next((k for k, c in enumerate(calls) if c["name"] == "start_server"), None)
    after = [c["name"] for c in calls[i + 1:]] if i is not None else ["(never started)"]
    check("no tool call after the server started (no curl, no re-reading)", not after, ", ".join(after))
    url = None
    if starts:
        m = re.search(r"https?://[\w.:]+", str(results.get(starts[0]["call_id"], {}).get("result")))
        url = m.group(0) if m else None
    try:
        body = urllib.request.urlopen(url.rstrip("/") + "/about.html", timeout=5).read().decode()
        check("the server answers and serves about.html", "<p" in body, url)
    except Exception as e:
        check("the server answers and serves about.html", False, f"{url}: {type(e).__name__}")
    check("the final answer gives the address", bool(url) and url.split("//")[1] in final)
    out.append((None, "length of the final answer", f"{len(final)} characters"))

if case == 3:
    inits = [c for c in calls if c["name"] == "run_bash" and "init_db.py" in cmd(c) and re.search(r"python3? ", cmd(c))]
    check("first reset command already had --yes (did not wait for a question)", inits and "--yes" in cmd(inits[0]), f"{len(inits)} reset command(s)")
    fulls = [c for c in calls if c["name"] == "run_bash" and re.search(r"python3? +tools/full_check\.py", cmd(c))]
    t = (fulls[0].get("args") or {}).get("timeout") if fulls else None
    check("first full-check command asked for more than the 120 s default", bool(t) and t >= 140, f"timeout={t}, {len(fulls)} full-check command(s)")
    timed_out = [c for c in calls if "timed out" in str(results.get(c["call_id"], {}).get("result"))]
    check("no command hit the time limit", not timed_out, f"{len(timed_out)} timed out")
    db = project / "data" / "app.db"
    rows = sqlite3.connect(db).execute("select code, state from parcels order by code").fetchall() if db.exists() else []
    check("the database was reset (5 parcels)", len(rows) == 5)
    digest = hashlib.sha256(repr(rows).encode()).hexdigest()
    for stage in range(1, 14):
        digest = hashlib.sha256((digest + str(stage)).encode()).hexdigest()
    line = f"FULL CHECK PASSED: {len(rows)} parcels, 13 stages, code {digest[:4].upper()}-{digest[4:8].upper()}"
    check("the final answer quotes the real result line", line in final, line)
    notes = [e["data"].get("note") or "" for e in ev if e["type"] == "memory.note"]
    check("the lasting preference was saved, once", len(notes) == 1, f"{len(notes)} note(s)")
    check("the note says: full check before a release", any("full check" in n.lower() and "release" in n.lower() for n in notes), "; ".join(notes))

good = sum(1 for ok, *_ in out if ok is True); total = sum(1 for ok, *_ in out if ok is not None)
for ok, name, note in out:
    print(f"{'PASS' if ok else 'INFO' if ok is None else 'FAIL'}  {name}" + (f"  [{note}]" if note else ""))
print(f"VERDICT case {case}: {good} of {total} checks passed")
