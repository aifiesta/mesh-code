#!/usr/bin/env python3
"""Release gate for a local tier: six agent prompts against the real tools.

  engine/.venv/bin/python engine/scripts/local_smoke.py [--model local/<id>] [--install]

Uses the real ~/.mesh-code/local layer (installed models, bundled or
downloaded runtime). Prints, per prompt, whether the tool call arrived
structured or had to be parsed from text, time to first token, tokens/s,
and the server's resident memory. Exits 1 when a tool prompt produced no
call at all. The manifest can flip a tier default on this evidence alone.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import meshharness  # noqa: E402
from meshharness.core import client, config, textcalls  # noqa: E402
from meshharness.core.tools import TOOLS  # noqa: E402
from meshharness.local import prompt as local_prompt  # noqa: E402
from meshharness.local.manager import LocalModels, LocalUnavailable  # noqa: E402
from meshharness.local.server import _cmdline  # noqa: E402
from meshharness.local.provider import AUTO_ID  # noqa: E402

PROMPTS = [
    ("read_file", "Read the file README.md and tell me its first heading."),
    ("write_file", "Create hello.py containing a script that prints 'hi'."),
    ("run_bash", "Run `echo smoke-ok` and report the output."),
    ("read_file", "Open pyproject.toml and list the project's dependencies."),
    ("create_plan", "Plan how you would add a /health endpoint to a FastAPI app in this repo, as steps."),
    (None, "In one sentence, what does a sha256 checksum protect against?"),
]
NAMES = {t["function"]["name"] for t in TOOLS}
SCHEMAS = {t["function"]["name"]: (t["function"].get("parameters") or {}).get("properties", {}) for t in TOOLS}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default=AUTO_ID)
    ap.add_argument("--install", action="store_true", help="download the model when it is missing")
    ap.add_argument("--cwd", default=".", help="workspace the prompts refer to")
    ap.add_argument("--force", action="store_true", help="run even if the app's llama-server is live")
    args = ap.parse_args()

    # Booting a second LocalModels kills the pid the app's server owns and
    # rewrites its state.json: refuse unless asked.
    if not args.force and _live_server(config.LOCAL_DIR / "llama-server.pid"):
        print("a llama-server from the running app is live; quit Mesh Code or pass --force"); return 1

    cfg = dict(config.DEFAULT_CONFIG)
    cfg.update(local_auto_setup=False, local_enabled=True)
    mgr = LocalModels(cfg, local_dir=config.LOCAL_DIR, app_version=meshharness.__version__,
                      emit=_progress, on_catalog_change=lambda: None)
    mgr.boot()
    try:
        return _run(mgr, cfg, model_arg=args.model, install=args.install, cwd=args.cwd)
    finally:
        mgr.shutdown()


def _run(mgr: LocalModels, cfg: dict, *, model_arg: str, install: bool, cwd: str) -> int:
    mgr.booted.wait(60)
    status = mgr.status()
    print(f"machine: {status['hardware']}\nrecommended: {status['recommended']}")

    model = mgr.default_id() if model_arg == AUTO_ID else model_arg
    if not model:
        print("no local model fits this machine"); return 1
    if model not in mgr.installed_ids():
        if not install:
            print(f"{model} is not installed; pass --install to download it"); return 1
        mgr.install(model)
        while model not in mgr.installed_ids():
            time.sleep(1)
            if not any(j["target"] == model for j in mgr.status()["jobs"]):
                time.sleep(3)   # the job leaves the queue a moment before the install is recorded
                if model not in mgr.installed_ids():
                    print("download ended without an installed model"); return 1

    try:
        target = mgr.target_for(model, notify=print)
    except LocalUnavailable as e:
        print(f"unavailable: {e}"); return 1
    print(f"serving {target.display_id} as {target.model} at {target.base_url} (ctx {target.context_limit})")

    system = local_prompt.build_compact_system_prompt(cfg, cwd=str(Path(cwd).resolve()))
    failures = 0
    for expected, text in PROMPTS:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": text}]
        started, first, reply, meta = time.monotonic(), None, "", {}
        for chunk in client.stream_chat(messages, cfg, TOOLS, target=target):
            if isinstance(chunk, dict):
                meta = chunk
            else:
                first = first or time.monotonic()
                reply += chunk
        elapsed = time.monotonic() - started
        calls = meta.get("tool_calls") or []
        how = "structured" if calls else ""
        if not calls and reply:
            _, parsed, _ = textcalls.extract(reply, NAMES, schemas=SCHEMAS)
            if parsed:
                calls, how = parsed, "text-parsed"
        names = [c.get("name") or (c.get("function") or {}).get("name") for c in calls]
        out = (meta.get("usage") or {}).get("completion_tokens") or 0
        tps = f"{out / max(elapsed, 1e-6):.1f} tok/s" if out else "n/a"
        ttft = f"{(first or time.monotonic()) - started:.2f}s"
        verdict = "ok"
        if expected and not calls:
            verdict, failures = "NO CALL", failures + 1
        elif expected and expected not in names:
            verdict = f"called {names}"
        elif not expected and calls:
            verdict = f"unexpected call {names}"
        print(f"[{verdict:>16}] {how or '-':12} ttft {ttft:>6}  {tps:>10}  {text[:48]!r}")
        if meta.get("error"):
            print(f"      error: {meta['error']}")

    pid = mgr.status()["server"].get("pid")
    if pid:
        rss = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
        if rss:
            print(f"llama-server rss: {int(rss) // 1024} MB")
    print("PASS" if failures == 0 else f"FAIL: {failures} tool prompt(s) produced no call")
    return 1 if failures else 0


def _live_server(pid_file: Path) -> bool:
    # A cmdline probe, never a signal: os.kill(pid, 0) terminates on Windows,
    # and a recycled pid must not read as the app's server.
    try:
        return "llama-server" in _cmdline(int(pid_file.read_text().strip()))
    except (OSError, ValueError):
        return False


def _progress(event: str, data: dict) -> None:
    if event == "local.progress" and data.get("total"):
        pct = 100 * data["bytes"] // data["total"]
        print(f"\r  {data['target']} {data['phase']} {pct}%", end="", flush=True)
        if data["phase"] in ("done", "error", "cancelled"):
            print()


if __name__ == "__main__":
    sys.exit(main())
