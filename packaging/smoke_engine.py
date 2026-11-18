"""Boot a frozen engine binary and prove a client can talk to it.

Usage: python packaging/smoke_engine.py <path-to-mesh-code-engine>

Passes when the binary prints its {ready, port, token} line and the
WebSocket answers with engine.ready. Needs `websockets` on sys.path, which
the engine's own dependencies provide in any venv the engine is installed in.
"""
import asyncio
import json
import os
import subprocess
import sys
import threading
import time

READY_TIMEOUT_S = 60   # a freshly frozen binary unpacks and gets a Gatekeeper scan on first launch
SOCKET_TIMEOUT_S = 15


def main(argv: list) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    exe = argv[1]
    if not os.access(exe, os.X_OK):
        print(f"error: {exe} is missing or not executable", file=sys.stderr)
        return 1

    proc = subprocess.Popen(
        [exe, "--port", "0"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        encoding="utf-8", errors="replace",
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    try:
        ready = _read_ready(proc)
        if not ready:
            return _fail(proc, "the frozen engine did not report ready")
        print(f"    engine ready on port {ready['port']}")
        try:
            ok = asyncio.run(_socket_says_ready(ready["port"], ready["token"]))
        except Exception as e:  # refused, rejected handshake, timeout: the engine log says why
            return _fail(proc, f"socket: {e!r}")
        if not ok:
            return _fail(proc, "the socket did not answer with engine.ready")
        print("    socket OK")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def _read_ready(proc) -> dict:
    found: dict = {}

    def pump():
        for line in proc.stdout:
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("ready"):
                found.update(msg)
                return

    t = threading.Thread(target=pump, daemon=True)
    t.start()
    deadline = time.time() + READY_TIMEOUT_S
    while time.time() < deadline and not found:
        if proc.poll() is not None:
            break
        time.sleep(0.2)
    return found


async def _socket_says_ready(port: int, token: str) -> bool:
    import websockets

    url = f"ws://127.0.0.1:{port}/ws?token={token}"
    async with websockets.connect(url, open_timeout=SOCKET_TIMEOUT_S) as ws:
        raw = await asyncio.wait_for(ws.recv(), SOCKET_TIMEOUT_S)
    return json.loads(raw).get("type") == "engine.ready"


def _fail(proc, why: str) -> int:
    print(f"error: {why}", file=sys.stderr)
    proc.terminate()
    try:
        _, err = proc.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        _, err = proc.communicate()
    if err:
        print(err[-4000:], file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
